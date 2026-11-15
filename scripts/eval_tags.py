"""Score the tagger against the owner's labels (docs/PLAN.md W2: precision >= 0.85,
recall >= 0.70).

    DIGEST_DB_URL=postgresql://... uv run python scripts/eval_tags.py labels.csv

Predictions are the items' current tags in the database, so a re-tagged item is scored
as it is now. See digest/tag_eval.py for how parent tags count.
"""

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

import psycopg

from digest.config import ConfigError
from digest.store.dburl import DB_URL_ENV, DbUrlError, resolve_db_url
from digest.tag_eval import LabelError, evaluate, fetch_rows, format_report, read_labels
from digest.taxonomy import load_taxonomy


def main(argv: Sequence[str] | None = None, env: dict[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("labels", type=Path)
    parser.add_argument("--taxonomy", type=Path, default=Path("taxonomy.yaml"))
    parser.add_argument("--allow-remote", action="store_true")
    args = parser.parse_args(argv)
    try:
        url = resolve_db_url(os.environ if env is None else env, allow_remote=args.allow_remote)
        taxonomy = load_taxonomy(args.taxonomy)
        labels = read_labels(args.labels, taxonomy)
    except (DbUrlError, ConfigError, LabelError, OSError) as exc:
        print(exc, file=sys.stderr)
        return 2
    if url is None:
        print(f"{DB_URL_ENV} is not set", file=sys.stderr)
        return 2
    if not labels:
        print(f"no labeled rows in {args.labels}", file=sys.stderr)
        return 2
    try:
        with psycopg.connect(url, prepare_threshold=None) as conn:
            rows = fetch_rows(conn, list(labels))
    except psycopg.Error as exc:
        print(f"could not read the database ({type(exc).__name__})", file=sys.stderr)
        return 1
    missing = len(labels) - len(rows)
    if missing:
        print(f"note: {missing} labeled item(s) are no longer tagged in the database")
    report = evaluate(labels, {r.id: r.predicted for r in rows}, taxonomy)
    print(format_report(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
