import Form from "next/form";
import Link from "next/link";

import { site } from "@/lib/site";

import { ThemeToggle } from "./ThemeToggle";

export function Header() {
  return (
    <header className="sticky top-0 z-20 border-b border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-950">
      <div className="mx-auto flex h-14 max-w-7xl items-center gap-3 px-4">
        <Link href="/" className="shrink-0 text-lg font-bold tracking-tight">
          {site.name}
        </Link>
        <nav className="flex items-center gap-1 text-sm" aria-label="Main">
          <Link href="/" className="rounded-full px-3 py-1.5 hover:bg-zinc-100 dark:hover:bg-zinc-800">
            Popular
          </Link>
          <Link href="/tags" className="rounded-full px-3 py-1.5 hover:bg-zinc-100 dark:hover:bg-zinc-800">
            Tags
          </Link>
        </nav>
        <Form action="/search" className="ml-auto hidden min-w-0 flex-1 justify-end sm:flex" role="search">
          <input
            type="search"
            name="q"
            placeholder="Search"
            aria-label="Search"
            maxLength={100}
            className="w-full max-w-xs rounded-full border border-zinc-300 bg-transparent px-4 py-1.5 text-sm outline-none focus:border-zinc-500 dark:border-zinc-700"
          />
        </Form>
        <Link
          href="/search"
          aria-label="Search"
          className="ml-auto flex size-9 items-center justify-center rounded-full hover:bg-zinc-100 sm:hidden dark:hover:bg-zinc-800"
        >
          <svg aria-hidden="true" viewBox="0 0 20 20" className="size-5" fill="none" stroke="currentColor" strokeWidth="2">
            <circle cx="9" cy="9" r="6" />
            <path d="m14 14 4 4" />
          </svg>
        </Link>
        <ThemeToggle />
      </div>
    </header>
  );
}
