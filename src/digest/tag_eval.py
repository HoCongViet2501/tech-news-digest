"""Tag evaluation set (docs/PLAN.md W2 step 4): export a stratified sample of tagged items
for the owner to label, then score the tagger against the labels.

Labeling file: CSV, one row per item. The owner fills `labels` with every taxonomy slug
that applies (space separated, most specific ones are enough), `-` when none applies.
Rows with an empty `labels` are not labeled yet and are skipped.

Scoring follows how tags are read (a child tag implies its parents, tags.parent):
- a predicted tag is correct when it is a labeled tag or an ancestor of one;
- a labeled tag is found when it is predicted or an ancestor of a predicted tag.
"""

import csv
import random
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import psycopg
from pydantic import BaseModel

from digest.taxonomy import Taxonomy

FIELDS = ("id", "source", "title", "url", "description", "predicted", "labels")
NONE_LABEL = "-"
DESCRIPTION_CHARS = 240


class Predicted(BaseModel):
    tag: str
    method: str
    confidence: float


class SampleRow(BaseModel):
    id: str
    source: str
    title: str
    url: str
    description: str = ""
    predicted: list[Predicted]

    @property
    def stratum(self) -> str:
        """Method of the most confident tag, or 'none'."""
        if not self.predicted:
            return "none"
        return max(self.predicted, key=lambda p: p.confidence).method


_TAGGED = """
select i.id, s.name as source, i.title, i.url, coalesce(i.description, '') as description,
       coalesce(
         json_agg(json_build_object('tag', t.tag, 'method', t.method,
                                    'confidence', t.confidence)
                  order by t.confidence desc, t.tag)
           filter (where t.tag is not null),
         '[]') as predicted
from public.items i
join public.sources s on s.id = i.source_id
left join public.item_tags t on t.item_id = i.id
where i.tagged_at is not null {only}
group by i.id, s.name
"""


def fetch_rows(conn: psycopg.Connection, ids: Sequence[str] | None = None) -> list[SampleRow]:
    """Tagged items with their current tags (all of them, or just `ids`)."""
    only = "and i.id = any(%s::text[])" if ids is not None else ""
    params = (list(ids),) if ids is not None else ()
    with conn.cursor() as cur:
        cur.execute(_TAGGED.format(only=only), params)
        columns = [c.name for c in cur.description or ()]
        rows = [SampleRow.model_validate(dict(zip(columns, r, strict=True))) for r in cur]
    return sorted(rows, key=lambda r: r.id)


def stratified_sample(
    rows: Sequence[SampleRow], n: int, *, seed: int, min_per_stratum: int = 20
) -> list[SampleRow]:
    """`n` rows, proportional to each stratum (method of the top tag, or none) but with
    at least `min_per_stratum` per stratum when it has that many, so per-method numbers
    mean something even for rare methods. Deterministic for a seed."""
    rng = random.Random(seed)
    strata: dict[str, list[SampleRow]] = defaultdict(list)
    for row in rows:
        strata[row.stratum].append(row)
    for members in strata.values():
        rng.shuffle(members)
    n = min(n, len(rows))
    quota = {k: min(len(v), min_per_stratum) for k, v in strata.items()}
    left = n - sum(quota.values())
    if left < 0:  # tiny n: just take the floor from each, largest strata first
        quota = {k: 0 for k in strata}
        for k in sorted(strata, key=lambda k: -len(strata[k])):
            take = min(len(strata[k]), min_per_stratum, n - sum(quota.values()))
            quota[k] = take
    else:
        spare = {k: len(v) - quota[k] for k, v in strata.items()}
        total_spare = sum(spare.values())
        for k in sorted(strata):
            extra = round(left * spare[k] / total_spare) if total_spare else 0
            quota[k] += min(extra, spare[k])
        # Rounding can leave the total off by a few; fix it on the largest strata.
        for k in sorted(strata, key=lambda k: -len(strata[k])):
            diff = n - sum(quota.values())
            if diff == 0:
                break
            quota[k] = max(0, min(len(strata[k]), quota[k] + diff))
    picked = [row for k in sorted(strata) for row in strata[k][: quota[k]]]
    return sorted(picked, key=lambda r: (r.stratum, r.source, r.id))


def format_predicted(predicted: Iterable[Predicted]) -> str:
    return " ".join(f"{p.tag}({p.method} {p.confidence:.2f})" for p in predicted)


def write_sample(path: Path, rows: Sequence[SampleRow]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "id": row.id,
                    "source": row.source,
                    "title": row.title,
                    "url": row.url,
                    "description": " ".join(row.description.split())[:DESCRIPTION_CHARS],
                    "predicted": format_predicted(row.predicted),
                    "labels": "",
                }
            )


class LabelError(Exception):
    """The labeling file is unusable (e.g. unknown slugs). The message says where."""


def read_labels(path: Path, taxonomy: Taxonomy) -> dict[str, set[str]]:
    """Labeled rows: item id -> set of slugs (empty for '-'). Unlabeled rows are skipped."""
    labels: dict[str, set[str]] = {}
    errors = []
    with path.open(encoding="utf-8", newline="") as f:
        for line, row in enumerate(csv.DictReader(f), start=2):
            raw = (row.get("labels") or "").replace(",", " ").split()
            if not raw:
                continue
            slugs = {s.lower() for s in raw if s != NONE_LABEL}
            unknown = sorted(s for s in slugs if s not in taxonomy.by_slug)
            if unknown:
                errors.append(f"line {line}: unknown tag(s) {', '.join(unknown)}")
            labels[row["id"]] = slugs
    if errors:
        raise LabelError("\n".join(errors))
    return labels


@dataclass
class Score:
    predicted: int = 0
    correct: int = 0
    labeled: int = 0
    found: int = 0

    @property
    def precision(self) -> float | None:
        return self.correct / self.predicted if self.predicted else None

    @property
    def recall(self) -> float | None:
        return self.found / self.labeled if self.labeled else None


@dataclass
class Report:
    items: int = 0
    overall: Score = field(default_factory=Score)
    by_method: dict[str, Score] = field(default_factory=dict)


def _with_ancestors(slugs: Iterable[str], taxonomy: Taxonomy) -> set[str]:
    out = set(slugs)
    for slug in list(out):
        out.update(taxonomy.ancestors(slug))
    return out


def evaluate(
    labels: dict[str, set[str]], predictions: dict[str, list[Predicted]], taxonomy: Taxonomy
) -> Report:
    """Micro precision/recall overall and per method. A method's recall is the share of
    all labeled tags that its predictions found (so methods' recalls do not add up)."""
    report = Report()
    for item_id, wanted in labels.items():
        report.items += 1
        got = predictions.get(item_id, [])
        wanted_closure = _with_ancestors(wanted, taxonomy)
        report.overall.labeled += len(wanted)
        all_closure = _with_ancestors((p.tag for p in got), taxonomy)
        report.overall.found += sum(1 for w in wanted if w in all_closure)
        by_method: dict[str, list[str]] = defaultdict(list)
        for p in got:
            by_method[p.method].append(p.tag)
            report.overall.predicted += 1
            report.overall.correct += p.tag in wanted_closure
        for method, tags in by_method.items():
            score = report.by_method.setdefault(method, Score())
            score.predicted += len(tags)
            score.correct += sum(1 for t in tags if t in wanted_closure)
            closure = _with_ancestors(tags, taxonomy)
            score.found += sum(1 for w in wanted if w in closure)
    for score in report.by_method.values():
        score.labeled = report.overall.labeled
    return report


def _pct(value: float | None) -> str:
    return "  n/a" if value is None else f"{value:5.2f}"


def format_report(report: Report) -> str:
    lines = [f"{report.items} labeled items", "method    predicted  precision  recall"]
    for name, score in [*sorted(report.by_method.items()), ("overall", report.overall)]:
        lines.append(
            f"{name:<9} {score.predicted:9}  {_pct(score.precision):>9}  {_pct(score.recall):>6}"
        )
    return "\n".join(lines)
