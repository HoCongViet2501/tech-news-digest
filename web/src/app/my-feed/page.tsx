import type { Metadata } from "next";

import { MyFeed } from "@/components/MyFeed";

export const metadata: Metadata = { title: "My Feed", robots: { index: false } };

/** Static shell; the feed itself loads in the browser with the reader's session. */
export default function MyFeedPage() {
  return <MyFeed />;
}
