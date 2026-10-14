import { expect, test, type Page } from "@playwright/test";
import { writeFileSync } from "node:fs";
import { seed } from "../../src/services/fixtures";
import type { AccessEvent, Snapshot } from "../../src/models";

// Synthetic HTTP responses exercise the real HTTP UI/polling path. No backend,
// database, owner session or controller command is involved in these tests.
async function fixture(page: Page) {
  const initial = seed("2026-10-12T12:00:00Z");
  const snapshot: Snapshot = {
    ...initial,
    identity: initial.identities.find((i) => i.role === "SUPER_ADMIN")!,
    clock: "2026-10-12T12:00:00Z",
    schedule: {
      weekdays: [1, 2, 3, 4, 5],
      opens: "08:00",
      closes: "18:00",
      exceptions: [],
      sync: "PENDING",
    },
    settings: { payment: "Synthetic", uploadLimit: 1024 },
    controls: {
      offline: false,
      failDisable: false,
      delayActivation: false,
      unlockTimeout: false,
    },
    controllerMode: "HIKVISION_CONTROLLED",
    controllerWritesAvailable: false,
    controllerReadStatus: "CONNECTED",
    controllerHistoryStatus: "OK",
    controllerLiveStatus: "OK",
  };
  const base: AccessEvent = {
    ...initial.events[0],
    id: "stable-event",
    card: "004200",
    person: "Synthetic Person",
    simulated: false,
    direction: "ENTRY",
    deviceDoor: "1",
    deviceReader: "1",
    readerMappingVerified: true,
  };
  const state = {
    snapshot,
    events: [base],
    workspaceReads: 0,
    recordReads: 0,
    activeRecords: 0,
    maxActiveRecords: 0,
    failWorkspace: false,
    failRecords: false,
    unauthorized: false,
    recordDelay: 650,
    workspaceDelay: 100,
    fastFilter: "",
    slowFilter: "",
  };
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    if (route.request().method() !== "GET")
      throw new Error("Unexpected write in refresh test");
    const respond = (body: unknown, status = 200) =>
      route.fulfill({ status, json: body });
    if (url.pathname === "/api/auth/me") return respond(snapshot.identity);
    if (url.pathname === "/api/workspace") {
      state.workspaceReads++;
      const result = structuredClone({
        ...snapshot,
        clock: new Date(
          Date.parse(snapshot.clock) + state.workspaceReads * 1000,
        ).toISOString(),
      });
      await new Promise((r) => setTimeout(r, state.workspaceDelay));
      if (state.unauthorized)
        return respond(
          { code: "UNAUTHENTICATED", message: "Session expired." },
          401,
        );
      if (state.failWorkspace)
        return respond(
          {
            code: "NOT_CONNECTED",
            message: "Synthetic workspace interruption.",
          },
          503,
        );
      return respond(result);
    }
    if (url.pathname.startsWith("/api/records/")) {
      state.recordReads++;
      state.activeRecords++;
      state.maxActiveRecords = Math.max(
        state.maxActiveRecords,
        state.activeRecords,
      );
      const card = url.searchParams.get("card") ?? "";
      const rows = state.events.map((e) => ({ ...e }));
      await new Promise((r) =>
        setTimeout(
          r,
          card === state.slowFilter && card
            ? 1300
            : card === state.fastFilter && card
              ? 50
              : state.recordDelay,
        ),
      );
      state.activeRecords--;
      if (state.failRecords)
        return respond(
          { code: "NOT_CONNECTED", message: "Synthetic records interruption." },
          503,
        );
      if (url.pathname.endsWith("audits"))
        return respond({
          items: [
            {
              id: "audit-one",
              at: snapshot.clock,
              actor: "Synthetic Admin",
              action: "LOGIN",
              target: "Test",
              result: "SUCCESS",
            },
          ],
          total: 1,
          page: 1,
          pageSize: 15,
        });
      const number = Number(url.searchParams.get("page") ?? 1);
      return respond({
        items:
          number === 2
            ? [{ ...base, id: "page-two", card: "009999" }]
            : rows.filter((e) => e.card.includes(card)),
        total: card ? rows.filter((e) => e.card.includes(card)).length : 30,
        page: number,
        pageSize: 15,
      });
    }
    throw new Error(`Unexpected API path ${url.pathname}`);
  });
  return state;
}

async function signalRefresh(page: Page) {
  await page.evaluate(() =>
    document.dispatchEvent(new Event("visibilitychange")),
  );
}

async function watchTable(page: Page) {
  await page.evaluate(() => {
    const target = document.querySelector(".main-content table")!;
    const row = document.querySelector(".main-content tbody tr")!;
    const evidence = {
      missingFrames: 0,
      replacedFrames: 0,
      loadingFrames: 0,
      running: true,
    };
    Object.assign(window, { refreshEvidence: evidence });
    function frame() {
      if (!evidence.running) return;
      if (!document.querySelector(".main-content table"))
        evidence.missingFrames++;
      if (!target.isConnected || !row.isConnected) evidence.replacedFrames++;
      if (
        document
          .querySelector(".main-content")
          ?.textContent?.includes("Loading records…")
      )
        evidence.loadingFrames++;
      requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
  });
}

test("activity keeps table, focus and scroll through background polls and event bursts", async ({
  page,
}, info) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const state = await fixture(page);
  await page.goto("/cards-activity");
  await page
    .getByRole("button", { name: "Access activity", exact: true })
    .click();
  await expect(page.locator("tbody")).toContainText("004200");
  // StrictMode intentionally starts/cancels the initial effect twice. Measure
  // concurrency after that mount, across the background polls being tested.
  await expect.poll(() => state.activeRecords).toBe(0);
  state.maxActiveRecords = 0;
  await page.getByLabel("Card identifier", { exact: true }).focus();
  const scroll = await page.evaluate(() => {
    const area = document.querySelector(".table-scroll")!;
    area.scrollLeft = 120;
    return { x: area.scrollLeft, y: window.scrollY };
  });
  await watchTable(page);
  const initialReads = state.recordReads;
  await expect
    .poll(() => state.recordReads, { timeout: 14000 })
    .toBeGreaterThanOrEqual(initialReads + 2);
  await page.waitForTimeout(750);
  expect(
    await page.evaluate(() => ({
      x: document.querySelector(".table-scroll")!.scrollLeft,
      y: window.scrollY,
    })),
  ).toEqual(scroll);
  state.events = [
    ...Array.from({ length: 12 }, (_, i) => ({
      ...state.events[0],
      id: `burst-${i}`,
      card: `0066${String(i).padStart(2, "0")}`,
    })),
    state.events[0],
  ];
  const beforeBurst = state.recordReads;
  for (let i = 0; i < 8; i++) await signalRefresh(page);
  await expect.poll(() => state.recordReads).toBeGreaterThan(beforeBurst);
  await expect(page.locator("tbody")).toContainText("006611");
  await expect(
    page.getByLabel("Card identifier", { exact: true }),
  ).toBeFocused();
  const evidence = await page.evaluate(() => {
    const result = (
      window as unknown as {
        refreshEvidence: {
          missingFrames: number;
          replacedFrames: number;
          loadingFrames: number;
          running: boolean;
        };
      }
    ).refreshEvidence;
    result.running = false;
    return result;
  });
  expect(evidence.missingFrames).toBe(0);
  expect(evidence.replacedFrames).toBe(0);
  expect(evidence.loadingFrames).toBe(0);
  expect(state.maxActiveRecords).toBe(1);
  const evidencePath = test.info().outputPath("refresh-evidence.json");
  writeFileSync(
    evidencePath,
    JSON.stringify({
      viewport: info.project.name,
      ...evidence,
      scroll,
      recordReads: state.recordReads,
      workspaceReads: state.workspaceReads,
      burstEvents: 12,
      maxActiveRecords: state.maxActiveRecords,
    }),
  );
  await test.info().attach("refresh-evidence", {
    path: evidencePath,
    contentType: "application/json",
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: `test-results/refresh-bursts-${info.project.name}.png`,
    fullPage: true,
  });
  expect(errors).toEqual([]);
});

test("background errors retain records, dashboard and form drafts; expired sessions clear them", async ({
  page,
}) => {
  const state = await fixture(page);
  await page.goto("/cards-activity");
  await page
    .getByRole("button", { name: "Access activity", exact: true })
    .click();
  await expect(page.locator("tbody")).toContainText("004200");
  const table = await page.locator("table").elementHandle();
  state.failRecords = true;
  await signalRefresh(page);
  await expect(page.getByRole("alert")).toContainText(
    "Synthetic records interruption",
  );
  await expect(page.locator("tbody")).toContainText("004200");
  expect(await table!.evaluate((n) => n.isConnected)).toBe(true);
  state.failRecords = false;
  await page.getByRole("button", { name: "Retry", exact: true }).click();
  await expect(page.getByRole("alert")).toHaveCount(0);
  await page.goto("/profile");
  await page.getByLabel("Phone", { exact: true }).fill("080DRAFT");
  const input = await page.getByLabel("Phone", { exact: true }).elementHandle();
  state.snapshot.identity = {
    ...state.snapshot.identity,
    name: "Updated synthetic display name",
  };
  await signalRefresh(page);
  await expect(
    page.getByText("Updated synthetic display name").first(),
  ).toHaveText("Updated synthetic display name");
  await expect(page.getByLabel("Phone", { exact: true })).toHaveValue(
    "080DRAFT",
  );
  expect(await input!.evaluate((n) => n.isConnected)).toBe(true);
  state.failWorkspace = true;
  await signalRefresh(page);
  await expect(page.getByRole("alert")).toContainText(
    "Synthetic workspace interruption",
  );
  await expect(page.getByLabel("Phone", { exact: true })).toHaveValue(
    "080DRAFT",
  );
  expect(await input!.evaluate((n) => n.isConnected)).toBe(true);
  state.failWorkspace = false;
  await signalRefresh(page);
  await expect(page.getByRole("alert")).toHaveCount(0);
  await page.goto("/dashboard");
  await expect(page.locator("tbody")).toContainText("004200");
  const dashboard = await page
    .getByRole("heading", { name: "Dashboard", exact: true })
    .elementHandle();
  state.failWorkspace = true;
  await signalRefresh(page);
  await expect(page.getByRole("alert")).toContainText(
    "Synthetic workspace interruption",
  );
  expect(await dashboard!.evaluate((n) => n.isConnected)).toBe(true);
  state.failWorkspace = false;
  state.unauthorized = true;
  await signalRefresh(page);
  await expect(
    page.getByRole("button", { name: "Sign in", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Dashboard", exact: true }),
  ).toHaveCount(0);
});

test("filters, pagination and record families never show a late result for an old query", async ({
  page,
}) => {
  const state = await fixture(page);
  state.events.push({ ...state.events[0], id: "filter-two", card: "004300" });
  state.slowFilter = "0042";
  state.fastFilter = "0043";
  await page.goto("/cards-activity");
  await page
    .getByRole("button", { name: "Access activity", exact: true })
    .click();
  await expect(page.locator("tbody")).toContainText("004200");
  const field = page.getByLabel("Card identifier", { exact: true });
  const beforeFilter = state.recordReads;
  await field.fill("0042");
  await expect.poll(() => state.recordReads).toBeGreaterThan(beforeFilter);
  await field.fill("0043");
  await expect(page.locator("tbody")).toContainText("004300");
  await expect(page.locator("tbody")).not.toContainText("004200");
  await page.waitForTimeout(1500);
  await expect(page.locator("tbody")).not.toContainText("004200");
  await field.fill("");
  await expect(
    page.getByRole("button", { name: "Next", exact: true }),
  ).toBeEnabled();
  await page.getByRole("button", { name: "Next", exact: true }).click();
  await expect(page.locator("tbody")).toContainText("009999");
  await signalRefresh(page);
  await expect(
    page.getByText("30 records · page 2", { exact: true }),
  ).toBeVisible();
  await page.goto("/administration");
  await page.getByRole("button", { name: "Audit logs", exact: true }).click();
  await expect(page.locator("tbody")).toContainText("Synthetic Admin");
  await expect(page.locator("tbody")).not.toContainText("009999");
});

test("a refresh queued during a slow workspace read fetches the latest data", async ({
  page,
}) => {
  const state = await fixture(page);
  await page.goto("/profile");
  await page.getByLabel("Phone", { exact: true }).fill("QUEUED-DRAFT");
  const input = await page.getByLabel("Phone", { exact: true }).elementHandle();
  state.workspaceDelay = 500;
  const before = state.workspaceReads;
  await signalRefresh(page);
  await expect.poll(() => state.workspaceReads).toBeGreaterThan(before);
  state.snapshot.identity = {
    ...state.snapshot.identity,
    name: "Latest queued name",
  };
  for (let i = 0; i < 8; i++) await signalRefresh(page);
  // The first response already captured the old name. A queued second GET must
  // update it before the next four-second timer, with the form still mounted.
  await expect(page.getByText("Latest queued name").first()).toHaveText(
    "Latest queued name",
    { timeout: 2000 },
  );
  await expect(page.getByLabel("Phone", { exact: true })).toHaveValue(
    "QUEUED-DRAFT",
  );
  expect(await input!.evaluate((n) => n.isConnected)).toBe(true);
  expect(state.workspaceReads - before).toBeLessThanOrEqual(2);
});
