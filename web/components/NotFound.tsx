import Link from "next/link";
export default function NotFound({ message = "Not found." }: { message?: string }) {
  return (
    <main className="notFoundPage">
      <p>{message}</p>
      <Link href="/">← Back to meetings</Link>
    </main>
  );
}
