import type { SVGProps } from "react";

// Small stroke icons (24×24 grid) for the card actions. Decorative: buttons carry labels.

type Props = SVGProps<SVGSVGElement> & { filled?: boolean };

function Svg({ filled, children, ...rest }: Props) {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      fill={filled ? "currentColor" : "none"}
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      {...rest}
    >
      {children}
    </svg>
  );
}

export const UpIcon = (p: Props) => (
  <Svg {...p}>
    <path d="M7 10v11H3V10zM7 10l4-8a3 3 0 0 1 3 3v4h5.5a2 2 0 0 1 2 2.3l-1.3 8a2 2 0 0 1-2 1.7H7" />
  </Svg>
);

export const DownIcon = (p: Props) => (
  <Svg {...p}>
    <path d="M17 14V3h4v11zM17 14l-4 8a3 3 0 0 1-3-3v-4H4.5a2 2 0 0 1-2-2.3l1.3-8a2 2 0 0 1 2-1.7H17" />
  </Svg>
);

export const BookmarkIcon = (p: Props) => (
  <Svg {...p}>
    <path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z" />
  </Svg>
);

export const HideIcon = (p: Props) => (
  <Svg {...p}>
    <path d="M9.9 4.2A10 10 0 0 1 12 4c5 0 9 4.5 10 8a13 13 0 0 1-2.2 3.8M6.6 6.6A13 13 0 0 0 2 12c1 3.5 5 8 10 8a10 10 0 0 0 5.4-1.6M3 3l18 18M9.9 9.9a3 3 0 0 0 4.2 4.2" />
  </Svg>
);

export const MoreIcon = (p: Props) => (
  <Svg {...p}>
    <circle cx="5" cy="12" r="1" />
    <circle cx="12" cy="12" r="1" />
    <circle cx="19" cy="12" r="1" />
  </Svg>
);

export const RefreshIcon = (p: Props) => (
  <Svg {...p}>
    <path d="M21 12a9 9 0 1 1-2.6-6.4M21 3v6h-6" />
  </Svg>
);
