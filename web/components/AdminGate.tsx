"use client";
import { useEffect, useState } from "react";
import { login, getToken, logout, AuthError } from "@/lib/adminAuth";

export default function AdminGate({ children }: { children: React.ReactNode }) {
  const [ready, setReady] = useState(false);
  const [authed, setAuthed] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setAuthed(!!getToken());
    setReady(true);
  }, []);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email, password);
      setAuthed(true);
    } catch (err) {
      setError(
        err instanceof AuthError && err.code === "INVALID_CREDENTIALS"
          ? "Invalid email or password."
          : "Could not reach the server. Try again."
      );
    } finally {
      setBusy(false);
    }
  }

  if (!ready) return null;

  if (!authed) {
    return (
      <div className="adminLogin">
        <h1>Admin sign in</h1>
        <form onSubmit={onSubmit}>
          <label>Email<input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required /></label>
          <label>Password<input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required /></label>
          {error ? <p className="adminError">{error}</p> : null}
          <button type="submit" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
        </form>
      </div>
    );
  }

  return (
    <>
      <div className="adminBar">
        <span>Admin</span>
        <button onClick={() => { logout(); setAuthed(false); }}>Sign out</button>
      </div>
      {children}
    </>
  );
}
