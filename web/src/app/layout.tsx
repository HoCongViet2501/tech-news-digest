import type { Metadata, Viewport } from "next";

import { Header } from "@/components/Header";
import { Tracker } from "@/components/Tracker";
import { homeRedirectScript } from "@/lib/auth-config";
import { site } from "@/lib/site";
import { themeScript } from "@/lib/theme";

import "./globals.css";

export const metadata: Metadata = {
  title: { default: site.name, template: `%s · ${site.name}` },
  description: site.tagline,
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#ffffff" },
    { media: "(prefers-color-scheme: dark)", color: "#09090b" },
  ],
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    // data-theme is set before hydration by the inline script.
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript + homeRedirectScript }} />
      </head>
      <body className="min-h-screen bg-zinc-50 text-zinc-900 antialiased dark:bg-zinc-950 dark:text-zinc-100">
        <Header />
        <main className="mx-auto max-w-7xl px-4 py-6">{children}</main>
        <Tracker />
      </body>
    </html>
  );
}
