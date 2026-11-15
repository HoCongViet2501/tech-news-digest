"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useId, useRef, useState } from "react";

import { signOut } from "@/lib/browser-client";
import { resetPersonalStore } from "@/lib/personal-store";
import { useHydrated, useSessionUser, type SessionUser } from "@/lib/session";

/**
 * Header account area. Signed out: "Sign in". Signed in: a "My Feed" button and an avatar
 * menu (bookmarks, history, settings, sign out). Renders a fixed-size placeholder on the
 * server and during hydration, so cached pages look the same for everyone.
 */
export function AccountMenu() {
  const hydrated = useHydrated();
  const user = useSessionUser();
  if (!hydrated) return <div className="size-9 shrink-0" aria-hidden="true" />;
  if (!user) {
    return (
      <Link
        href="/sign-in"
        className="shrink-0 rounded-full border border-zinc-300 px-3 py-1.5 text-sm font-medium hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-800"
      >
        Sign in
      </Link>
    );
  }
  return <SignedIn user={user} />;
}

function SignedIn({ user }: { user: SessionUser }) {
  const pathname = usePathname();
  const onFeed = pathname === "/my-feed";
  return (
    <>
      <Link
        href="/my-feed"
        aria-current={onFeed ? "page" : undefined}
        className={`hidden shrink-0 items-center gap-1.5 rounded-full px-3.5 py-1.5 text-sm font-semibold shadow-sm transition sm:inline-flex ${
          onFeed
            ? "bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900"
            : "bg-gradient-to-r from-indigo-500 to-fuchsia-500 text-white hover:from-indigo-600 hover:to-fuchsia-600"
        }`}
      >
        <svg aria-hidden="true" viewBox="0 0 20 20" className="size-4" fill="currentColor">
          <path d="M10 1.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8L10 14.9l-5.2 2.7 1-5.8L1.5 7.7l5.9-.9z" />
        </svg>
        My Feed
      </Link>
      <UserMenu user={user} />
    </>
  );
}

function UserMenu({ user }: { user: SessionUser }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const menuId = useId();

  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const item = "block w-full rounded-lg px-3 py-2 text-left text-sm hover:bg-zinc-100 dark:hover:bg-zinc-800";
  const close = () => setOpen(false);

  return (
    <div ref={ref} className="relative shrink-0">
      <button
        type="button"
        aria-label={`Account menu for ${user.name}`}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        onClick={() => setOpen((o) => !o)}
        className="flex size-9 items-center justify-center overflow-hidden rounded-full bg-zinc-200 text-sm font-semibold ring-zinc-400 hover:ring-2 dark:bg-zinc-700"
      >
        {user.avatarUrl ? (
          // eslint-disable-next-line @next/next/no-img-element -- tiny avatar; no next/image optimization (Hobby quota)
          <img src={user.avatarUrl} alt="" width={36} height={36} referrerPolicy="no-referrer" className="size-full" />
        ) : (
          <span aria-hidden="true">{user.name.slice(0, 1).toUpperCase()}</span>
        )}
      </button>
      {open ? (
        <div
          id={menuId}
          role="menu"
          className="absolute right-0 mt-2 w-56 rounded-xl border border-zinc-200 bg-white p-1 shadow-lg dark:border-zinc-700 dark:bg-zinc-900"
        >
          <p className="truncate px-3 py-2 text-xs text-zinc-600 dark:text-zinc-400">{user.email ?? user.name}</p>
          <Link href="/my-feed" role="menuitem" className={`${item} sm:hidden`} onClick={close}>
            My Feed
          </Link>
          <Link href="/bookmarks" role="menuitem" className={item} onClick={close}>
            Bookmarks
          </Link>
          <Link href="/history" role="menuitem" className={item} onClick={close}>
            History
          </Link>
          <Link href="/settings" role="menuitem" className={item} onClick={close}>
            Settings
          </Link>
          <button
            type="button"
            role="menuitem"
            className={item}
            onClick={async () => {
              close();
              await signOut().catch(() => {});
              resetPersonalStore();
              router.push("/");
            }}
          >
            Sign out
          </button>
        </div>
      ) : null}
    </div>
  );
}
