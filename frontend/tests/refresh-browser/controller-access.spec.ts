import { expect, test } from "@playwright/test";
import { seed } from "../../src/services/fixtures";
import type { Snapshot } from "../../src/models";

test("selected card reads and writes use the application with explicit confirmation", async ({
  page,
}) => {
  const initial = seed("2026-10-14T12:00:00Z");
  const snapshot: Snapshot = {
    ...initial,
    clock: "2026-10-14T12:00:00Z",
    schedule: { weekdays: [1,2,3,4,5], opens: "07:00", closes: "18:30", exceptions: [], sync: "OUT_OF_SYNC" },
    settings: { payment: "Synthetic fixture", uploadLimit: 5242880 },
    controls: { offline: false, failDisable: false, delayActivation: false, unlockTimeout: false },
    identity: initial.identities.find((i) => i.role === "SUPER_ADMIN")!,
    people: initial.people.filter((p) => p.id === "user"),
    cards: [
      {
        id: "managed-card",
        number: "00009999",
        kind: "PERSONAL",
        revoked: false,
        disabled: true,
        privileged: false,
      },
    ],
    assignments: [],
    notifications: [],
    controllerMode: "HIKVISION_READ_ONLY",
    controllerWritesAvailable: false,
    controllerReadStatus: "CONNECTED",
  };
  let enabled = false,
    executed = 0;
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/auth/me")
      return route.fulfill({ json: snapshot.identity });
    if (path === "/api/auth/csrf")
      return route.fulfill({ json: { csrfToken: "fixture" } });
    if (path === "/api/workspace") return route.fulfill({ json: snapshot });
    if (path === "/api/controller/access/authorize") {
      const body = route.request().postDataJSON();
      expect(body.confirmed).toBe(true);
      expect(body.cards).toEqual([
        { number: "00009999", kind: "PERSONAL", personIds: ["user"] },
      ]);
      enabled = true;
    }
    if (path === "/api/controller/access/close") enabled = false;
    if (path.startsWith("/api/controller/access"))
      return route.fulfill({
        json: {
          enabled,
          expired: false,
          template: 3,
          cards: enabled ? ["00009999"] : [],
          operations:
            enabled && !executed
              ? [
                  {
                    id: "selected-operation",
                    card: "00009999",
                    desired: "ENABLED",
                    status: "PENDING",
                    message: "Awaiting verification",
                  },
                ]
              : [],
        },
      });
    if (path === "/api/controller/cards/00009999")
      return route.fulfill({
        json: {
          card: "00009999",
          readAt: "2026-10-14T12:00:00Z",
          record: {
            type: 3,
            valid: 1,
            doorRights: {},
            plansByDoor: {},
            start: [2026, 10, 14, 7, 0, 0],
            expiry: [2026, 10, 14, 18, 0, 0],
          },
        },
      });
    if (path === "/api/controller/operations/selected-operation/execute") {
      expect(route.request().postDataJSON()).toEqual({ confirmed: true });
      executed++;
      return route.fulfill({
        json: {
          operationId: "selected-operation",
          status: "CONFIRMED",
          readbackVerified: true,
        },
      });
    }
    return route.fulfill({ json: { items: [], total: 0, page: 1 } });
  });
  await page.goto("/administration");
  await page
    .getByLabel("Authorize writes for these selected cards only")
    .selectOption("YES");
  await page
    .getByRole("button", { name: "Authorize selected cards", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Read 00009999", exact: true })
    .click();
  await expect(page.getByText("Card type: Blacklisted.")).toBeVisible();
  await page
    .getByRole("button", { name: "Review controller operation" })
    .click();
  expect(executed).toBe(0);
  await page.getByRole("button", { name: "Apply this card operation" }).click();
  await expect(page.getByText("Controller card state verified.")).toBeVisible();
  expect(executed).toBe(1);
  await page.getByRole("button", { name: "Lock controller writes" }).click();
  await expect(
    page.getByRole("button", { name: "Authorize selected cards", exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});

test("remote unlock checks duration and requires a second explicit action", async ({
  page,
}) => {
  const initial = seed("2026-10-14T12:00:00Z");
  const snapshot: Snapshot = {
    ...initial,
    clock: "2026-10-14T12:00:00Z",
    schedule: { weekdays: [1,2,3,4,5], opens: "07:00", closes: "18:30", exceptions: [], sync: "OUT_OF_SYNC" },
    settings: { payment: "Synthetic fixture", uploadLimit: 5242880 },
    controls: { offline: false, failDisable: false, delayActivation: false, unlockTimeout: false },
    identity: initial.identities.find((i) => i.role === "SUPER_ADMIN")!,
    notifications: [],
    devices: [{ ...initial.devices[0], doorId: "door-1" }],
    controllerMode: "HIKVISION_READ_ONLY",
    controllerWritesAvailable: false,
    controllerReadStatus: "CONNECTED",
  };
  let duration = 2,
    unlocks = 0,
    changes = 0;
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/auth/me")
      return route.fulfill({ json: snapshot.identity });
    if (path === "/api/auth/csrf")
      return route.fulfill({ json: { csrfToken: "fixture" } });
    if (path === "/api/workspace") return route.fulfill({ json: snapshot });
    if (path === "/api/controller/unlock" && route.request().method() === "GET")
      return route.fulfill({
        json: {
          doorId: "door-1",
          configuredSeconds: duration,
          requiredSeconds: 5,
          ready: duration === 5,
          sensor: "UNKNOWN",
        },
      });
    if (path === "/api/controller/unlock-duration") {
      expect(route.request().postDataJSON()).toEqual({ confirmed: true });
      changes++;
      duration = 5;
      return route.fulfill({
        json: {
          operationId: "duration-op",
          status: "CONFIRMED",
          readbackVerified: true,
          physicalState: "UNKNOWN",
          message: "Duration verified.",
        },
      });
    }
    if (path === "/api/controller/unlock") {
      expect(route.request().postDataJSON()).toEqual({ confirmed: true });
      unlocks++;
      return route.fulfill({
        json: {
          operationId: "unlock-op",
          status: "UNCERTAIN",
          readbackVerified: false,
          physicalState: "UNKNOWN",
          message:
            "Controller acknowledged the unlock; physical door state is unknown.",
        },
      });
    }
    return route.fulfill({ json: { items: [], total: 0, page: 1 } });
  });
  await page.goto("/dashboard");
  await page
    .getByRole("button", { name: "Remote unlock", exact: true })
    .click();
  await expect(
    page.getByText(
      "The configured duration must match five seconds before remote unlock is available.",
    ),
  ).toBeVisible();
  expect(unlocks).toBe(0);
  expect(changes).toBe(0);
  await page
    .getByRole("button", { name: "Set release duration to 5 seconds" })
    .click();
  expect(changes).toBe(0);
  await page.getByRole("button", { name: "Confirm duration change" }).click();
  await page
    .getByRole("button", { name: "Unlock Door 1 once for 5 seconds" })
    .click();
  await expect(
    page
      .getByText(
        "Controller acknowledged the unlock; physical door state is unknown.",
        { exact: true },
      )
      .last(),
  ).toBeVisible();
  expect(unlocks).toBe(1);
  expect(changes).toBe(1);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});
