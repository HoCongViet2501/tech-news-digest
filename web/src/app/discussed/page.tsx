import type { Metadata } from "next";

import { FeedPage } from "@/components/FeedPage";

export const metadata: Metadata = { title: "Discussed" };

export default function DiscussedPage() {
  return <FeedPage sort="discussed" />;
}
