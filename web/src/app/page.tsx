import { site } from "@/lib/site";

export default function Home() {
  return (
    <main className="mx-auto flex min-h-screen max-w-2xl flex-col justify-center gap-2 px-4">
      <h1 className="text-3xl font-bold">{site.name}</h1>
      <p className="text-neutral-600">{site.tagline}</p>
    </main>
  );
}
