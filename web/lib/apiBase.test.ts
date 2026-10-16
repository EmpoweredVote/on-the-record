import { afterEach, describe, expect, it, vi } from "vitest";
import { apiBase } from "./apiBase";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("apiBase", () => {
  it("uses the configured URL outside development, without a trailing slash", () => {
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("NEXT_PUBLIC_EV_ACCOUNTS_URL", "https://api.test/");
    expect(apiBase()).toBe("https://api.test");
  });

  it("is empty outside development when nothing is configured", () => {
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("NEXT_PUBLIC_EV_ACCOUNTS_URL", "");
    expect(apiBase()).toBe("");
  });

  it("uses the dev server's own origin in the browser during development", () => {
    vi.stubEnv("NODE_ENV", "development");
    vi.stubEnv("NEXT_PUBLIC_EV_ACCOUNTS_URL", "https://ev-accounts.onrender.com");
    vi.stubGlobal("window", { location: { origin: "http://localhost:3000" } });
    expect(apiBase()).toBe("http://localhost:3000");
  });

  it("falls back to the configured URL during development when there is no window", () => {
    vi.stubEnv("NODE_ENV", "development");
    vi.stubEnv("NEXT_PUBLIC_EV_ACCOUNTS_URL", "https://api.test");
    expect(apiBase()).toBe("https://api.test");
  });
});
