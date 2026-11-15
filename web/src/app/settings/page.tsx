import type { Metadata } from "next";

import { Settings } from "@/components/Settings";
import { getTags } from "@/lib/data";

export const metadata: Metadata = { title: "Settings", robots: { index: false } };

/** The tag list is public and cached; the reader's own settings load in the browser. */
export default async function SettingsPage() {
  const { tags } = await getTags();
  return <Settings tags={tags} />;
}
