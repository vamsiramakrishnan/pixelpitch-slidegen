import { defineConfig } from "vite";
import { viteSingleFile } from "vite-plugin-singlefile";

export default defineConfig(({ mode }) => ({
  plugins: [viteSingleFile()],
  build: {
    target: "es2022",
    sourcemap: false,
    outDir: mode === "host" ? "dist/host" : "dist",
    rollupOptions: { input: mode === "host" ? "host.html" : "index.html" },
  },
}));
