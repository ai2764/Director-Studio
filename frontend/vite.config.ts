import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const backendUrl = process.env.DS_BACKEND_URL?.trim() || "http://127.0.0.1:8790";

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    allowedHosts: true,
    proxy: {
      "/api": {
        target: backendUrl,
        changeOrigin: true,
      },
    },
  },
});

