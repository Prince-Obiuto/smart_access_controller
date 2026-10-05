import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";

test("durable registration, uploads, authenticated roles, events, reload and API process restart", async ({
  page,
}, info) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const email = `connected-${info.project.name}@futo.edu.ng`;
  await page.goto("/register");
  await page
    .getByLabel("Registration number / staff ID")
    .fill("CONNECTED-" + info.project.name);
  await page.getByLabel("First name", { exact: true }).fill("Connected");
  await page.getByLabel("Last name", { exact: true }).fill(info.project.name);
  await page.getByLabel("University email").fill(email);
  await page.getByLabel("Phone", { exact: true }).fill("08000000000");
  await page.getByLabel("Department", { exact: true }).fill("IFT");
  await page
    .getByLabel("Photo", { exact: true })
    .setInputFiles("../backend/var/browser-photo.png");
  await page
    .getByLabel("Password", { exact: true })
    .fill("Synthetic-browser-password!");
  await page
    .getByRole("button", { name: "Create account", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "My Access", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Controller integration is simulated.", { exact: true }),
  ).toBeVisible();
  await expect(page.getByLabel("Development identity")).toHaveCount(0);
  await page.getByRole("button", { name: "Request first card" }).click();
  await page
    .getByLabel("Payment evidence", { exact: true })
    .setInputFiles("../backend/var/browser-photo.png");
  await page.getByRole("button", { name: "Submit for review" }).click();
  await expect(
    page.locator(".badge").filter({ hasText: /^Submitted$/ }),
  ).toBeVisible();
  await page.reload();
  await expect(
    page.locator(".badge").filter({ hasText: /^Submitted$/ }),
  ).toBeVisible();
  const before = await (await page.request.get("/api/workspace")).json();
  // Restart the actual Uvicorn child, leaving PostgreSQL and private upload storage intact.
  process.kill(
    Number(readFileSync("../backend/var/browser-api.pid", "utf8")),
    "SIGUSR1",
  );
  await page.waitForTimeout(2000);
  await expect
    .poll(async () => {
      try {
        return (await page.request.get("/api/health")).status();
      } catch {
        return 0;
      }
    })
    .toBe(200);
  await page.reload();
  await expect(
    page.locator(".badge").filter({ hasText: /^Submitted$/ }),
  ).toBeVisible();
  const after = await (await page.request.get("/api/workspace")).json();
  expect(after.identity.id).toBe(before.identity.id);
  expect(after.applications[0].evidence.url).toBe(
    before.applications[0].evidence.url,
  );
  expect((await page.request.get(after.people[0].photo)).status()).toBe(200);
  expect(
    (await page.request.get(after.applications[0].evidence.url)).status(),
  ).toBe(200);
  expect(await page.evaluate(() => Object.keys(localStorage))).toEqual([]);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: `test-results/http-${info.project.name}-application.png`,
    fullPage: true,
  });
  const csrf = async () =>
    (await (await page.request.get("/api/auth/csrf")).json()).csrfToken;
  await page.request.post("/api/auth/logout", {
    headers: { "X-CSRF-Token": await csrf() },
    data: {},
  });
  await page.goto("/login");
  await page
    .getByLabel("Email address", { exact: true })
    .fill("manager@futo.edu.ng");
  await page
    .getByLabel("Password", { exact: true })
    .fill("Synthetic-browser-password!");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Dashboard", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Timed unlock", exact: true }),
  ).toHaveCount(0);
  await page.goto("/requests");
  const request = page
    .locator(".panel")
    .filter({
      has: page.getByRole("heading", {
        name: `Connected ${info.project.name}`,
        exact: true,
      }),
    });
  await request.getByRole("button", { name: "Approve payment" }).click();
  await expect(
    request.locator(".badge").filter({ hasText: /^Approved$/ }),
  ).toBeVisible();
  await expect(
    request.getByRole("button", { name: "Issue card", exact: true }),
  ).toHaveCount(0);
  const denied = await page.request.post("/api/commands", {
    headers: {
      "X-CSRF-Token": await csrf(),
      "Idempotency-Key": "denied-" + info.project.name,
    },
    data: { type: "issue", id: before.applications[0].id, number: "00112233" },
  });
  expect(denied.status()).toBe(403);
  await page.goto("/cards-activity");
  await expect(
    page.getByRole("button", { name: "CSV", exact: true }),
  ).toHaveCount(0);
  // A separately authenticated SA stores an event. Subscription polling updates this UI without reload.
  const { request: api } = await import("@playwright/test");
  const sa = await api.newContext({ baseURL: "http://127.0.0.1:5175" });
  const token = async () =>
    (await (await sa.get("/api/auth/csrf")).json()).csrfToken;
  expect(
    (
      await sa.post("/api/auth/login", {
        headers: { "X-CSRF-Token": await token() },
        data: {
          email: "super_admin@futo.edu.ng",
          password: "Synthetic-browser-password!",
        },
      })
    ).status(),
  ).toBe(200);
  expect(
    (
      await sa.post("/api/commands", {
        headers: {
          "X-CSRF-Token": await token(),
          "Idempotency-Key": "event-" + info.project.name,
        },
        data: { type: "demo-event", event: "UNKNOWN" },
      })
    ).status(),
  ).toBe(200);
  await expect(page.getByText("0000000000").first()).toBeVisible({
    timeout: 12000,
  });
  await sa.dispose();
  if (info.project.name === "phone") {
    await page.getByRole("button", { name: "Open navigation" }).click();
    await expect(
      page.getByRole("dialog", { name: "Navigation menu" }),
    ).toBeVisible();
    await page
      .getByRole("dialog")
      .getByRole("link", { name: "My Access", exact: true })
      .click();
    await expect(
      page.getByRole("heading", { name: "My Access", exact: true }),
    ).toBeVisible();
  }
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  expect(errors).toEqual([]);
});
