import type { NextConfig } from "next";

// M-5 fix: proxy /api/* to the FastAPI backend so frontend code never needs
// hardcoded hostnames. Falls back to localhost:8000 in development; set
// NEXT_PUBLIC_API_URL in production (e.g. "https://grader.example.com").
const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

const nextConfig: NextConfig = {
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${API_URL}/:path*`,
      },
    ];
  },
};

export default nextConfig;
