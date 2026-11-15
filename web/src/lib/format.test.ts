import { describe, expect, it } from "vitest";

import { toCard } from "./card";
import { ageLabel, compactNumber, thumbUrl } from "./format";

describe("format", () => {
  const now = Date.parse("2026-11-15T12:00:00Z");

  it("labels ages relative to the page time", () => {
    expect(ageLabel("2026-11-15T11:59:50Z", now)).toBe("1m");
    expect(ageLabel("2026-11-15T09:00:00Z", now)).toBe("3h");
    expect(ageLabel("2026-11-12T12:00:00Z", now)).toBe("3d");
    expect(ageLabel("2026-11-16T12:00:00Z", now)).toBe("1m"); // future dates clamp
  });

  it("compacts numbers", () => {
    expect(compactNumber(999)).toBe("999");
    expect(compactNumber(1000)).toBe("1k");
    expect(compactNumber(1234)).toBe("1.2k");
    expect(compactNumber(56789)).toBe("57k");
  });

  it("builds wsrv.nl thumbnails with a fixed ratio and an encoded source", () => {
    const url = new URL(thumbUrl("https://example.com/a b.png?x=1&y=2", 640));
    expect(url.host).toBe("wsrv.nl");
    expect(url.searchParams.get("url")).toBe("https://example.com/a b.png?x=1&y=2");
    expect(url.searchParams.get("w")).toBe("640");
    expect(url.searchParams.get("h")).toBe("335");
  });

  it("tolerates missing and malformed jsonb in rows", () => {
    const card = toCard({
      id: "a".repeat(40),
      title: null,
      url: null,
      discussion_url: null,
      description: null,
      image_url: null,
      lang: null,
      reading_min: null,
      published_at: null,
      tldr: null,
      source_id: null,
      source_name: null,
      source_kind: null,
      source_domain: null,
      upvotes: null,
      comments: null,
      tags: null,
      mentions: { not: "an array" },
      siblings: [null, 3],
    });
    expect(card.tags).toEqual([]);
    expect(card.mentions).toEqual([]);
    expect(card.siblings).toHaveLength(2);
    expect(card.upvotes).toBe(0);
  });
});
