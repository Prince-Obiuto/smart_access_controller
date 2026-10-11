import { defineConfig } from "@playwright/test";
import connected from "./playwright.http.config";

export default defineConfig({
  ...connected,
  testMatch: "controlled.spec.ts",
  webServer: Array.isArray(connected.webServer)
    ? connected.webServer.map((server, index) => ({
        ...server,
        ...(index === 0
          ? { env: { IFT_BROWSER_CONTROLLER_MODE: "HIKVISION_CONTROLLED" } }
          : {}),
      }))
    : connected.webServer,
});
