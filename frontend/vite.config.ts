import path from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Dev proxy: forward API calls to the FastAPI service so the SPA and API share an origin.
export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": path.resolve(__dirname, "src") } },
  server: {
    port: Number(process.env.PORT) || 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ""),
      },
    },
  },
});
