import Link from "next/link";

export default function NotFound() {
  return (
    <div className="py-24 text-center">
      <h1 className="text-2xl font-bold">Not found</h1>
      <p className="mt-2 text-zinc-600 dark:text-zinc-400">
        It may be older than our retention window, or the link is wrong.
      </p>
      <Link href="/" className="mt-6 inline-block underline">
        Back to Popular
      </Link>
    </div>
  );
}
