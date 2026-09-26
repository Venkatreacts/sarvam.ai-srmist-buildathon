import type { NextConfig } from "next";

const API = process.env.CHAOSLAB_API ?? "http://127.0.0.1:8765";

const nextConfig: NextConfig = {
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${API}/api/:path*` },
      { source: "/runs/:path*", destination: `${API}/runs/:path*` },
    ];
  },
};

export default nextConfig;
