/** @type {import('next').NextConfig} */
const nextConfig = {
  // The container image runs the self-contained server from .next/standalone (see Dockerfile).
  output: "standalone",
  typescript: {
    ignoreBuildErrors: true,
  },
  images: {
    unoptimized: true,
  },
}

export default nextConfig
