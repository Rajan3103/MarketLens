// Next.js settings for the MarketLens web app.
//
// The browser only ever talks to this app (http://localhost:3000). Requests to /api/... and /webhook/...
// are forwarded ("rewritten") to the Python backend, so the browser never needs the backend's address
// and there are no cross-site (CORS) problems. Set BACKEND_URL in frontend/.env.local to change it.
import path from "node:path";
import { fileURLToPath } from "node:url";

const BACKEND_URL = (process.env.BACKEND_URL || "http://127.0.0.1:8000").replace(/\/+$/, "");
const HERE = path.dirname(fileURLToPath(import.meta.url));     // the frontend/ folder

/** @type {import('next').NextConfig} */
const nextConfig = {
  // Tailwind CSS through Turbopack (made by create-next-app)
  turbopack: {
    root: HERE,          // this folder is the project (not a stray package-lock.json higher up)
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },

  // Forward the API and webhook addresses to python -m marketlens.api
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${BACKEND_URL}/api/:path*` },
      { source: "/webhook/:path*", destination: `${BACKEND_URL}/webhook/:path*` },
    ];
  },
};

export default nextConfig;
