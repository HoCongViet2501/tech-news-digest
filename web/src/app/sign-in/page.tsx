import type { Metadata } from "next";

import { SignIn } from "@/components/SignIn";

export const metadata: Metadata = { title: "Sign in", robots: { index: false } };

export default function SignInPage() {
  return <SignIn />;
}
