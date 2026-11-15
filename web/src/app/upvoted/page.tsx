import type { Metadata } from "next";

import { FeedPage } from "@/components/FeedPage";

export const metadata: Metadata = { title: "Most upvoted" };

export default function UpvotedPage() {
  return <FeedPage sort="upvoted" />;
}
