import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests/http-browser",
  timeout: 45000,
  workers: 1,
  use: { baseURL: "http://127.0.0.1:5175", trace: "off" },
  webServer: [
    {
      command:
        "../backend/.venv/bin/python ../backend/scripts/browser_server.py",
      url: "http://127.0.0.1:8001/api/health",
      reuseExistingServer: false,
      timeout: 120000,
    },
    {
      command:
        "VITE_SERVICE_MODE=http VITE_API_PROXY_TARGET=http://127.0.0.1:8001 npm run dev -- --port 5175",
      url: "http://127.0.0.1:5175",
      reuseExistingServer: false,
    },
  ],
  projects: [
    { name: "phone", use: { viewport: { width: 360, height: 800 } } },
    { name: "laptop", use: { viewport: { width: 1440, height: 1000 } } },
  ],
});
