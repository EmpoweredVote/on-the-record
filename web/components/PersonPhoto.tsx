"use client";

import { useState } from "react";

// First + last initial (e.g. "Nithya Raman" -> "NR"). Falls back to a single
// letter for mononyms.
function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "";
  if (parts.length === 1) return parts[0].charAt(0).toUpperCase();
  const first = parts[0].charAt(0);
  const last = parts[parts.length - 1].charAt(0);
  return (first + last).toUpperCase();
}

// Renders a person's headshot, falling back to their initial when there is no
// URL or when the image fails to load. Upstream headshot data sometimes holds a
// source/citation page URL rather than an image, so onError matters here.
export default function PersonPhoto({
  name,
  url,
  large = false,
}: {
  name: string;
  url: string | null;
  large?: boolean;
}) {
  const [failed, setFailed] = useState(false);
  const cls = large ? "personPhoto large" : "personPhoto";

  if (url && !failed) {
    // Static export has no image optimizer; an intentional <img> is correct here.
    // eslint-disable-next-line @next/next/no-img-element
    return (
      <img
        className={cls}
        src={url}
        alt=""
        loading="lazy"
        onError={() => setFailed(true)}
      />
    );
  }

  return (
    <span className={`${cls} personPhotoFallback`} aria-hidden>
      {initials(name)}
    </span>
  );
}
