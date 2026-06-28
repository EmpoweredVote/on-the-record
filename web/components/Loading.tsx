export default function Loading({ label = "Loading…" }: { label?: string }) {
  return <p className="loadingState" role="status" aria-live="polite">{label}</p>;
}
