import type { Card, Mention } from "./card";

/** Resized thumbnail through the free wsrv.nl proxy (no next/image: Vercel Hobby quota). */
export const COVER_RATIO = 1.91; // og:image ratio (1200×630)

export function thumbUrl(src: string, width: number): string {
  const height = Math.round(width / COVER_RATIO);
  return `https://wsrv.nl/?url=${encodeURIComponent(src)}&w=${width}&h=${height}&fit=cover&output=webp&q=70`;
}

export function thumbSrcSet(src: string): string {
  return [320, 480, 640].map((w) => `${thumbUrl(src, w)} ${w}w`).join(", ");
}

export function faviconUrl(domain: string): string {
  return `https://www.google.com/s2/favicons?domain=${encodeURIComponent(domain)}&sz=64`;
}

/** Deterministic gradient per source, for cards without a usable image. */
export function sourceGradient(key: string): string {
  let h = 0;
  for (let i = 0; i < key.length; i++) h = (h * 31 + key.charCodeAt(i)) >>> 0;
  const a = h % 360;
  const b = (a + 50) % 360;
  return `linear-gradient(135deg, hsl(${a} 70% 55%), hsl(${b} 70% 40%))`;
}

/** Compact age relative to `now` (the page's ranking time, so SSR and hydration agree). */
export function ageLabel(publishedAt: string, now: number): string {
  const minutes = Math.max(0, Math.floor((now - Date.parse(publishedAt)) / 60_000));
  if (minutes < 60) return `${Math.max(minutes, 1)}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours}h`;
  const days = Math.floor(hours / 24);
  if (days < 60) return `${days}d`;
  return `${Math.floor(days / 30)}mo`;
}

export function compactNumber(n: number): string {
  if (n < 1000) return String(n);
  if (n < 10_000) return `${(n / 1000).toFixed(1).replace(/\.0$/, "")}k`;
  return `${Math.round(n / 1000)}k`;
}

const SHORT_NAMES: Record<string, string> = {
  hackernews: "HN",
  lobsters: "Lobsters",
  devto: "dev.to",
  lemmy: "Lemmy",
  hf_papers: "HF Papers",
  producthunt: "Product Hunt",
};

export function shortSourceName(kind: string, name: string): string {
  return SHORT_NAMES[kind] ?? name;
}

/** Mentions on other sites than the card's own source ("also on HN · Lobsters"). */
export function alsoOn(card: Card): Mention[] {
  return card.mentions.filter((m) => m.sourceId !== card.source.id);
}
