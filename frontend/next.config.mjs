// The browser only talks to Next.js; /api/* is proxied to the FastAPI backend.
// BACKEND_URL is read at build time (rewrites are baked in), so pass it as a build arg in Docker.
const backend = process.env.BACKEND_URL || "http://localhost:8000";

// Basic security headers for every page. (A Content-Security-Policy is not included: Next.js needs per-request nonces for it, so add one
// only after testing it on a staging copy.)
const securityHeaders = [
  { key: "X-Frame-Options", value: "DENY" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
  // Browsers ignore this over plain http (local development) and apply it on https (Railway).
  { key: "Strict-Transport-Security", value: "max-age=31536000" },
];

/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  poweredByHeader: false, // do not announce "X-Powered-By: Next.js"
  // The dev/prod proxy gives up on slow backend calls after about 30 s ("Internal Server Error"). Importing hundreds of
  // skills through an LLM can take longer, so allow 10 minutes. (If your Next.js version rejects this key it only warns.)
  // NOTE: Railway's own proxy in front of the app may close a long request sooner; keep long jobs as background jobs with polling.
  experimental: { proxyTimeout: 600000 },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
  },
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
  // NOTE: uploads pass through this rewrite proxy, which buffers request bodies up to ~10 MB by default
  // (backend MAX_UPLOAD_MB defaults to 10 to match). For larger files, call the backend directly instead.
};

export default nextConfig;
