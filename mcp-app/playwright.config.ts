import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "tests",
  outputDir: "../.tmp/mcp-app-browser",
  timeout: 45000,
  workers: 1,
  use: { baseURL: "http://127.0.0.1:18092", viewport: { width: 1100, height: 1100 }, trace: "retain-on-failure" },
  webServer: {
    command: "uv run --project .. --extra mcp-app python ../tests/fixtures/mcp_preview.py --db ../.tmp/mcp-browser-jobs.sqlite",
    url: "http://127.0.0.1:18092/health",
    reuseExistingServer: false,
    timeout: 30000,
  },
});
