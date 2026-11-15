import type { Metadata } from "next";
import Link from "next/link";

import { CardCollection, EmptyNote } from "@/components/CardCollection";

export const metadata: Metadata = { title: "Reading history", robots: { index: false } };

export default function HistoryPage() {
  return (
    <CardCollection
      kind="history"
      empty={<EmptyNote>Nothing read yet.</EmptyNote>}
      actions={
        <Link href="/settings" className="text-sm underline">
          Delete my history
        </Link>
      }
    />
  );
}
