import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { toCard, type FeedCardRow } from "@/lib/card";

import { FeedCard } from "./FeedCard";

const NOW = Date.parse("2026-11-15T12:00:00Z");

function row(overrides: Partial<FeedCardRow>): FeedCardRow {
  return {
    id: "a".repeat(40),
    title: "Title",
    url: "https://example.com/post",
    discussion_url: "https://news.ycombinator.com/item?id=1",
    description: null,
    image_url: null,
    lang: "en",
    reading_min: 5,
    published_at: "2026-11-15T09:00:00Z",
    tldr: null,
    source_id: 1,
    source_name: "Hacker News",
    source_kind: "hackernews",
    source_domain: "news.ycombinator.com",
    upvotes: 1234,
    comments: 56,
    tags: [],
    mentions: [],
    siblings: [],
    ...overrides,
  };
}

const render = (r: FeedCardRow) => renderToStaticMarkup(<FeedCard card={toCard(r)} now={NOW} />);

describe("FeedCard", () => {
  it("escapes & < > in the title and description and keeps emoji", () => {
    const html = render(
      row({
        title: "Rust & WebAssembly: <script>alert(1)</script> builds 🚀",
        description: "Ship it & smile <3 🎉 <img src=x onerror=alert(1)>",
        image_url: "https://example.com/cover.png?a=1&b=<2>",
      }),
    );
    expect(html).not.toContain("<script>");
    expect(html).not.toContain("<img src=x");
    expect(html).toContain("Rust &amp; WebAssembly: &lt;script&gt;alert(1)&lt;/script&gt; builds 🚀");
    expect(html).toContain("Ship it &amp; smile &lt;3 🎉");
    expect(html).toMatchSnapshot();
  });

  it("renders tags, also-on mentions, and a Vietnamese title with emoji", () => {
    const html = render(
      row({
        title: "Kiến trúc <microservices> & DDD 🧱",
        lang: "vi",
        tags: [
          { slug: "architecture", name: "Architecture" },
          { slug: "ddd", name: "DDD" },
          { slug: "backend", name: "Backend" },
          { slug: "hidden", name: "Fourth tag is not shown" },
        ],
        mentions: [
          { source_id: 1, source_name: "Hacker News", source_kind: "hackernews", discussion_url: null, score: 1, comments: 1 },
          { source_id: 2, source_name: "Lobsters", source_kind: "lobsters", discussion_url: null, score: 1, comments: 1 },
        ],
      }),
    );
    expect(html).toContain("also on Lobsters");
    expect(html).not.toContain("#hidden");
    expect(html).toMatchSnapshot();
  });

  it("falls back to the source favicon on a gradient without an image", () => {
    const html = render(row({ image_url: null }));
    expect(html).toContain("google.com/s2/favicons?domain=news.ycombinator.com");
    expect(html).toContain("linear-gradient");
    expect(html).not.toContain("wsrv.nl");
  });
});
