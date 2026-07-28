// Bloomington city legislation pages — the official ordinance/resolution text
// behind an agenda item's legislation_ref:
//   https://bloomington.in.gov/council/legislation/{Type}/{year}/{year}-{NN}
// Mirrors the pipeline's src/legislation_oracle.py build_legislation_url:
// the type path comes from the landing page's fixed list and the number is
// zero-padded to two digits (2026-01 is a page; 2026-1 is a 404).
//
// Pending legislation has no page yet (the URL 404s until the city records a
// final action) — callers gate rendering on the item having happened.
const BASE_URL = "https://bloomington.in.gov/council/legislation";

const TYPE_PATHS: Record<string, string> = {
  ordinance: "Ordinance",
  resolution: "Resolution",
  "appropriation ordinance": "Appropriation%20Ordinance",
};

const REF_RE = /^\s*(.+?)\s+(\d{4})-(\d+)\s*$/;

export function legislationUrl(legislationRef: string | null): string | null {
  if (!legislationRef) return null;
  const m = REF_RE.exec(legislationRef);
  if (!m) return null;
  const kind = m[1].replace(/\s+/g, " ").trim().toLowerCase();
  const path = TYPE_PATHS[kind];
  if (!path) return null;
  const year = m[2];
  const number = String(Number(m[3])).padStart(2, "0");
  return `${BASE_URL}/${path}/${year}/${year}-${number}`;
}
