import type { NextConfig } from "next";
import { PHASE_DEVELOPMENT_SERVER } from "next/constants";
import { devApiTarget } from "./lib/devApiTarget";

// Local dev only: forward /api/* to the API (live by default, see
// lib/devApiTarget.ts), so the browser makes same-origin calls and never meets
// the live API's CORS allow-list (see lib/apiBase.ts). `output: "export"` does
// not allow rewrites in a build, so only the dev server gets them.
export default function nextConfig(phase: string): NextConfig {
  const config: NextConfig = {
    output: "export",
    trailingSlash: true,
  };
  if (phase !== PHASE_DEVELOPMENT_SERVER) return config;

  const target = devApiTarget(process.env);
  console.log(`[on-the-record] dev: /api/* → ${target}`);
  return {
    ...config,
    async rewrites() {
      return [{ source: "/api/:path*", destination: `${target}/api/:path*` }];
    },
  };
}
