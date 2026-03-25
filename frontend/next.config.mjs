import path from "node:path";
import { fileURLToPath } from "node:url";

const frontendRoot = path.dirname(fileURLToPath(import.meta.url));

// Internal viewer defaults to the local FastAPI backend.
// In deploy/proxy environments, absolute base URL handling may need to be revisited.
const backendOrigin = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");

/** @type {import('next').NextConfig} */
const nextConfig = {
  turbopack: {
    root: frontendRoot,
  },
  async rewrites() {
    return [
      {
        source: "/backend-api/:path*",
        destination: `${backendOrigin}/:path*`,
      },
    ];
  },
};

export default nextConfig;
