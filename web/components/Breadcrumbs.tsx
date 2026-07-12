import Link from "next/link";

export interface Crumb {
  label: string;
  /** Omit on the last (current) crumb — it renders as plain text. */
  href?: string;
}

/**
 * Hierarchical trail shown at the top of a page so any page can walk back up
 * to Meetings (the site root). Replaces the ad-hoc "← All X" back-links.
 * The last crumb is the current page and is not a link.
 */
export default function Breadcrumbs({ items }: { items: Crumb[] }) {
  return (
    <nav className="breadcrumbs" aria-label="Breadcrumb">
      <ol>
        {items.map((c, i) => {
          const last = i === items.length - 1;
          return (
            <li key={i}>
              {c.href && !last ? (
                <Link href={c.href}>{c.label}</Link>
              ) : (
                <span aria-current={last ? "page" : undefined}>{c.label}</span>
              )}
              {!last && (
                <span className="breadcrumbSep" aria-hidden="true">
                  /
                </span>
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
