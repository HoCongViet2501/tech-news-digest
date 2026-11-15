import type { Metadata } from "next";

import { CardCollection, EmptyNote } from "@/components/CardCollection";

export const metadata: Metadata = { title: "Bookmarks", robots: { index: false } };

export default function BookmarksPage() {
  return <CardCollection kind="bookmarks" empty={<EmptyNote>No bookmarks yet.</EmptyNote>} />;
}
