// Build a deep-link back to the moment a quote was said. For YouTube sources we
// append `&t=<seconds>s` exactly as the publish-quotes pipeline does, so a
// candidate's link matches what ends up on the live quote. Non-YouTube sources
// (files, hls, unknown) link to the source as-is — no fragment.
export function quoteDeepLink(
  sourceUrl: string | null | undefined,
  playbackKind: string | null | undefined,
  seconds: number
): string | null {
  if (!sourceUrl) return null;
  if (playbackKind !== "youtube") return sourceUrl;
  const s = Math.max(0, Math.floor(seconds || 0));
  const sep = sourceUrl.includes("?") ? "&" : "?";
  return `${sourceUrl}${sep}t=${s}s`;
}
