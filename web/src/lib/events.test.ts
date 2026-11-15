import { describe, expect, it, vi } from "vitest";

import {
  dwellEvent,
  EventBatcher,
  MAX_BATCH,
  MAX_DWELL_MS,
  parsePendingDwell,
  recordEventsRequest,
  type TrackedEvent,
} from "./events";

const click = (n: number): TrackedEvent => ({ item_id: `item-${n}`, kind: "click" });

describe("EventBatcher", () => {
  it("sends nothing when empty", async () => {
    const send = vi.fn(async () => {});
    await new EventBatcher(send).flush();
    expect(send).not.toHaveBeenCalled();
  });

  it("splits a flush into chunks of at most 50, in order", async () => {
    const sent: TrackedEvent[][] = [];
    const b = new EventBatcher(async (events) => void sent.push(events), 1000);
    for (let i = 0; i < 120; i++) b.add(click(i));
    await b.flush();
    expect(sent.map((c) => c.length)).toEqual([MAX_BATCH, MAX_BATCH, 20]);
    expect(sent.flat().map((e) => e.item_id)).toEqual(Array.from({ length: 120 }, (_, i) => `item-${i}`));
    expect(b.size).toBe(0);
  });

  it("drops a failed chunk instead of retrying it, and keeps working", async () => {
    let fail = true;
    const sent: TrackedEvent[][] = [];
    const b = new EventBatcher(async (events) => {
      if (fail) throw new Error("offline");
      sent.push(events);
    });
    b.add(click(1));
    await expect(b.flush()).resolves.toBeUndefined();
    fail = false;
    b.add(click(2));
    await b.flush();
    expect(sent).toEqual([[click(2)]]);
  });

  it("caps the queue, keeping the newest events", async () => {
    const sent: TrackedEvent[][] = [];
    const b = new EventBatcher(async (events) => void sent.push(events), 3);
    for (let i = 0; i < 5; i++) b.add(click(i));
    expect(b.size).toBe(3);
    await b.flush();
    expect(sent.flat().map((e) => e.item_id)).toEqual(["item-2", "item-3", "item-4"]);
  });

  it("events added during a flush go to the next one", async () => {
    const sent: TrackedEvent[][] = [];
    const b = new EventBatcher(async (events) => {
      sent.push(events);
      b.add(click(99));
    });
    b.add(click(1));
    await b.flush();
    expect(sent).toEqual([[click(1)]]);
    expect(b.size).toBe(1);
  });
});

describe("dwellEvent", () => {
  it("measures time away and caps it at one hour", () => {
    expect(dwellEvent({ itemId: "a", startedAt: 1_000 }, 43_000)).toEqual({ item_id: "a", kind: "dwell", dwell_ms: 42_000 });
    expect(dwellEvent({ itemId: "a", startedAt: 0 }, 5 * MAX_DWELL_MS)).toMatchObject({ dwell_ms: MAX_DWELL_MS });
  });

  it("ignores missing, too short and clock-skewed visits", () => {
    expect(dwellEvent(null, 10_000)).toBeNull();
    expect(dwellEvent({ itemId: "a", startedAt: 10_000 }, 10_500)).toBeNull();
    expect(dwellEvent({ itemId: "a", startedAt: 10_000 }, 5_000)).toBeNull();
    expect(dwellEvent({ itemId: "a", startedAt: Number.NaN }, 5_000)).toBeNull();
  });
});

describe("parsePendingDwell", () => {
  it("accepts the stored shape and rejects anything else", () => {
    expect(parsePendingDwell('{"itemId":"a","startedAt":5,"leftAt":7}')).toEqual({ itemId: "a", startedAt: 5 });
    expect(parsePendingDwell(null)).toBeNull();
    expect(parsePendingDwell("{oops")).toBeNull();
    expect(parsePendingDwell('{"itemId":1,"startedAt":5}')).toBeNull();
    expect(parsePendingDwell("[]")).toBeNull();
  });
});

describe("recordEventsRequest", () => {
  it("posts to the RPC with keepalive and the user's token", () => {
    const [url, init] = recordEventsRequest("http://127.0.0.1:54321/", "anon", "jwt", [click(1)]);
    expect(url).toBe("http://127.0.0.1:54321/rest/v1/rpc/record_events");
    expect(init).toMatchObject({
      method: "POST",
      keepalive: true,
      headers: { apikey: "anon", Authorization: "Bearer jwt", "Content-Type": "application/json" },
    });
    expect(JSON.parse(init.body as string)).toEqual({ p_events: [click(1)] });
  });
});
