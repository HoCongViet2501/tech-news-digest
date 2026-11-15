import type { FeedQuery } from "@/lib/card";
import type { FeedResult } from "@/lib/data";

import { FEED_GRID, FeedCard } from "./FeedCard";
import { FeedList } from "./FeedList";

/** First page rendered on the server (cached); later pages load in the browser. */
export function Feed({ feed, query, empty }: { feed: FeedResult; query: FeedQuery; empty: string }) {
  if (!feed.configured) {
    return (
      <p className="py-16 text-center text-zinc-600 dark:text-zinc-400">
        Not connected to a database. Set NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY (see
        web/.env.example).
      </p>
    );
  }
  if (!feed.cards.length) {
    return <p className="py-16 text-center text-zinc-600 dark:text-zinc-400">{empty}</p>;
  }
  const now = Date.parse(feed.asOf);
  return (
    <FeedList firstIds={feed.cards.map((c) => c.id)} query={query} asOf={feed.asOf}>
      <div className={FEED_GRID}>
        {feed.cards.map((card, i) => (
          <FeedCard key={card.id} card={card} now={now} eager={i < 2} />
        ))}
      </div>
    </FeedList>
  );
}

export function FeedSkeleton() {
  return (
    <div className={FEED_GRID} aria-busy="true">
      {Array.from({ length: 8 }, (_, i) => (
        <div key={i} className="h-80 animate-pulse rounded-2xl bg-zinc-200 dark:bg-zinc-800" />
      ))}
    </div>
  );
}
