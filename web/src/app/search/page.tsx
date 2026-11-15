import type { Metadata } from "next";
import Form from "next/form";
import { Suspense } from "react";

import { Feed, FeedSkeleton } from "@/components/Feed";
import { searchItems } from "@/lib/data";
import { SEARCH_MAX_LENGTH } from "@/lib/queries";

export const metadata: Metadata = { title: "Search" };

function normalize(q: string | string[] | undefined): string {
  return (typeof q === "string" ? q : "").trim().replace(/\s+/g, " ").slice(0, SEARCH_MAX_LENGTH);
}

export default function SearchPage({ searchParams }: PageProps<"/search">) {
  return (
    <>
      <h1 className="sr-only">Search</h1>
      <Suspense fallback={<SearchBox q="" />}>
        <SearchBoxFromParams searchParams={searchParams} />
      </Suspense>
      <Suspense fallback={<FeedSkeleton />}>
        <Results searchParams={searchParams} />
      </Suspense>
    </>
  );
}

function SearchBox({ q }: { q: string }) {
  return (
    <Form action="/search" role="search" className="mb-6 flex gap-2">
      <input
        type="search"
        name="q"
        defaultValue={q}
        placeholder="Search titles and descriptions (accents optional)"
        aria-label="Search"
        maxLength={SEARCH_MAX_LENGTH}
        className="min-w-0 flex-1 rounded-full border border-zinc-300 bg-white px-4 py-2 outline-none focus:border-zinc-500 dark:border-zinc-700 dark:bg-zinc-900"
      />
      <button
        type="submit"
        className="rounded-full bg-zinc-900 px-5 py-2 text-sm font-medium text-white dark:bg-zinc-100 dark:text-zinc-900"
      >
        Search
      </button>
    </Form>
  );
}

async function SearchBoxFromParams({ searchParams }: Pick<PageProps<"/search">, "searchParams">) {
  const q = normalize((await searchParams).q);
  return <SearchBox key={q} q={q} />;
}

async function Results({ searchParams }: Pick<PageProps<"/search">, "searchParams">) {
  const q = normalize((await searchParams).q);
  if (!q) return null;
  const result = await searchItems(q);
  return (
    <Feed key={q} feed={result} query={{ kind: "search", q }} empty={`No results for “${q}”.`} />
  );
}
