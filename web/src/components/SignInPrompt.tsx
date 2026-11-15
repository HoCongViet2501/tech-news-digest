import Link from "next/link";

/** Shown by personalized pages to signed-out readers. */
export function SignInPrompt({ next, title }: { next: string; title: string }) {
  return (
    <div className="mx-auto max-w-md py-16 text-center">
      <h1 className="text-2xl font-bold">{title}</h1>
      <p className="mt-2 text-zinc-600 dark:text-zinc-400">Sign in to see this page. The site is invite-only.</p>
      <Link
        href={`/sign-in?next=${encodeURIComponent(next)}`}
        className="mt-6 inline-block rounded-full bg-zinc-900 px-5 py-2 text-sm font-medium text-white hover:bg-zinc-700 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
      >
        Sign in
      </Link>
      <p className="mt-4 text-sm">
        <Link href="/" className="underline">
          Browse Popular instead
        </Link>
      </p>
    </div>
  );
}
