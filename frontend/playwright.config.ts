import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests/browser",
  timeout: 30000,
  workers: 1,
  use: { baseURL: "http://127.0.0.1:5176", trace: "retain-on-failure" },
  webServer: {
    command: "VITE_SERVICE_MODE=mock npm run dev -- --port 5176",
    url: "http://127.0.0.1:5176",
    reuseExistingServer: true,
  },
  projects: [
    { name: "phone", use: { viewport: { width: 360, height: 800 } } },
    { name: "laptop", use: { viewport: { width: 1440, height: 1000 } } },
  ],
});
