import type { Card } from "./card";
import type { FeedRow } from "./personal";

// My Feed paging over my_feed snapshots (docs/PLAN.md §15 step 4, §16 W4 notes).
// The first page is requested without a cursor; each next page passes the snapshot id
// and the last position. A different snapshot id in a response means the old snapshot
// expired and the list restarted from the top.

export type Cursor = { snapshotId: number; after: number };

export type PagingState = {
  snapshotId: number | null;
  /** Position (1-based, in the snapshot) of the last row received. */
  lastPos: number;
  pages: Card[][];
  done: boolean;
  /** True when the last response restarted the list (expired snapshot). */
  restarted: boolean;
};

export const initialPaging: PagingState = { snapshotId: null, lastPos: 0, pages: [], done: false, restarted: false };

/** The cursor for the next page; null means "first page". */
export function nextCursor(state: PagingState): Cursor | null {
  return state.snapshotId === null ? null : { snapshotId: state.snapshotId, after: state.lastPos };
}

/**
 * Folds one response into the state. `requested` is the cursor the rows were fetched
 * with (null for a first page or a refresh, which always replace the list).
 * Rows come after filtering hidden/blocked items and are limited after it, so a short
 * page means the snapshot is exhausted.
 */
export function applyPage(state: PagingState, requested: Cursor | null, rows: FeedRow[], pageSize: number): PagingState {
  const restarted = requested !== null && rows.length > 0 && rows[0].snapshotId !== requested.snapshotId;
  const fresh = requested === null || restarted;
  if (!rows.length) {
    return fresh
      ? { ...initialPaging, done: true }
      : { ...state, done: true, restarted: false };
  }
  const base = fresh ? [] : state.pages;
  const seen = new Set(base.flat().map((c) => c.id));
  const cards: Card[] = [];
  for (const { card } of rows) {
    if (seen.has(card.id)) continue;
    seen.add(card.id);
    cards.push(card);
  }
  return {
    snapshotId: rows[0].snapshotId,
    lastPos: rows[rows.length - 1].pos,
    pages: [...base, cards],
    done: rows.length < pageSize,
    restarted,
  };
}
