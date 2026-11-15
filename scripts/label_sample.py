"""Export a stratified sample of tagged items for the owner to label (docs/PLAN.md W2).

    DIGEST_DB_URL=postgresql://... uv run python scripts/label_sample.py --out labels.csv

Open the CSV in a spreadsheet or editor and fill the `labels` column of each row with
every taxonomy slug that applies (space separated; the most specific ones are enough,
parents are implied), or `-` when none applies. Leave it empty to skip a row. The
`predicted` column shows what the tagger chose. Then run scripts/eval_tags.py.

The DB URL goes through the same guard as `digest ingest`: DIGEST_DB_URL from the
environment only, and a non-local host needs --allow-remote.
"""

import argparse
import os
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

import psycopg

from digest.store.dburl import DB_URL_ENV, DbUrlError, resolve_db_url
from digest.tag_eval import fetch_rows, stratified_sample, write_sample


def main(argv: Sequence[str] | None = None, env: dict[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", type=Path, default=Path("labels.csv"))
    parser.add_argument("-n", type=int, default=200, help="sample size")
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--allow-remote", action="store_true")
    args = parser.parse_args(argv)
    try:
        url = resolve_db_url(os.environ if env is None else env, allow_remote=args.allow_remote)
    except DbUrlError as exc:
        print(exc, file=sys.stderr)
        return 2
    if url is None:
        print(f"{DB_URL_ENV} is not set", file=sys.stderr)
        return 2
    if args.out.exists():
        print(f"{args.out} exists; refusing to overwrite labels", file=sys.stderr)
        return 2
    try:
        with psycopg.connect(url, prepare_threshold=None) as conn:
            rows = fetch_rows(conn)
    except psycopg.Error as exc:
        print(f"could not read the database ({type(exc).__name__})", file=sys.stderr)
        return 1
    sample = stratified_sample(rows, args.n, seed=args.seed)
    write_sample(args.out, sample)
    strata = Counter(r.stratum for r in sample)
    print(
        f"wrote {len(sample)} of {len(rows)} tagged items to {args.out}: "
        + ", ".join(f"{k}={v}" for k, v in sorted(strata.items()))
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
