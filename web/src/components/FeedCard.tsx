import Link from "next/link";

import type { Card } from "@/lib/card";
import { ageLabel, alsoOn, compactNumber, shortSourceName } from "@/lib/format";

import { CardActions } from "./CardActions";
import { CardShell } from "./CardShell";
import { Cover } from "./Cover";

export const FEED_GRID = "grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4";

export const CARD_SIZES = "(min-width: 1280px) 300px, (min-width: 1024px) 31vw, (min-width: 640px) 48vw, 100vw";

type Props = {
  card: Card;
  /** Ranking time of the page (ms); ages are relative to it so SSR and hydration agree. */
  now: number;
  eager?: boolean;
};

/**
 * One grid card. Plain markup (no hooks), so it renders on the server and in the feed list.
 * The signed-in parts (actions, removal on hide) are client islands that render nothing
 * on the server, so cached public pages stay the same for everyone.
 */
export function FeedCard({ card, now, eager }: Props) {
  const others = alsoOn(card);
  return (
    <CardShell id={card.id} tags={card.tags.map((t) => t.slug)} sourceId={card.source.id}>
      <article className="group relative flex flex-col overflow-hidden rounded-2xl border border-zinc-200 bg-white shadow-sm transition hover:border-zinc-300 hover:shadow-md dark:border-zinc-800 dark:bg-zinc-900 dark:hover:border-zinc-700">
        <div className="flex flex-1 flex-col gap-2 p-4">
          <div className="flex items-center gap-2 text-xs text-zinc-600 dark:text-zinc-400">
            <span className="truncate font-medium">{card.source.name}</span>
            <span aria-hidden="true">·</span>
            <time dateTime={card.publishedAt} className="shrink-0">
              {ageLabel(card.publishedAt, now)}
            </time>
            {card.readingMin ? (
              <>
                <span aria-hidden="true">·</span>
                <span className="shrink-0">{card.readingMin} min read</span>
              </>
            ) : null}
          </div>
          <h2 lang={card.lang ?? undefined} className="line-clamp-3 text-base leading-snug font-semibold">
            <Link
              href={`/items/${card.id}`}
              prefetch={false}
              data-item-id={card.id}
              className="after:absolute after:inset-0 after:content-[''] focus-visible:outline-none group-focus-within:underline"
            >
              {card.title}
            </Link>
          </h2>
          {card.description ? (
            <p lang={card.lang ?? undefined} className="line-clamp-2 text-sm text-zinc-600 dark:text-zinc-400">
              {card.description}
            </p>
          ) : null}
          {card.tags.length ? (
            <ul className="relative z-10 mt-auto flex flex-wrap gap-1.5 pt-1" aria-label="Tags">
              {card.tags.slice(0, 3).map((t) => (
                <li key={t.slug}>
                  <Link
                    href={`/tags/${t.slug}`}
                    prefetch={false}
                    className="rounded-full bg-zinc-100 px-2 py-0.5 text-xs text-zinc-700 hover:bg-zinc-200 dark:bg-zinc-800 dark:text-zinc-300 dark:hover:bg-zinc-700"
                  >
                    #{t.slug}
                  </Link>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
        <Cover
          imageUrl={card.imageUrl}
          sourceName={card.source.name}
          sourceDomain={card.source.domain}
          sizes={CARD_SIZES}
          eager={eager}
        />
        <div className="flex items-center gap-3 px-4 py-2.5 text-xs text-zinc-600 dark:text-zinc-400">
          <span title="Upvotes">▲ {compactNumber(card.upvotes)}</span>
          <span title="Comments">💬 {compactNumber(card.comments)}</span>
          {others.length ? (
            <span className="ml-auto truncate" title="Also on">
              also on {others.map((m) => shortSourceName(m.sourceKind, m.sourceName)).join(" · ")}
            </span>
          ) : null}
        </div>
        <CardActions id={card.id} tags={card.tags} source={card.source} />
      </article>
    </CardShell>
  );
}
