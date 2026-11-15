import type { Metadata } from "next";
import Link from "next/link";

import { getTags, type TagInfo } from "@/lib/data";

export const metadata: Metadata = { title: "Tags" };

function TagLink({ tag, strong }: { tag: TagInfo; strong?: boolean }) {
  return (
    <Link
      href={`/tags/${tag.slug}`}
      className={`inline-flex items-center gap-1.5 rounded-full border border-zinc-200 px-3 py-1 text-sm hover:border-zinc-400 dark:border-zinc-800 dark:hover:border-zinc-600 ${strong ? "font-semibold" : ""}`}
    >
      #{tag.slug}
      <span className="text-xs text-zinc-600 dark:text-zinc-400">{tag.itemCount}</span>
    </Link>
  );
}

export default async function TagsPage() {
  const { configured, tags } = await getTags();
  const roots = tags.filter((t) => !t.parent || !tags.some((p) => p.slug === t.parent));
  const children = (slug: string) => tags.filter((t) => t.parent === slug);

  return (
    <>
      <h1 className="mb-1 text-2xl font-bold">Tags</h1>
      <p className="mb-6 text-sm text-zinc-600 dark:text-zinc-400">Item counts cover the last 7 days.</p>
      {!configured || !tags.length ? (
        <p className="py-16 text-center text-zinc-600 dark:text-zinc-400">No tags yet.</p>
      ) : (
        <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {roots.map((root) => (
            <li
              key={root.slug}
              className="rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900"
            >
              <div className="flex items-baseline justify-between gap-2">
                <h2 className="font-semibold">{root.name}</h2>
                <TagLink tag={root} strong />
              </div>
              {root.description ? (
                <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">{root.description}</p>
              ) : null}
              {children(root.slug).length ? (
                <ul className="mt-3 flex flex-wrap gap-1.5">
                  {children(root.slug).map((c) => (
                    <li key={c.slug}>
                      <TagLink tag={c} />
                    </li>
                  ))}
                </ul>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
