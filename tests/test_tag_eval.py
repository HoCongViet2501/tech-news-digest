from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest

import eval_tags
import label_sample
from digest.store import ItemTag, NewItem, PostgresStore, SourceSpec, TagMethod, TagUpdate
from digest.tag_eval import (
    LabelError,
    Predicted,
    SampleRow,
    evaluate,
    fetch_rows,
    format_report,
    read_labels,
    stratified_sample,
    write_sample,
)
from digest.taxonomy import load_taxonomy

REPO_ROOT = Path(__file__).resolve().parents[1]
TAXONOMY = load_taxonomy(REPO_ROOT / "taxonomy.yaml")
T0 = datetime(2026, 11, 15, 7, 0, tzinfo=UTC)


def p(tag: str, method: str = "rule", confidence: float = 0.9) -> Predicted:
    return Predicted(tag=tag, method=method, confidence=confidence)


def row(n: int, *predicted: Predicted) -> SampleRow:
    return SampleRow(
        id=f"{n:040x}", source="src", title=f"T{n}", url=f"https://x/{n}", predicted=list(predicted)
    )


def test_stratified_sample_keeps_rare_methods_and_size() -> None:
    rows = [row(n, p("ai", "rule")) for n in range(300)]
    rows += [row(1000 + n, p("ai", "embed", 0.7)) for n in range(25)]
    rows += [row(2000 + n) for n in range(5)]

    sample = stratified_sample(rows, 100, seed=1)

    assert len(sample) == 100
    by = {s: sum(1 for r in sample if r.stratum == s) for s in ("rule", "embed", "none")}
    # 20 per stratum first (none has only 5), the rest in proportion to what is left.
    assert by == {"rule": 74, "embed": 21, "none": 5}
    assert stratified_sample(rows, 100, seed=1) == sample  # deterministic
    assert len(stratified_sample(rows, 1000, seed=1)) == len(rows)


def test_written_sample_reads_back_as_labels(tmp_path: Path) -> None:
    path = tmp_path / "labels.csv"
    write_sample(path, [row(1, p("kafka")), row(2), row(3)])
    text = path.read_text().replace("kafka(rule 0.90),", "kafka(rule 0.90),kafka messaging", 1)
    lines = text.splitlines()
    lines[2] += "-"  # row 2: nothing applies; row 3 stays unlabeled
    path.write_text("\n".join(lines) + "\n")

    assert read_labels(path, TAXONOMY) == {f"{1:040x}": {"kafka", "messaging"}, f"{2:040x}": set()}

    path.write_text(text.replace("kafka messaging", "kafka nope"))
    with pytest.raises(LabelError, match="line 2: unknown tag"):
        read_labels(path, TAXONOMY)


def test_evaluate_counts_parents_as_implied() -> None:
    labels = {"a": {"kafka"}, "b": {"messaging"}, "c": set()}
    predictions = {
        "a": [p("messaging", "embed", 0.7), p("python", "llm", 0.85)],  # parent ok, python wrong
        # Too specific: the item is on the kafka page by mistake, but the kafka tag still
        # implies the labeled parent, so "messaging" counts as found.
        "b": [p("kafka", "native", 0.95)],
        "c": [p("ai", "rule")],
    }

    report = evaluate(labels, predictions, TAXONOMY)

    assert report.items == 3
    o = report.overall
    assert (o.predicted, o.correct, o.labeled, o.found) == (4, 1, 2, 1)
    # Recall: kafka was not found for "a" (only its parent was predicted).
    native = report.by_method["native"]
    assert (native.precision, native.recall) == (0.0, 0.5)
    assert report.by_method["embed"].precision == 1.0
    assert report.by_method["rule"].precision == 0.0
    assert "overall" in format_report(report)


def test_scripts_refuse_without_a_safe_db_url(tmp_path: Path) -> None:
    out = tmp_path / "labels.csv"
    assert label_sample.main(["--out", str(out)], env={}) == 2
    remote = {"DIGEST_DB_URL": "postgresql://u:p@db.example.com/postgres"}
    assert label_sample.main(["--out", str(out)], env=remote) == 2
    assert not out.exists()
    out.write_text("id,labels\nx,ai\n")
    assert eval_tags.main([str(out)], env=remote) == 2


@pytest.mark.integration
def test_fetch_rows_reads_tagged_items_with_tags(pg_conn: psycopg.Connection) -> None:
    store = PostgresStore(pg_conn)
    store.sync_sources([SourceSpec(kind="rss", name="A", url="https://a.example/feed")])
    store.sync_tags(TAXONOMY.specs())
    source_id = store.list_sources()[0].id
    ids = [f"{n:040x}" for n in (1, 2, 3)]
    store.upsert_items(
        [
            NewItem(
                id=i,
                source_id=source_id,
                title=i[-1],
                url=f"https://a/{i}",
                published_at=T0,
                fetched_at=T0,
            )
            for i in ids
        ]
    )
    store.save_tags(
        [
            TagUpdate(
                id=ids[0],
                methods=frozenset({TagMethod.RULE}),
                tags=[ItemTag(tag="kafka", confidence=0.9, method=TagMethod.RULE)],
                done=True,
            ),
            TagUpdate(id=ids[1], methods=frozenset(), done=True),
        ],
        T0,
    )

    rows = fetch_rows(pg_conn)

    assert [(r.id, r.source, r.stratum) for r in rows] == [
        (ids[0], "A", "rule"),
        (ids[1], "A", "none"),
    ]
    assert rows[0].predicted == [p("kafka", "rule", 0.9)]
    assert [r.id for r in fetch_rows(pg_conn, [ids[1], ids[2]])] == [ids[1]]
