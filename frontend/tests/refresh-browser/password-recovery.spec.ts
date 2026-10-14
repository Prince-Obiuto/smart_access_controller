import { expect, test } from "@playwright/test";

test("forgot and reset forms protect links and require normal login", async ({
  page,
}) => {
  let resets = 0;
  const requests: Record<string, unknown>[] = [];
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/auth/me") return route.fulfill({ json: null });
    if (path === "/api/auth/csrf")
      return route.fulfill({ json: { csrfToken: "fixture" } });
    if (path === "/api/auth/recovery-options")
      return route.fulfill({
        json: { available: true, contact: "Contact library desk." },
      });
    if (path === "/api/auth/forgot-password") {
      requests.push(route.request().postDataJSON());
      return route.fulfill({
        json: { message: "If eligible, recovery email will be attempted." },
      });
    }
    if (path === "/api/auth/reset-password") {
      resets++;
      requests.push(route.request().postDataJSON());
      return route.fulfill({
        json: { message: "Password changed. Sign in with your new password." },
      });
    }
    throw new Error(`Unexpected request ${path}`);
  });
  await page.goto("/login");
  await page.getByRole("link", { name: "Forgot password?" }).click();
  await page
    .getByLabel("Registered email address")
    .fill("visitor@example.test");
  await page.getByRole("button", { name: "Request recovery email" }).click();
  await expect(page.getByRole("status")).toContainText("If eligible");
  await page.goto("/reset-password#token=synthetic-single-use-token");
  await expect(
    page.getByRole("heading", { name: "Reset password" }),
  ).toBeVisible();
  expect(page.url()).not.toContain("token=");
  expect(resets).toBe(0); // Merely opening a link cannot consume it.
  await page
    .getByLabel("New password", { exact: true })
    .fill("A-long-fixture-password!");
  await page
    .getByLabel("Confirm new password")
    .fill("Different-password-fixture!");
  await page.getByRole("button", { name: "Reset password" }).click();
  await expect(page.getByRole("alert")).toContainText("Matching passwords");
  expect(resets).toBe(0);
  await page
    .getByLabel("Confirm new password")
    .fill("A-long-fixture-password!");
  await page.getByRole("button", { name: "Reset password" }).click();
  await expect(page.getByRole("status")).toContainText("Sign in");
  expect(resets).toBe(1);
  expect(requests[1].token).toBe("synthetic-single-use-token");
  expect(
    await page.locator('meta[name="referrer"]').getAttribute("content"),
  ).toBe("no-referrer");
  expect(
    await page.evaluate(() =>
      Object.keys(localStorage).concat(Object.keys(sessionStorage)),
    ),
  ).toEqual([]);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.getByRole("link", { name: "Back to sign in" }).click();
  await expect(
    page.getByRole("heading", { name: "Welcome back" }),
  ).toBeVisible();
});

test("unavailable email provides contact-admin path", async ({ page }) => {
  await page.route("**/api/**", (route) =>
    route.fulfill({
      json: route.request().url().endsWith("/auth/me")
        ? null
        : { available: false, contact: "Contact library desk in person." },
    }),
  );
  await page.goto("/forgot-password");
  await expect(page.getByRole("status")).toHaveText(
    "Email recovery is unavailable.",
  );
  await expect(
    page.getByRole("button", { name: "Request recovery email" }),
  ).toHaveCount(0);
  await page.getByRole("link", { name: "Contact administrator" }).click();
  await expect(
    page.getByRole("heading", { name: "Contact administrator" }),
  ).toBeVisible();
});
