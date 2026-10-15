import { expect, test } from "@playwright/test";
import { seed } from "../../src/services/fixtures";
import type { Snapshot } from "../../src/models";

function managedSnapshot(): Snapshot {
  const initial = seed("2026-10-15T12:00:00Z");
  return {
    ...initial,
    identity: initial.identities.find((i) => i.role === "SUPER_ADMIN")!,
    clock: "2026-10-15T12:00:00Z",
    schedule: {
      weekdays: [1, 2, 3, 4, 5],
      opens: "07:00",
      closes: "18:30",
      exceptions: [],
      sync: "PENDING",
    },
    settings: { payment: "Synthetic", uploadLimit: 5242880 },
    controls: {
      offline: false,
      failDisable: false,
      delayActivation: false,
      unlockTimeout: false,
    },
    controllerMode: "HIKVISION_CONTROLLED",
    controllerWritesAvailable: false,
    controllerReadStatus: "CONNECTED",
    notifications: [],
  };
}
for (const kind of ["CARD", "TEMPLATE"])
  test(`${kind} management previews exact change before execution`, async ({
    page,
  }) => {
    const snapshot = managedSnapshot();
    let executed = 0;
    let queued = kind !== "CARD";
    if (kind === "CARD") {
      snapshot.cards = [
        {
          id: "review-card",
          number: "00009999",
          kind: "PERSONAL",
          revoked: false,
          disabled: true,
          privileged: false,
        },
      ];
      snapshot.assignments = [
        {
          id: "review-assignment",
          cardId: "review-card",
          personId: "user",
          state: "PENDING",
          sync: "OUT_OF_SYNC",
          start: "2026-10-15T00:00:00Z",
          expiry: "2026-12-31T00:00:00Z",
          confirmations: [],
        },
      ];
    }
    const record = {
      enabled: 1,
      name: "Owner Active hours",
      week: 3,
      groups: Array(16).fill(0),
    };
    const preview = {
      id: "reviewed-operation",
      kind,
      slot: kind === "CARD" ? "00009999" : 3,
      status: "PENDING",
      message: "Fresh comparison saved",
      readbackVerified: false,
      before: kind === "CARD" ? null : record,
      proposed:
        kind === "CARD"
          ? { card: "00009999", type: 1 }
          : { ...record, week: 4 },
      affected: [{ card: "00009999", doors: [1] }],
      limitations: [],
    };
    await page.route("**/api/**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      if (path === "/api/auth/me")
        return route.fulfill({ json: snapshot.identity });
      if (path === "/api/auth/csrf")
        return route.fulfill({ json: { csrfToken: "fixture" } });
      if (path === "/api/workspace") return route.fulfill({ json: snapshot });
      if (path === "/api/commands") {
        expect(route.request().postDataJSON()).toEqual({
          type: "retry-sync",
          assignmentId: "review-assignment",
        });
        queued = true;
        return route.fulfill({
          json: {
            result: "PENDING",
            message: "Local operation prepared",
            operationIds: ["source-op"],
          },
        });
      }
      if (path === "/api/controller/access")
        return route.fulfill({
          json: {
            enabled: true,
            expired: false,
            template: 3,
            cards: ["00009999"],
            management: [],
            operations:
              executed || !queued
                ? []
                : [
                    {
                      id: "source-op",
                      card: "00009999",
                      desired: "ENABLED",
                      status: "PENDING",
                      message: "Pending physical activation",
                    },
                  ],
          },
        });
      if (path === "/api/controller/plans/TEMPLATE/3")
        return route.fulfill({
          json: {
            kind: "TEMPLATE",
            slot: 3,
            revision: "a".repeat(64),
            readAt: "2026-10-15T12:00:00Z",
            record,
          },
        });
      if (
        path ===
        `/api/controller/management/${kind === "CARD" ? "card" : "plan"}-preview`
      ) {
        const body = route.request().postDataJSON();
        if (kind === "CARD") expect(body.sourceOperationId).toBe("source-op");
        else expect(body.desired.week).toBe(4);
        return route.fulfill({ json: preview });
      }
      if (path === "/api/controller/management/reviewed-operation/execute") {
        expect(route.request().postDataJSON()).toEqual({
          confirmed: true,
          affectedCards: ["00009999"],
        });
        executed++;
        return route.fulfill({
          json: {
            ...preview,
            status: "CONFIRMED",
            message: "Device change and unaffected resources verified.",
            readbackVerified: true,
          },
        });
      }
      return route.fulfill({ json: { items: [], total: 0, page: 1 } });
    });
    await page.goto("/administration");
    if (kind === "CARD") {
      await page
        .getByRole("button", { name: "Prepare current assignment operation" })
        .click();
      await page
        .getByRole("button", { name: "Compare this operation" })
        .click();
      await page
        .getByRole("button", { name: "Prepare fresh comparison" })
        .click();
    } else {
      await page
        .getByRole("button", { name: "Schedules", exact: true })
        .click();
      await page
        .getByRole("button", { name: "Refresh from controller", exact: true })
        .click();
      await page.getByLabel("Linked week").fill("4");
      await page
        .getByRole("button", {
          name: "Preview schedule change and affected cards",
        })
        .click();
    }
    await expect(
      page.getByText("reviewed-operation", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText("Affected cards: 00009999 (door 1)"),
    ).toBeVisible();
    const apply = page.getByRole("button", {
      name: "Apply reviewed change once",
    });
    await expect(apply).toBeDisabled();
    expect(executed).toBe(0);
    await page
      .getByLabel("I authorize this exact change and the listed card impact.")
      .check();
    await apply.click();
    await expect(
      page
        .getByRole("dialog")
        .getByText("Device change and unaffected resources verified."),
    ).toBeVisible();
    await expect(apply).toHaveCount(0);
    expect(executed).toBe(1);
    await page.waitForTimeout(3500);
    await expect(
      page.getByText("reviewed-operation", { exact: true }),
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
      page
        .getByRole("dialog")
        .getByText("Door secured; prior release unobserved.", {
          exact: true,
        }),
    ).toBeVisible();
    await page.getByRole("button", { name: "Refresh unlock status" }).click();
    await expect(
      page
        .getByRole("dialog")
        .getByText("Door secured; prior release unobserved.", {
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

test("uncertain card reconciliation reads current state without another apply", async ({
  page,
}) => {
  const snapshot = managedSnapshot();
  let reconciles = 0;
  const preview = {
    id: "uncertain-card",
    kind: "CARD",
    slot: "00009999",
    status: "REQUIRES_RECONCILIATION",
    message: "Readback differed",
    readbackVerified: false,
    before: null,
    proposed: { card: "00009999", beginFlag: 1, endFlag: 1 },
    affected: [{ card: "00009999", doors: [1] }],
    limitations: [],
  };
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/auth/me")
      return route.fulfill({ json: snapshot.identity });
    if (path === "/api/auth/csrf")
      return route.fulfill({ json: { csrfToken: "fixture" } });
    if (path === "/api/workspace") return route.fulfill({ json: snapshot });
    if (path === "/api/controller/access")
      return route.fulfill({
        json: {
          enabled: true,
          expired: false,
          template: 3,
          cards: [],
          operations: [],
          management: [preview],
        },
      });
    if (path === "/api/controller/management/uncertain-card/reconcile") {
      expect(route.request().postDataJSON()).toEqual({
        confirmed: true,
        affectedCards: ["00009999"],
      });
      reconciles++;
      return route.fulfill({
        json: {
          ...preview,
          status: "CONFIRMED",
          readbackVerified: true,
          validityCompatibilityUsed: true,
          message: "Current state verified; raw flags retained.",
        },
      });
    }
    if (path.endsWith("/execute"))
      throw new Error("Reconciliation must not apply a write");
    return route.fulfill({ json: { items: [], total: 0, page: 1 } });
  });
  await page.goto("/administration");
  await page
    .getByRole("button", { name: "Inspect operation uncertai" })
    .click();
  await page
    .getByRole("button", { name: "Verify actual state without resending" })
    .click();
  await expect(
    page
      .getByRole("dialog")
      .getByText("Current state verified; raw flags retained."),
  ).toBeVisible();
  await expect(
    page.getByText("Actual validity limit flags remain zero.", {
      exact: false,
    }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Apply reviewed change once" }),
  ).toHaveCount(0);
  expect(reconciles).toBe(1);
});
