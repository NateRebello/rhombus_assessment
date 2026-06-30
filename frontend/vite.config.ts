import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 3000,
    // Polling is required for Vite to detect file changes from a Windows host
    // through Docker Desktop volume mounts (inotify events are not forwarded).
    watch: { usePolling: true, interval: 300 },
    // Proxy API calls to Django so CORS is not an issue during development.
    // VITE_API_PROXY_TARGET is the internal Docker service URL (http://web:8000).
    // VITE_API_BASE_URL is the public host URL used by the browser — NOT suitable
    // as a proxy target when running inside Docker because localhost from inside
    // the frontend container refers to the frontend container itself, not Django.
    proxy: {
      "/api": {
        target: process.env.VITE_API_PROXY_TARGET ?? process.env.VITE_API_BASE_URL ?? "http://web:8000",
        changeOrigin: true,
      },
    },
  },
});
