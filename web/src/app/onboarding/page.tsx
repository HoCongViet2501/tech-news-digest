import type { Metadata } from "next";

import { Onboarding } from "@/components/Onboarding";
import { getTags } from "@/lib/data";

export const metadata: Metadata = { title: "Your interests", robots: { index: false } };

/** The tag list is public and cached; the reader's choices load and save in the browser. */
export default async function OnboardingPage() {
  const { tags } = await getTags();
  return <Onboarding tags={tags} />;
}
