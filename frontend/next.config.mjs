/** @type {import('next').NextConfig} */
const config = {
  output: "export",
  poweredByHeader: false,
  images: { unoptimized: true },
  reactStrictMode: true,
  // Use process workers for static export.
  experimental: { cpus: 2, workerThreads: false },
};
export default config;
