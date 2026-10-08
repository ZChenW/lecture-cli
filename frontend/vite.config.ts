import { defineConfig } from "vitest/config";
import { svelte } from "@sveltejs/vite-plugin-svelte";

// The backend serves this build with a strict Content-Security-Policy: everything must be
// a same-origin file, so nothing is inlined into index.html or turned into a data: URL.
export default defineConfig(({ mode }) => ({
  plugins: [svelte()],
  base: "./",
  // Component tests mount Svelte in jsdom, which needs Svelte's browser build.
  resolve: mode === "test" ? { conditions: ["browser"] } : undefined,
  build: {
    outDir: "../lecture_cli/gui/static",
    emptyOutDir: true,
    assetsInlineLimit: 0,
    modulePreload: { polyfill: false },
    cssCodeSplit: false,
    rollupOptions: {
      // Fonts sit next to their OFL licence files (copied from public/fonts).
      output: { assetFileNames: (asset) => asset.names[0]?.endsWith(".woff2") ? "fonts/[name]-[hash][extname]" : "assets/[name]-[hash][extname]" },
    },
  },
  test: {
    include: ["src/**/*.test.ts"],
  },
}));
