"use client";

import type { SyntheticEvent } from "react";

import { faviconUrl, sourceGradient, thumbSrcSet, thumbUrl } from "@/lib/format";

type Props = {
  imageUrl: string | null;
  sourceName: string;
  sourceDomain: string | null;
  sizes: string;
  /** Above-the-fold covers load eagerly so they don't delay LCP. */
  eager?: boolean;
  className?: string;
};

/**
 * Fixed-ratio cover. The source favicon on a gradient sits underneath the image: when
 * the image is missing it is all there is, and when it fails to load the <img> is
 * hidden (Chrome would otherwise draw a broken-image icon) and the fallback shows.
 */
function hide(img: HTMLImageElement) {
  img.style.display = "none";
}

/** Also catches images that failed before hydration, when onError was not attached yet. */
function hideIfBroken(img: HTMLImageElement | null) {
  if (img?.complete && img.naturalWidth === 0) hide(img);
}

const onError = (e: SyntheticEvent<HTMLImageElement>) => hide(e.currentTarget);

export function Cover({ imageUrl, sourceName, sourceDomain, sizes, eager, className }: Props) {
  return (
    <div
      className={["relative aspect-[1.91] overflow-hidden", className].filter(Boolean).join(" ")}
      style={{ backgroundImage: sourceGradient(sourceName) }}
    >
      <div className="absolute inset-0 flex items-center justify-center">
        {sourceDomain ? (
          // eslint-disable-next-line @next/next/no-img-element -- no next/image optimization (Hobby quota)
          <img
            src={faviconUrl(sourceDomain)}
            alt=""
            width={32}
            height={32}
            loading="lazy"
            decoding="async"
            className="size-8 rounded-md bg-white/90 p-1 shadow"
          />
        ) : (
          <span className="text-2xl font-bold text-white/90">{sourceName.slice(0, 1)}</span>
        )}
      </div>
      {imageUrl ? (
        // eslint-disable-next-line @next/next/no-img-element -- resized by wsrv.nl instead of next/image
        <img
          src={thumbUrl(imageUrl, 480)}
          srcSet={thumbSrcSet(imageUrl)}
          sizes={sizes}
          alt=""
          loading={eager ? "eager" : "lazy"}
          fetchPriority={eager ? "high" : undefined}
          decoding="async"
          referrerPolicy="no-referrer"
          ref={hideIfBroken}
          onError={onError}
          className="absolute inset-0 size-full object-cover"
        />
      ) : null}
    </div>
  );
}
