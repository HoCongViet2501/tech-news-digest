import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense } from "react";

import { Feed, FeedSkeleton } from "@/components/Feed";
import { getFeed, getTags } from "@/lib/data";

// Cache Components needs at least one param at build time; this one renders a 404.
const PLACEHOLDER = "__none__";

export async function generateStaticParams() {
  const { tags } = await getTags();
  const top = tags.filter((t) => t.itemCount > 0).slice(0, 12);
  return top.length ? top.map((t) => ({ slug: t.slug })) : [{ slug: PLACEHOLDER }];
}

export async function generateMetadata({ params }: PageProps<"/tags/[slug]">): Promise<Metadata> {
  const { slug } = await params;
  const tag = (await getTags()).tags.find((t) => t.slug === slug);
  return tag ? { title: `#${tag.slug}`, description: tag.description ?? undefined } : {};
}

export default function TagPage({ params }: PageProps<"/tags/[slug]">) {
  return (
    <Suspense fallback={<FeedSkeleton />}>
      <TagFeed params={params} />
    </Suspense>
  );
}

async function TagFeed({ params }: Pick<PageProps<"/tags/[slug]">, "params">) {
  const { slug } = await params;
  const { tags } = await getTags();
  const tag = tags.find((t) => t.slug === slug);
  if (!tag) notFound();
  const children = tags.filter((t) => t.parent === tag.slug);
  const parent = tags.find((t) => t.slug === tag.parent);
  const feed = await getFeed("popular", tag.slug);

  return (
    <>
      <header className="mb-6">
        {parent ? (
          <Link href={`/tags/${parent.slug}`} className="text-sm text-zinc-600 hover:underline dark:text-zinc-400">
            #{parent.slug}
          </Link>
        ) : null}
        <h1 className="text-2xl font-bold">#{tag.slug}</h1>
        <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">
          {tag.name}
          {tag.description ? ` — ${tag.description}` : null}
        </p>
        {children.length ? (
          <ul className="mt-3 flex flex-wrap gap-1.5" aria-label="Sub-tags">
            {children.map((c) => (
              <li key={c.slug}>
                <Link
                  href={`/tags/${c.slug}`}
                  className="rounded-full bg-zinc-100 px-2.5 py-0.5 text-xs text-zinc-700 hover:bg-zinc-200 dark:bg-zinc-800 dark:text-zinc-300 dark:hover:bg-zinc-700"
                >
                  #{c.slug}
                </Link>
              </li>
            ))}
          </ul>
        ) : null}
      </header>
      <Feed
        feed={feed}
        query={{ kind: "feed", sort: "popular", tag: tag.slug, asOf: feed.asOf }}
        empty="No items with this tag in the last 7 days."
      />
    </>
  );
}
