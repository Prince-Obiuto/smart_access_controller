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
    schedule: {
      weekdays: [1, 2, 3, 4, 5],
      opens: "07:00",
      closes: "18:30",
      exceptions: [],
      sync: "OUT_OF_SYNC",
    },
    settings: { payment: "Synthetic fixture", uploadLimit: 5242880 },
    controls: {
      offline: false,
      failDisable: false,
      delayActivation: false,
      unlockTimeout: false,
    },
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
    controllerMode: "HIKVISION_CONTROLLED",
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
    schedule: {
      weekdays: [1, 2, 3, 4, 5],
      opens: "07:00",
      closes: "18:30",
      exceptions: [],
      sync: "OUT_OF_SYNC",
    },
    settings: { payment: "Synthetic fixture", uploadLimit: 5242880 },
    controls: {
      offline: false,
      failDisable: false,
      delayActivation: false,
      unlockTimeout: false,
    },
    identity: initial.identities.find((i) => i.role === "SUPER_ADMIN")!,
    notifications: [],
    devices: [{ ...initial.devices[0], doorId: "door-1" }],
    controllerMode: "HIKVISION_CONTROLLED",
    controllerWritesAvailable: false,
    controllerReadStatus: "CONNECTED",
  };
  let duration = 2,
    unlocks = 0,
    changes = 0;
  let pending = false,
    observed = false;
  const unlockRecord = () => ({
    operationId: "unlock-op",
    command: "UNLOCK5",
    status: observed ? "CONFIRMED" : "UNCERTAIN",
    readbackVerified: false,
    physicalState: observed ? "RELEASED_AND_RELOCKED_OBSERVED" : "UNKNOWN",
    sdkAcknowledged: true,
    sdkStatus: "ACKNOWLEDGED",
    transportStatus: "ACKNOWLEDGED",
    message: observed
      ? "Release and relocking recorded."
      : "Controller acknowledged the unlock; physical door state is unknown.",
  });
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
          ready: duration === 5 && !pending,
          sensor: "UNKNOWN",
          controllerStatus: "CONNECTED",
          unresolvedOperations: pending ? [unlockRecord()] : [],
          ...(new URL(route.request().url()).searchParams.has("operationId")
            ? { operation: unlockRecord() }
            : {}),
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
      pending = true;
      return route.fulfill({ json: unlockRecord() });
    }
    if (path === "/api/controller/unlock/confirm-physical") {
      expect(route.request().postDataJSON()).toEqual({
        operationId: "unlock-op",
        confirmed: true,
      });
      observed = true;
      pending = false;
      return route.fulfill({ json: unlockRecord() });
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
  await expect(
    page.getByRole("button", { name: "Unlock Door 1 once for 5 seconds" }),
  ).toBeDisabled();
  await expect(
    page.getByText("Submitted operation:", { exact: false }),
  ).toContainText("unlock-op");
  await page.getByRole("button", { name: "Refresh unlock status" }).click();
  await expect(
    page
      .getByText(
        "Controller acknowledged the unlock; physical door state is unknown.",
        { exact: true },
      )
      .last(),
  ).toBeVisible();
  await page
    .getByRole("group", { name: "Unlock operation unlock-op" })
    .getByRole("button", { name: "Confirm this release and relocking" })
    .click();
  await expect(
    page.getByText("Release and relocking recorded.", { exact: true }).last(),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Unlock Door 1 once for 5 seconds" }),
  ).toBeEnabled();
  expect(unlocks).toBe(1);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});

for (const status of ["PENDING", "UNCERTAIN"]) {
  test(`remote unresolved ${status} records retain unknown acknowledgement and reconcile by ID`, async ({
    page,
  }) => {
    const initial = seed("2026-10-14T12:00:00Z");
    const snapshot: Snapshot = {
      ...initial,
      identity: initial.identities.find((i) => i.role === "SUPER_ADMIN")!,
      clock: "2026-10-14T12:00:00Z",
      schedule: {
        weekdays: [1, 2, 3, 4, 5],
        opens: "07:00",
        closes: "18:30",
        exceptions: [],
        sync: "OUT_OF_SYNC",
      },
      settings: { payment: "Synthetic", uploadLimit: 5242880 },
      controls: {
        offline: false,
        failDisable: false,
        delayActivation: false,
        unlockTimeout: false,
      },
      notifications: [],
      devices: [{ ...initial.devices[0], doorId: "door-1" }],
      controllerMode: "HIKVISION_CONTROLLED",
      controllerWritesAvailable: false,
      controllerReadStatus: "CONNECTED",
    };
    let reconciled = false,
      unlocks = 0;
    const first = () => ({
      operationId: "older-unresolved",
      command: "UNLOCK",
      status: reconciled ? "RECONCILED" : status,
      sdkAcknowledged: null,
      sdkStatus: "UNKNOWN",
      transportStatus: "UNCERTAIN",
      physicalState: reconciled
        ? "SECURED_OBSERVED_RELEASE_UNKNOWN"
        : "UNKNOWN",
      readbackVerified: false,
      message: reconciled
        ? "Door secured; prior release unobserved."
        : "No successful SDK acknowledgement is recorded; the door command outcome remains uncertain.",
    });
    const second = {
      ...first(),
      operationId: "another-unresolved",
      command: "UNLOCK5",
      status: "UNCERTAIN",
    };
    await page.route("**/api/**", async (route) => {
      const url = new URL(route.request().url()),
        path = url.pathname;
      if (path === "/api/auth/me")
        return route.fulfill({ json: snapshot.identity });
      if (path === "/api/auth/csrf")
        return route.fulfill({ json: { csrfToken: "fixture" } });
      if (path === "/api/workspace") return route.fulfill({ json: snapshot });
      if (
        path === "/api/controller/unlock" &&
        route.request().method() === "GET"
      )
        return route.fulfill({
          json: {
            doorId: "door-1",
            configuredSeconds: 5,
            requiredSeconds: 5,
            ready: false,
            sensor: "UNKNOWN",
            controllerStatus: "CONNECTED",
            unresolvedOperations: [...(!reconciled ? [first()] : []), second],
            ...(url.searchParams.has("operationId")
              ? { operation: first() }
              : {}),
          },
        });
      if (path === "/api/controller/unlock/reconcile") {
        const body = route.request().postDataJSON();
        expect(body.operationId).toBe("older-unresolved");
        expect(body.confirmed).toBe(true);
        reconciled = true;
        return route.fulfill({ json: first() });
      }
      if (path === "/api/controller/unlock") {
        unlocks++;
        throw new Error("Unresolved door command must block submission.");
      }
      return route.fulfill({ json: { items: [], total: 0, page: 1 } });
    });
    await page.goto("/dashboard");
    await page
      .getByRole("button", { name: "Remote unlock", exact: true })
      .click();
    const group = page.getByRole("group", {
      name: "Unlock operation older-unresolved",
    });
    await expect(
      group.getByText("SDK acknowledgement: UNKNOWN.", { exact: false }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Unlock Door 1 once for 5 seconds" }),
    ).toBeDisabled();
    if (status === "PENDING")
      await expect(
        group.getByRole("button", {
          name: "Confirm this release and relocking",
        }),
      ).toHaveCount(0);
    await group
      .getByRole("button", {
        name: "Door is secured now; original release unobserved",
      })
      .click();
    await expect(
      page.getByRole("dialog").getByText("Door secured; prior release unobserved.", {
        exact: true,
      }),
    ).toBeVisible();
    await page.getByRole("button", { name: "Refresh unlock status" }).click();
    await expect(
      page.getByRole("dialog").getByText("Door secured; prior release unobserved.", {
        exact: true,
      }),
    ).toBeVisible();
    await expect(
      page.getByRole("group", { name: "Unlock operation another-unresolved" }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Unlock Door 1 once for 5 seconds" }),
    ).toBeDisabled();
    expect(unlocks).toBe(0);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
  });
}
