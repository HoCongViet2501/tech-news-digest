import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense } from "react";

import { Cover } from "@/components/Cover";
import { getFeed, getItem } from "@/lib/data";
import { ageLabel, compactNumber, shortSourceName } from "@/lib/format";

// Cache Components needs at least one param at build time; this one renders a 404.
const PLACEHOLDER = "0".repeat(40);
const DESCRIPTION_MAX = 600;

export async function generateStaticParams() {
  // Prerender today's Popular page; other items render on first visit, then are cached.
  const { cards } = await getFeed("popular");
  return cards.length ? cards.map((c) => ({ id: c.id })) : [{ id: PLACEHOLDER }];
}

export async function generateMetadata({ params }: PageProps<"/items/[id]">): Promise<Metadata> {
  const { id } = await params;
  const { card } = await getItem(id);
  return card ? { title: card.title, description: card.description?.slice(0, 160) } : {};
}

export default function ItemPage({ params }: PageProps<"/items/[id]">) {
  return (
    <Suspense fallback={<div className="mx-auto h-96 max-w-3xl animate-pulse rounded-2xl bg-zinc-200 dark:bg-zinc-800" />}>
      <Item params={params} />
    </Suspense>
  );
}

function excerpt(text: string): string {
  return text.length > DESCRIPTION_MAX ? `${text.slice(0, DESCRIPTION_MAX).trimEnd()}…` : text;
}

async function Item({ params }: Pick<PageProps<"/items/[id]">, "params">) {
  const { id } = await params;
  const { card, asOf } = await getItem(id);
  if (!card) notFound();
  const discussions = card.mentions.filter((m) => m.discussionUrl);

  return (
    <article className="mx-auto max-w-3xl">
      <div className="mb-3 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-zinc-600 dark:text-zinc-400">
        <span className="font-medium">{card.source.name}</span>
        <span aria-hidden="true">·</span>
        <time dateTime={card.publishedAt}>{ageLabel(card.publishedAt, Date.parse(asOf))} ago</time>
        {card.readingMin ? (
          <>
            <span aria-hidden="true">·</span>
            <span>{card.readingMin} min read</span>
          </>
        ) : null}
        <span aria-hidden="true">·</span>
        <span>▲ {compactNumber(card.upvotes)}</span>
        <span>💬 {compactNumber(card.comments)}</span>
      </div>
      <h1 lang={card.lang ?? undefined} className="text-2xl leading-tight font-bold sm:text-3xl">
        {card.title}
      </h1>
      {card.tags.length ? (
        <ul className="mt-3 flex flex-wrap gap-1.5" aria-label="Tags">
          {card.tags.map((t) => (
            <li key={t.slug}>
              <Link
                href={`/tags/${t.slug}`}
                className="rounded-full bg-zinc-100 px-2.5 py-0.5 text-xs text-zinc-700 hover:bg-zinc-200 dark:bg-zinc-800 dark:text-zinc-300 dark:hover:bg-zinc-700"
              >
                #{t.slug}
              </Link>
            </li>
          ))}
        </ul>
      ) : null}

      <Cover
        imageUrl={card.imageUrl}
        sourceName={card.source.name}
        sourceDomain={card.source.domain}
        sizes="(min-width: 768px) 768px, 100vw"
        eager
        className="mt-5 rounded-2xl"
      />

      {card.tldr ? (
        <section className="mt-6 rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
          <h2 className="mb-1 text-sm font-semibold tracking-wide text-zinc-600 uppercase dark:text-zinc-400">
            TL;DR
          </h2>
          <p className="leading-relaxed">{card.tldr}</p>
        </section>
      ) : null}

      {card.description ? (
        <p lang={card.lang ?? undefined} className="mt-6 leading-relaxed text-zinc-700 dark:text-zinc-300">
          {excerpt(card.description)}
        </p>
      ) : null}

      <div className="mt-6 flex flex-wrap gap-3">
        <a
          href={card.url}
          rel="noopener"
          className="rounded-full bg-zinc-900 px-5 py-2 text-sm font-medium text-white hover:bg-zinc-700 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
        >
          Read the original ↗
        </a>
        {discussions.map((m) => (
          <a
            key={m.sourceId}
            href={m.discussionUrl ?? undefined}
            rel="noopener"
            className="rounded-full border border-zinc-300 px-5 py-2 text-sm hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-800"
          >
            Discuss on {shortSourceName(m.sourceKind, m.sourceName)} ({compactNumber(m.comments)}) ↗
          </a>
        ))}
      </div>

      {card.mentions.length > 1 ? (
        <section className="mt-8">
          <h2 className="mb-2 font-semibold">Seen on</h2>
          <ul className="space-y-1 text-sm">
            {card.mentions.map((m) => (
              <li key={m.sourceId}>
                {m.discussionUrl ? (
                  <a href={m.discussionUrl} rel="noopener" className="underline">
                    {m.sourceName}
                  </a>
                ) : (
                  m.sourceName
                )}{" "}
                <span className="text-zinc-600 dark:text-zinc-400">
                  ▲ {compactNumber(m.score)} · 💬 {compactNumber(m.comments)}
                </span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {card.siblings.length ? (
        <section className="mt-8">
          <h2 className="mb-2 font-semibold">Same story elsewhere</h2>
          <ul className="space-y-2 text-sm">
            {card.siblings.map((s) => (
              <li key={s.id}>
                <Link href={`/items/${s.id}`} prefetch={false} className="underline">
                  {s.title}
                </Link>{" "}
                <span className="text-zinc-600 dark:text-zinc-400">— {s.sourceName}</span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </article>
  );
}
