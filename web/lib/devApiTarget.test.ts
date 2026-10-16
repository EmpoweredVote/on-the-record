import { describe, expect, it } from "vitest";
import { devApiTarget, LIVE_API_ORIGIN } from "./devApiTarget";

describe("devApiTarget", () => {
  it("defaults to the live API", () => {
    expect(devApiTarget({})).toBe(LIVE_API_ORIGIN);
  });

  it("EV_API_PROXY_TARGET wins, without a trailing slash", () => {
    expect(devApiTarget({ EV_API_PROXY_TARGET: "http://localhost:3000/" })).toBe("http://localhost:3000");
  });

  it("keeps a local backend already set in NEXT_PUBLIC_EV_ACCOUNTS_URL", () => {
    expect(devApiTarget({ NEXT_PUBLIC_EV_ACCOUNTS_URL: "http://localhost:3000" })).toBe("http://localhost:3000");
    expect(devApiTarget({ NEXT_PUBLIC_EV_ACCOUNTS_URL: "http://127.0.0.1:5050/" })).toBe("http://127.0.0.1:5050");
  });

  it("ignores a non-local NEXT_PUBLIC_EV_ACCOUNTS_URL", () => {
    expect(devApiTarget({ NEXT_PUBLIC_EV_ACCOUNTS_URL: "https://ev-accounts.onrender.com" })).toBe(LIVE_API_ORIGIN);
  });
});
