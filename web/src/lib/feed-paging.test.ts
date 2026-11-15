import { describe, expect, it } from "vitest";

import type { Card } from "./card";
import { applyPage, initialPaging, nextCursor } from "./feed-paging";
import type { FeedRow } from "./personal";

const card = (id: string) => ({ id }) as Card;
const rows = (snapshotId: number, from: number, ids: string[]): FeedRow[] =>
  ids.map((id, i) => ({ snapshotId, pos: from + i, card: card(id) }));
const ids = (pages: Card[][]) => pages.map((p) => p.map((c) => c.id));

describe("My Feed paging", () => {
  it("starts without a cursor and then follows snapshot id + last position", () => {
    expect(nextCursor(initialPaging)).toBeNull();
    const s1 = applyPage(initialPaging, null, rows(7, 1, ["a", "b", "c"]), 3);
    expect(s1).toMatchObject({ snapshotId: 7, lastPos: 3, done: false, restarted: false });
    expect(nextCursor(s1)).toEqual({ snapshotId: 7, after: 3 });

    // Hidden items are skipped server-side, so positions can jump.
    const s2 = applyPage(s1, nextCursor(s1), rows(7, 5, ["d", "e", "f"]), 3);
    expect(ids(s2.pages)).toEqual([["a", "b", "c"], ["d", "e", "f"]]);
    expect(nextCursor(s2)).toEqual({ snapshotId: 7, after: 7 });
  });

  it("a short page ends the list; an empty one too, keeping the pages", () => {
    const s1 = applyPage(initialPaging, null, rows(7, 1, ["a", "b"]), 3);
    expect(s1.done).toBe(true);
    const full = applyPage(initialPaging, null, rows(7, 1, ["a", "b", "c"]), 3);
    const end = applyPage(full, nextCursor(full), [], 3);
    expect(end.done).toBe(true);
    expect(ids(end.pages)).toEqual([["a", "b", "c"]]);
  });

  it("restarts from the top when the snapshot expired", () => {
    const s1 = applyPage(initialPaging, null, rows(7, 1, ["a", "b", "c"]), 3);
    const s2 = applyPage(s1, nextCursor(s1), rows(9, 1, ["x", "a", "y"]), 3);
    expect(s2.restarted).toBe(true);
    expect(s2.snapshotId).toBe(9);
    expect(ids(s2.pages)).toEqual([["x", "a", "y"]]);
    expect(nextCursor(s2)).toEqual({ snapshotId: 9, after: 3 });
  });

  it("a first page or refresh replaces the list", () => {
    const s1 = applyPage(initialPaging, null, rows(7, 1, ["a", "b", "c"]), 3);
    const s2 = applyPage(s1, null, rows(8, 1, ["d"]), 3);
    expect(ids(s2.pages)).toEqual([["d"]]);
    expect(s2.restarted).toBe(false);
    expect(applyPage(s1, null, [], 3)).toMatchObject({ pages: [], done: true, snapshotId: null });
  });

  it("drops ids already shown", () => {
    const s1 = applyPage(initialPaging, null, rows(7, 1, ["a", "b", "c"]), 3);
    const s2 = applyPage(s1, nextCursor(s1), rows(7, 4, ["c", "d", "d"]), 3);
    expect(ids(s2.pages)).toEqual([["a", "b", "c"], ["d"]]);
    expect(s2.lastPos).toBe(6);
  });
});
