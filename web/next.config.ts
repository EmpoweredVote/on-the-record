import type { NextConfig } from "next";

// Local dev only: forward /api/* to the live ev-accounts API, so the browser
// makes same-origin calls and never meets the API's CORS allow-list (see
// lib/apiBase.ts). Set EV_API_PROXY_TARGET to use a local backend instead.
// `output: "export"` does not allow rewrites in a build, so production gets none.
const isDev = process.env.NODE_ENV === "development";
const devApiTarget = (process.env.EV_API_PROXY_TARGET || "https://accounts-api.empowered.vote").replace(/\/$/, "");

const nextConfig: NextConfig = {
  output: "export",
  trailingSlash: true,
  ...(isDev && {
    async rewrites() {
      return [{ source: "/api/:path*", destination: `${devApiTarget}/api/:path*` }];
    },
  }),
};

export default nextConfig;
