import { expect, test } from "@playwright/test";
import { seed } from "../../src/services/fixtures";

// Synthetic HTTP only: no owner session, database or controller command.
test("reader scan stays open and preserves confirmation when dialog closes", async ({ page }) => {
  const initial = seed(new Date().toISOString());
  const snapshot = {
    ...initial, identity: initial.identities.find((i) => i.role === "SUPER_ADMIN")!,
    applications: [{ ...initial.applications[0], state: "APPROVED" }],
    clock: new Date().toISOString(),
    schedule: { weekdays: [1, 2, 3, 4, 5], opens: "07:00", closes: "18:30", exceptions: [], sync: "PENDING" },
    settings: { payment: "Synthetic", uploadLimit: 1024 },
    controls: { offline: false, failDisable: false, delayActivation: false, unlockTimeout: false },
    controllerMode: "HIKVISION_READ_ONLY", controllerWritesAvailable: false,
    readerEnrolmentAvailable: true, controllerReadStatus: "CONNECTED",
  };
  let starts = 0, cancellations = 0, detected = false;
  let expiresAt = "";
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const method = route.request().method();
    const respond = (json: unknown, status = 200) => route.fulfill({ json, status });
    if (path === "/api/auth/me") return respond(snapshot.identity);
    if (path === "/api/workspace") return respond({ ...snapshot, clock: new Date().toISOString() });
    if (path === "/api/auth/csrf") return respond({ csrfToken: "synthetic" });
    if (path === "/api/enrolment-sessions" && method === "POST") {
      if (++starts === 1) return respond({ code: "NOT_CONNECTED", message: "Fresh controller communication is required." }, 409);
      expiresAt = new Date(Date.now() + 120000).toISOString();
      return respond({ id: "synthetic-reader-scan", state: "WAITING", expiresAt });
    }
    if (path.endsWith("/cancel")) { cancellations++; return respond({ ok: true }); }
    if (path.endsWith("/confirm")) return respond("00009999");
    if (path === "/api/enrolment-sessions/synthetic-reader-scan" && method === "GET")
      return respond({ id: "synthetic-reader-scan", state: detected ? "DETECTED" : "WAITING", expiresAt, ...(detected ? { number: "00009999" } : {}) });
    throw new Error(`Unexpected acceptance-test request: ${method} ${path}`);
  });
  await page.goto("/requests");
  await page.getByRole("button", { name: "Issue card", exact: true }).click();
  const dialog = page.getByRole("dialog");
  const start = dialog.getByRole("button", { name: "Scan real entrance reader" });
  await start.click();
  await expect(dialog.getByRole("alert")).toHaveText("Fresh controller communication is required.");
  await start.click();
  await expect(dialog.getByText(/WAITING/)).toBeVisible();
  await page.waitForTimeout(5500); // Covers the previous immediate cancellation and a workspace poll.
  await expect(dialog.getByText(/WAITING/)).toBeVisible();
  expect(cancellations).toBe(0);
  detected = true;
  await dialog.getByRole("button", { name: "Use confirmed card number" }).click();
  await expect(dialog.getByLabel("Card number", { exact: true })).toHaveValue("00009999");
  await expect(dialog.getByText(/CONFIRMED/)).toBeVisible();
  await dialog.getByRole("button", { name: "Close dialog" }).click();
  await page.waitForTimeout(250);
  expect(cancellations).toBe(0);
});
