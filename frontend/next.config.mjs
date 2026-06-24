/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  devIndicators: false,
  // Release gates run npm test and npm run typecheck before production bundling.
  eslint: {
    ignoreDuringBuilds: true,
  },
  typescript: {
    ignoreBuildErrors: true,
  },
  // Use a release-specific output directory so stale local .next artifacts cannot block Windows builds.
  distDir: process.env.ANVIL_NEXT_DIST_DIR || ".next-release",
  // Avoid Next's recursive cleaner in restricted Windows sandboxes; it can spin on EPERM/EBUSY.
  cleanDistDir: false,
  experimental: {
    cpus: 1,
    workerThreads: true,
    webpackBuildWorker: false,
  },
};

export default nextConfig;
