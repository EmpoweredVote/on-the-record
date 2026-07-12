"use client";

import { useState, useEffect, useCallback } from "react";
import { Header } from "@empoweredvote/ev-ui";

function DarkToggle({ isDark, onToggle }: { isDark: boolean; onToggle: () => void }) {
  return (
    <button
      onClick={onToggle}
      aria-label={isDark ? "Switch to light mode" : "Switch to dark mode"}
      style={{
        width: "32px",
        height: "32px",
        borderRadius: "50%",
        border: "none",
        background: "transparent",
        cursor: "pointer",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        color: isDark ? "#59B0C4" : "#00657c",
        padding: 0,
      }}
    >
      {isDark ? (
        // Sun icon
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" width="18" height="18">
          <path d="M10 2a.75.75 0 01.75.75v1.5a.75.75 0 01-1.5 0v-1.5A.75.75 0 0110 2zM10 15a.75.75 0 01.75.75v1.5a.75.75 0 01-1.5 0v-1.5A.75.75 0 0110 15zM10 7a3 3 0 100 6 3 3 0 000-6zM15.657 5.404a.75.75 0 10-1.06-1.06l-1.061 1.06a.75.75 0 001.06 1.06l1.061-1.06zM6.464 14.596a.75.75 0 10-1.06-1.06l-1.061 1.06a.75.75 0 001.06 1.06l1.061-1.06zM18 10a.75.75 0 01-.75.75h-1.5a.75.75 0 010-1.5h1.5A.75.75 0 0118 10zM5 10a.75.75 0 01-.75.75h-1.5a.75.75 0 010-1.5h1.5A.75.75 0 015 10zM14.596 15.657a.75.75 0 001.06-1.06l-1.06-1.061a.75.75 0 10-1.06 1.06l1.06 1.061zM5.404 6.464a.75.75 0 001.06-1.06l-1.06-1.061a.75.75 0 10-1.06 1.06l1.06 1.061z" />
        </svg>
      ) : (
        // Moon icon
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" width="18" height="18">
          <path fillRule="evenodd" d="M7.455 2.004a.75.75 0 01.26.77 7 7 0 009.958 7.967.75.75 0 011.067.853A8.5 8.5 0 116.647 1.921a.75.75 0 01.808.083z" clipRule="evenodd" />
        </svg>
      )}
    </button>
  );
}

export default function SiteHeader() {
  const [isDark, setIsDark] = useState(false);

  // Publish the sticky header's height so content can stick below it
  // (e.g. .skimBar). The ev-ui Header height varies by breakpoint (~75px
  // desktop / ~67px mobile), so measure it live rather than hardcode.
  useEffect(() => {
    const el = document.querySelector("header");
    if (!el) return;
    const setVar = () =>
      document.documentElement.style.setProperty(
        "--site-header-height",
        `${el.getBoundingClientRect().height}px`
      );
    setVar();
    const ro = new ResizeObserver(setVar);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const read = () => {
      const root = document.documentElement;
      return root.classList.contains("dark") || root.getAttribute("data-theme") === "dark";
    };

    // Initialize: saved preference wins, then fall back to OS. Light must be set
    // explicitly (not by removing data-theme) — on an OS that prefers dark, the
    // `:root:not([data-theme="light"])` dark rule would otherwise still match.
    const saved = localStorage.getItem("theme");
    if (saved === "dark") {
      document.documentElement.setAttribute("data-theme", "dark");
    } else if (saved === "light") {
      document.documentElement.setAttribute("data-theme", "light");
    } else if (window.matchMedia("(prefers-color-scheme: dark)").matches) {
      document.documentElement.setAttribute("data-theme", "dark");
    }
    setIsDark(read());

    const observer = new MutationObserver(() => setIsDark(read()));
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class", "data-theme"],
    });
    return () => observer.disconnect();
  }, []);

  const toggle = useCallback(() => {
    // Always set data-theme to an explicit value. Removing it for light mode
    // leaves the dark `@media (prefers-color-scheme: dark)` rule in force on an
    // OS that prefers dark, so the page would never actually switch to light.
    const theme = isDark ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("theme", theme);
  }, [isDark]);

  return (
    <Header
      logoSrc="/EVLogo.svg"
      navItems={[]}
      darkMode={isDark}
      secondaryAction={<DarkToggle isDark={isDark} onToggle={toggle} />}
      profileMenu={{
        label: null,
        items: [
          { label: "My Account", href: "https://login.empowered.vote/profile" },
          { label: "EV Financials", href: "https://financials.empowered.vote" },
        ],
      }}
    />
  );
}
