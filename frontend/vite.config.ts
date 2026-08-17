import path from "node:path";
import tailwindcss from "@tailwindcss/vite";
import { tanstackRouter } from "@tanstack/router-plugin/vite";
import react from "@vitejs/plugin-react-swc";
import { defineConfig } from "vite";

// Where to send API requests during local dev. Defaults to the backend
// running directly on the host (bare-metal `uv run uvicorn ...`); the
// docker-compose `frontend` service overrides this to `http://core:8000`
// (the Docker network hostname) since `localhost` inside that container
// would refer to the container itself, not the `core` service.
const apiProxyTarget = process.env.VITE_API_PROXY_TARGET ?? "http://localhost:8000";

// server.app:app's routes are unprefixed (no shared "/api" base), so the
// dev-server proxy has to match each route group explicitly rather than a
// single prefix rewrite.
const apiRoutes = [
  "/capabilities",
  "/health",
  "/effects",
  "/render",
  "/mask-preview",
  "/mask",
  "/jobs",
  "/library",
  "/projects",
  "/generate",
  "/assemble",
];

export default defineConfig({
  plugins: [tanstackRouter({ target: "react", autoCodeSplitting: true }), react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(import.meta.dirname, "./src"),
    },
  },
  server: {
    proxy: Object.fromEntries(apiRoutes.map((route) => [route, apiProxyTarget])),
  },
  build: {
    outDir: "dist",
  },
});
