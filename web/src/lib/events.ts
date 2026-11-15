// Click / dwell event batching (docs/PLAN.md §16 W4 step 4). Pure logic; the browser
// wiring lives in components/Tracker.tsx.

export type TrackedEvent =
  | { item_id: string; kind: "click" }
  | { item_id: string; kind: "dwell"; dwell_ms: number };

/** record_events accepts at most 50 events per call. */
export const MAX_BATCH = 50;
/** Longest dwell the DB keeps (1 h). */
export const MAX_DWELL_MS = 3_600_000;
/** Shorter returns are noise (a mis-click, an app switch), not reading. */
export const MIN_DWELL_MS = 1_000;

export type Sender = (events: TrackedEvent[]) => Promise<void>;

/**
 * Queues events and sends them in chunks of at most MAX_BATCH. A failed chunk is dropped
 * (tracking is best effort and must never block the UI or retry forever); the queue is
 * capped so a long offline session cannot grow it without bound.
 */
export class EventBatcher {
  private queue: TrackedEvent[] = [];

  constructor(
    private readonly send: Sender,
    private readonly maxQueued = MAX_BATCH * 4,
  ) {}

  get size(): number {
    return this.queue.length;
  }

  add(event: TrackedEvent): void {
    this.queue.push(event);
    if (this.queue.length > this.maxQueued) this.queue.splice(0, this.queue.length - this.maxQueued);
  }

  /** Sends everything queued; resolves once every chunk was sent or failed. */
  async flush(): Promise<void> {
    if (!this.queue.length) return;
    const pending = this.queue;
    this.queue = [];
    const chunks: TrackedEvent[][] = [];
    for (let i = 0; i < pending.length; i += MAX_BATCH) chunks.push(pending.slice(i, i + MAX_BATCH));
    await Promise.all(chunks.map((c) => this.send(c).catch(() => {})));
  }
}

export type PendingDwell = { itemId: string; startedAt: number };

/** The dwell event for time spent away on an article, or null when it is too short to count. */
export function dwellEvent(pending: PendingDwell | null, now: number): TrackedEvent | null {
  if (!pending) return null;
  const ms = Math.round(now - pending.startedAt);
  if (!(ms >= MIN_DWELL_MS)) return null;
  return { item_id: pending.itemId, kind: "dwell", dwell_ms: Math.min(ms, MAX_DWELL_MS) };
}

export function parsePendingDwell(raw: string | null): PendingDwell | null {
  if (!raw) return null;
  try {
    const v: unknown = JSON.parse(raw);
    if (
      typeof v === "object" &&
      v !== null &&
      typeof (v as PendingDwell).itemId === "string" &&
      typeof (v as PendingDwell).startedAt === "number"
    ) {
      return { itemId: (v as PendingDwell).itemId, startedAt: (v as PendingDwell).startedAt };
    }
  } catch {
    // Corrupt value: ignore it.
  }
  return null;
}

/** The fetch request for one batch: keepalive (sendBeacon cannot send Authorization). */
export function recordEventsRequest(
  supabaseUrl: string,
  anonKey: string,
  accessToken: string,
  events: TrackedEvent[],
): [string, RequestInit] {
  return [
    `${supabaseUrl.replace(/\/$/, "")}/rest/v1/rpc/record_events`,
    {
      method: "POST",
      keepalive: true,
      headers: {
        apikey: anonKey,
        Authorization: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ p_events: events }),
    },
  ];
}
