import Link from "next/link";

import { SORTS, type Sort } from "@/lib/card";
import { getFeed } from "@/lib/data";

import { Feed } from "./Feed";

/** Popular / Most upvoted / Discussed. One static route per sort, so each is prerendered. */
export async function FeedPage({ sort }: { sort: Sort }) {
  const feed = await getFeed(sort);
  const label = SORTS.find((s) => s.sort === sort)?.label ?? "Popular";
  return (
    <>
      <h1 className="sr-only">{label}</h1>
      <nav aria-label="Sort" className="mb-5 flex gap-2 overflow-x-auto">
        {SORTS.map((s) => (
          <Link
            key={s.sort}
            href={s.href}
            aria-current={s.sort === sort ? "page" : undefined}
            className={
              s.sort === sort
                ? "shrink-0 rounded-full bg-zinc-900 px-4 py-1.5 text-sm font-medium text-white dark:bg-zinc-100 dark:text-zinc-900"
                : "shrink-0 rounded-full px-4 py-1.5 text-sm text-zinc-700 hover:bg-zinc-200 dark:text-zinc-300 dark:hover:bg-zinc-800"
            }
          >
            {s.label}
          </Link>
        ))}
      </nav>
      <Feed
        feed={feed}
        query={{ kind: "feed", sort, tag: null, asOf: feed.asOf }}
        empty="Nothing from the last 7 days yet."
      />
    </>
  );
}
