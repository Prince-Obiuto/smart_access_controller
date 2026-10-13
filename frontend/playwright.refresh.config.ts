import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/refresh-browser",
  timeout: 45000,
  workers: 1,
  reporter: [
    ["list"],
    ["json", { outputFile: "test-results/refresh-results.json" }],
  ],
  use: { baseURL: "http://127.0.0.1:5177", trace: "retain-on-failure" },
  webServer: {
    command: "VITE_SERVICE_MODE=http npm run dev -- --port 5177 --strictPort",
    url: "http://127.0.0.1:5177",
    reuseExistingServer: false,
  },
  projects: [
    { name: "phone", use: { viewport: { width: 360, height: 800 } } },
    { name: "laptop", use: { viewport: { width: 1440, height: 1000 } } },
  ],
});
