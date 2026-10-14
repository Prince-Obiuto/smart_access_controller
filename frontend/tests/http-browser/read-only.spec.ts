import { expect, test } from "@playwright/test";

test("read-only blocked state is honest and offers no door writes", async ({
  page,
}, info) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/login");
  await page
    .getByLabel("Email address", { exact: true })
    .fill("super_admin@futo.edu.ng");
  await page
    .getByLabel("Password", { exact: true })
    .fill("Synthetic-browser-password!");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Dashboard", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText(
      "Controller event connection unavailable · check the local connection.",
      { exact: true },
    ),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Timed unlock", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByText("Event connection blocked", { exact: true }),
  ).toBeVisible();
  const snapshot = await (await page.request.get("/api/workspace")).json();
  expect(snapshot.controllerMode).toBe("HIKVISION_READ_ONLY");
  expect(snapshot.controllerWritesAvailable).toBe(false);
  expect(
    snapshot.devices.every((device: { online: boolean }) => !device.online),
  ).toBe(true);
  const csrf = (await (await page.request.get("/api/auth/csrf")).json())
    .csrfToken;
  const unlock = await page.request.post("/api/commands", {
    headers: {
      "X-CSRF-Token": csrf,
      "Idempotency-Key": "blocked-unlock-" + info.project.name,
    },
    data: { type: "unlock", doorId: "door-1", confirmed: true },
  });
  expect(unlock.status()).toBe(409);
  await page.getByRole("link", { name: "View activity", exact: true }).click();
  await page
    .getByRole("button", { name: "Access activity", exact: true })
    .click();
  const granted = page.locator("tr").filter({ hasText: "GRANTED" });
  await expect(granted).toContainText("00000123");
  await expect(granted).toContainText("Device reader 1 · mapping unverified");
  const unknown = page
    .locator("tr")
    .filter({ hasText: "INVALID_OR_AMBIGUOUS_TIME" });
  await expect(unknown).toContainText("UNKNOWN");
  await expect(unknown).toContainText("Received:");
  await expect(unknown).toContainText("Device reader 99");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: `test-results/read-only-${info.project.name}.png`,
    fullPage: true,
  });
  expect(errors).toEqual([]);
});
