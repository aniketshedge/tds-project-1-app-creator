import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

function normalizeBasePath(rawPath) {
  const candidate = (rawPath || "/").trim();
  if (!candidate || candidate === "/") {
    return "/";
  }
  const withLeadingSlash = candidate.startsWith("/") ? candidate : `/${candidate}`;
  return withLeadingSlash.endsWith("/") ? withLeadingSlash : `${withLeadingSlash}/`;
}

const basePath = normalizeBasePath(process.env.VITE_APP_BASE_PATH || "/");
const apiPrefix = `${basePath}api`;
const previewPrefix = `${basePath}preview`;

export default defineConfig({
  base: basePath,
  plugins: [vue()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    proxy: {
      [apiPrefix]: {
        target: process.env.VITE_API_TARGET || "http://localhost:8000",
        changeOrigin: true,
      },
      [previewPrefix]: {
        target: process.env.VITE_API_TARGET || "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
