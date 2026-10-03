import { beforeEach, describe, expect, it, vi } from "vitest";
import { MockService } from "../src/services/mockService";
import {
  calendarMonths,
  csvCell,
  validEmail,
} from "../src/services/accessControlService";
import type { Command } from "../src/models";
let s: MockService;
const login = async (person: string) => s.selectDemo("account-" + person);
const controls = async (
  patch: Partial<{
    offline: boolean;
    failDisable: boolean;
    delayActivation: boolean;
    unlockTimeout: boolean;
  }>,
) => {
  const snap = await s.snapshot();
  await s.execute({
    type: "demo-controls",
    controls: { ...snap.controls, ...patch },
  });
};
const evidence = () =>
  new File(["synthetic receipt"], "demo.pdf", { type: "application/pdf" });
const replacement = async () => {
  await login("user");
  await s.execute({ type: "application", kind: "REPLACEMENT" });
  const app = (await s.snapshot()).applications.find(
    (a) => a.state === "DRAFT",
  )!;
  await s.execute({ type: "submit", id: app.id, file: evidence() });
  await login("manager");
  await s.execute({ type: "review", id: app.id, approve: true });
  await login("admin");
  return app.id;
};
const visitorAssigned = async () => {
  await login("admin");
  await s.execute({
    type: "visitor-approve",
    id: "VISIT-1001",
    note: "External departmental approval, demo ref 01",
  });
  await s.execute({
    type: "visitor-assign",
    id: "VISIT-1001",
    cardId: "pool-04",
  });
};
beforeEach(() => {
  s = new MockService(true, 0);
});
describe("registration", () => {
  it.each([" user@FUTO.EDU.NG ", "a@futo.edu.ng"])(
    "accepts exact domain %s",
    (email) => expect(validEmail(email, true)).toBe(true),
  );
  it.each([
    "a@student.futo.edu.ng",
    "a@futo.edu.ng.evil.example",
    "a@example.com",
    "not-email",
    "a@@futo.edu.ng",
  ])("rejects invalid university email %s", (email) =>
    expect(validEmail(email, true)).toBe(false),
  );
  it("registers USER with no physical assignment, supports memory-only login", async () => {
    const i = await s.register({
      type: "STAFF",
      reference: "STAFF/NEW",
      firstName: "Demo",
      lastName: "Person",
      email: " new@FUTO.EDU.NG ",
      phone: "0800",
      department: "IFT",
      password: "synthetic-password",
    });
    expect(i.role).toBe("USER");
    expect((await s.snapshot()).assignments).toHaveLength(0);
    await s.logout();
    expect((await s.login("new@futo.edu.ng", "synthetic-password")).id).toBe(
      i.id,
    );
  });
});
describe("authorization", () => {
  it.each([
    { type: "issue", id: "REQ-1001", number: "001122" },
    { type: "unlock", doorId: "door-1", confirmed: true },
    {
      type: "dates",
      personId: "user",
      start: "2026-01-01",
      expiry: "2027-01-01",
    },
  ] as Command[])("Manager cannot $type", async (command) => {
    await login("manager");
    await expect(s.execute(command)).rejects.toMatchObject({
      code: "FORBIDDEN",
    });
  });
  it("Manager reads and reviews but cannot export", async () => {
    await login("manager");
    await expect(s.exportRecords("events", {}, "csv")).rejects.toMatchObject({
      code: "FORBIDDEN",
    });
    expect((await s.records("events", {})).total).toBeGreaterThan(0);
    await s.execute({ type: "review", id: "REQ-1001", approve: true });
    expect((await s.snapshot()).applications[0].state).toBe("APPROVED");
  });
  it("User records scoped; cannot alter another applicant or card", async () => {
    await login("user");
    expect((await s.snapshot()).people.map((p) => p.id)).toEqual(["user"]);
    await expect(
      s.execute({ type: "submit", id: "REQ-1001", file: evidence() }),
    ).rejects.toMatchObject({ code: "FORBIDDEN" });
    await expect(s.records("events", {})).rejects.toMatchObject({
      code: "FORBIDDEN",
    });
    await expect(
      s.execute({
        type: "revoke",
        assignmentId: "assignment-super",
        reason: "MISSING",
        own: true,
      }),
    ).rejects.toMatchObject({ code: "FORBIDDEN" });
  });
  it("Management exports but cannot review", async () => {
    await login("management");
    expect(
      (await s.exportRecords("events", { result: "DENIED" }, "csv")).type,
    ).toContain("csv");
    await expect(
      s.execute({ type: "review", id: "REQ-1001", approve: true }),
    ).rejects.toMatchObject({ code: "FORBIDDEN" });
  });
  it("production disables demo identity and reset", async () => {
    const p = new MockService(false, 0);
    expect(await p.demoIdentities()).toEqual([]);
    await expect(p.selectDemo("account-super")).rejects.toMatchObject({
      code: "FORBIDDEN",
    });
    await expect(p.resetDemo()).rejects.toMatchObject({ code: "FORBIDDEN" });
  });
});
describe("payment and cards", () => {
  it("preserves rejection and resubmission history", async () => {
    await login("user");
    await s.execute({ type: "application", kind: "REPLACEMENT" });
    const id = (await s.snapshot()).applications[0].id;
    await s.execute({ type: "submit", id, file: evidence() });
    await login("manager");
    await expect(
      s.execute({ type: "review", id, approve: false }),
    ).rejects.toMatchObject({ code: "VALIDATION_ERROR" });
    await s.execute({
      type: "review",
      id,
      approve: false,
      reason: "Unreadable reference",
    });
    await login("user");
    await s.execute({ type: "submit", id, file: evidence() });
    const a = (await s.snapshot()).applications[0];
    expect(a.state).toBe("SUBMITTED");
    expect(a.history.some((h) => h.reason === "Unreadable reference")).toBe(
      true,
    );
  });
  it("submission never approves and duplicates are blocked", async () => {
    await login("user");
    await s.execute({ type: "application", kind: "REPLACEMENT" });
    const id = (await s.snapshot()).applications[0].id;
    await s.execute({ type: "submit", id, file: evidence() });
    expect((await s.snapshot()).applications[0].state).toBe("SUBMITTED");
    await expect(
      s.execute({ type: "submit", id, file: evidence() }),
    ).rejects.toMatchObject({ code: "CONFLICT" });
    await expect(
      s.execute({ type: "application", kind: "REPLACEMENT" }),
    ).rejects.toMatchObject({ code: "CONFLICT" });
  });
  it("failed disable blocks replacement until old credential is confirmed disabled", async () => {
    const id = await replacement();
    await controls({ failDisable: true });
    expect(
      (await s.execute({ type: "issue", id, number: "00007777" })).result,
    ).toBe("FAILED");
    let snap = await s.snapshot();
    expect(snap.cards.some((c) => c.number === "00007777")).toBe(false);
    expect(
      snap.assignments.find((a) => a.id === "assignment-user")?.state,
    ).toBe("REVOKED");
    expect(snap.notifications.some((n) => n.severity === "CRITICAL")).toBe(
      true,
    );
    await controls({ failDisable: false });
    await s.execute({ type: "retry-sync", assignmentId: "assignment-user" });
    await s.execute({ type: "issue", id, number: "00007777" });
    snap = await s.snapshot();
    expect(snap.cards.find((c) => c.id === "card-user")?.disabled).toBe(true);
    expect(
      snap.assignments.filter(
        (a) => a.personId === "user" && a.state === "ACTIVE",
      ),
    ).toHaveLength(1);
  });
  it("delayed activation is pending; reviewer and issuer differ; duplicate issuance blocked", async () => {
    await login("manager");
    await s.execute({ type: "review", id: "REQ-1001", approve: true });
    await login("admin");
    await controls({ delayActivation: true });
    await s.execute({ type: "issue", id: "REQ-1001", number: "00003333" });
    let a = (await s.snapshot()).applications[0];
    expect(a.state).toBe("ISSUANCE_PENDING");
    expect(a.issuedAt).toBeUndefined();
    await controls({ delayActivation: false });
    await s.execute({ type: "retry-sync", assignmentId: a.assignmentId });
    a = (await s.snapshot()).applications[0];
    expect(a.state).toBe("ISSUED");
    expect(a.reviewer).toBe("Ibrahim Bello");
    expect(a.issuer).toBe("Adaeze Nwosu");
    await expect(
      s.execute({ type: "issue", id: a.id, number: "00003333" }),
    ).rejects.toMatchObject({ code: "CONFLICT" });
  });
  it("rejects assigned or revoked assets and retains leading zeros", async () => {
    await login("manager");
    await s.execute({ type: "review", id: "REQ-1001", approve: true });
    await login("admin");
    await expect(
      s.execute({ type: "issue", id: "REQ-1001", number: "004201" }),
    ).rejects.toMatchObject({ code: "CONFLICT" });
    await s.execute({
      type: "revoke",
      assignmentId: "assignment-user",
      reason: "DAMAGED",
    });
    await expect(
      s.execute({ type: "issue", id: "REQ-1001", number: "004201" }),
    ).rejects.toMatchObject({ code: "CONFLICT" });
    await s.execute({ type: "issue", id: "REQ-1001", number: "00008888" });
    expect(
      (await s.snapshot()).cards.some((c) => c.number === "00008888"),
    ).toBe(true);
  });
  it("immediate own missing report even offline", async () => {
    await login("user");
    await controls({ offline: true });
    const r = await s.execute({
      type: "revoke",
      assignmentId: "assignment-user",
      reason: "MISSING",
      own: true,
    });
    expect(r.message).toBe(
      "Revoked locally — controller disable not confirmed.",
    );
    expect((await s.snapshot()).cards[0].revoked).toBe(true);
  });
  it("date expiry validated and updates stay pending", async () => {
    await login("admin");
    await expect(
      s.execute({
        type: "dates",
        personId: "user",
        start: "2027-01-02",
        expiry: "2027-01-01",
      }),
    ).rejects.toMatchObject({ code: "VALIDATION_ERROR" });
    await s.execute({
      type: "dates",
      personId: "user",
      start: "2026-01-01",
      expiry: "2027-02-01",
    });
    expect((await s.snapshot()).people.find((p) => p.id === "user")?.sync).toBe(
      "PENDING",
    );
  });
  it("scan session supports cancel and timeout", async () => {
    await login("admin");
    const scan = await s.startScan();
    expect((await s.pollScan(scan.id)).state).toBe("DETECTED");
    await s.cancelScan(scan.id);
    await expect(s.confirmScan(scan.id)).rejects.toMatchObject({
      code: "CONFLICT",
    });
    const second = await s.startScan();
    vi.spyOn(Date, "now").mockReturnValue(Date.parse(second.expiresAt) + 1);
    expect((await s.pollScan(second.id)).state).toBe("TIMED_OUT");
    vi.restoreAllMocks();
  });
});
describe("visitors and retention", () => {
  it("pool disabled before assignment; approval, unique assignment, physical return and disable required", async () => {
    await login("admin");
    expect(
      (await s.snapshot()).cards
        .filter((c) => c.kind === "VISITOR")
        .every((c) => c.disabled),
    ).toBe(true);
    await expect(
      s.execute({
        type: "visitor-assign",
        id: "VISIT-1001",
        cardId: "pool-04",
      }),
    ).rejects.toMatchObject({ code: "CONFLICT" });
    await visitorAssigned();
    await expect(
      s.execute({
        type: "visitor-assign",
        id: "VISIT-1001",
        cardId: "pool-03",
      }),
    ).rejects.toMatchObject({ code: "CONFLICT" });
    await expect(
      s.execute({
        type: "visitor-return",
        id: "VISIT-1001",
        returned: false,
        condition: "GOOD",
      }),
    ).rejects.toMatchObject({ code: "VALIDATION_ERROR" });
    await controls({ failDisable: true });
    await s.execute({
      type: "visitor-return",
      id: "VISIT-1001",
      returned: true,
      condition: "GOOD",
    });
    let snap = await s.snapshot();
    expect(snap.cards.find((c) => c.id === "pool-04")?.poolState).toBe(
      "RETURN_PENDING_DISABLE",
    );
    await controls({ failDisable: false });
    await s.execute({
      type: "retry-sync",
      assignmentId: snap.visits[0].assignmentId,
    });
    snap = await s.snapshot();
    expect(snap.cards.find((c) => c.id === "pool-04")).toMatchObject({
      poolState: "AVAILABLE_DISABLED",
      disabled: true,
    });
  });
  it("expired unreturned card is unavailable", async () => {
    await visitorAssigned();
    await s.execute({ type: "demo-clock", clock: "2026-10-04T12:00:00Z" });
    expect(
      (await s.snapshot()).cards.find((c) => c.id === "pool-04")?.poolState,
    ).toBe("QUARANTINED");
  });
  it("visitor restore preserves deadline and does not activate access", async () => {
    await visitorAssigned();
    await s.execute({
      type: "visitor-return",
      id: "VISIT-1001",
      returned: true,
      condition: "GOOD",
    });
    await login("super");
    await s.execute({ type: "demo-clock", clock: "2026-10-20T12:00:00Z" });
    const p = (await s.recovery()).people[0];
    await s.execute({ type: "restore", family: "people", id: p.id });
    expect(
      (await s.snapshot()).people.find((p) => p.id === "visitor")?.purgeAt,
    ).toBe(p.purgeAt);
    expect(
      (await s.snapshot()).assignments.find((a) => a.personId === "visitor")
        ?.state,
    ).toBe("CLOSED");
    await s.execute({ type: "demo-clock", clock: "2026-11-18T12:00:00Z" });
    expect((await s.snapshot()).people.some((p) => p.id === "visitor")).toBe(
      false,
    );
  });
  it("access log restoration retains 120-day purge deadline", async () => {
    await login("super");
    await s.execute({ type: "demo-clock", clock: "2027-01-05T12:00:00Z" });
    const e = (await s.recovery()).events[0];
    await s.execute({ type: "restore", family: "events", id: e.id });
    expect(
      (await s.records("events", {})).items.find((r) => r.id === e.id)?.purgeAt,
    ).toBe(e.purgeAt);
    await s.execute({ type: "demo-clock", clock: "2027-02-05T12:00:00Z" });
    expect(
      (await s.records("events", {})).items.some((r) => r.id === e.id),
    ).toBe(false);
  });
  it("audit deadline uses six calendar months plus 30 days", async () => {
    await login("super");
    await s.execute({ type: "demo-clock", clock: "2027-04-04T12:00:00Z" });
    const a = (await s.recovery()).audits.find((a) => a.id === "AUD-1001")!;
    expect(a.deletedAt).toBe("2027-04-03T10:00:00.000Z");
    expect(a.purgeAt).toBe("2027-05-03T10:00:00.000Z");
    expect(calendarMonths("2026-08-31T00:00:00Z", 6)).toBe(
      "2027-02-28T00:00:00.000Z",
    );
  });
});
describe("records and operations", () => {
  it("filtered exports audit and escape formulas", async () => {
    await login("management");
    expect(
      (await s.records("events", { card: "008899", result: "DENIED" })).total,
    ).toBe(1);
    const csv = await (
      await s.exportRecords(
        "events",
        { card: "008899", result: "DENIED" },
        "csv",
      )
    ).text();
    expect(csv).toContain("008899");
    expect(csv).not.toContain("004201");
    expect((await s.records("audits", { action: "EXPORT_CSV" })).total).toBe(1);
    for (const v of ["=SUM(A1)", "+1", "-1", "@SUM(A1)", "  =1", "\t1"])
      expect(csvCell(v)).toContain("'");
  });
  it("local PDF export generates PDF", async () => {
    await login("admin");
    const blob = await s.exportRecords("events", { result: "DENIED" }, "pdf");
    expect(blob.type).toBe("application/pdf");
    expect((await blob.text()).slice(0, 4)).toBe("%PDF");
  });
  it("unlock timeout is uncertain and not automatically repeated", async () => {
    await login("admin");
    await controls({ unlockTimeout: true });
    expect(
      (await s.execute({ type: "unlock", doorId: "door-1", confirmed: true }))
        .result,
    ).toBe("UNCERTAIN");
    expect((await s.records("audits", { action: "unlock" })).total).toBe(1);
    expect((await s.snapshot()).devices[0].sensor).toBe("UNKNOWN");
  });
  it("Super Admin role change does not create privileged card", async () => {
    await login("super");
    await s.execute({ type: "role", personId: "user", role: "SUPER_ADMIN" });
    expect((await s.snapshot()).cards.filter((c) => c.privileged)).toHaveLength(
      1,
    );
  });
});

describe("historical visitor privacy", () => {
  it("exit does not checkout; reassignment does not rewrite prior event owner; visitor purge anonymizes earlier events", async () => {
    await visitorAssigned();
    await login("visitor");
    await s.execute({ type: "demo-event", event: "EXIT" });
    expect((await s.snapshot()).visits[0].state).toBe("ACTIVE");
    await login("admin");
    const previous = (await s.records("events", { person: "Tunde" })).items[0];
    expect(previous).toBeDefined();
    await s.execute({
      type: "visitor-return",
      id: "VISIT-1001",
      returned: true,
      condition: "GOOD",
    });
    await s.register({
      type: "VISITOR",
      reference: "",
      firstName: "Second",
      lastName: "Visitor",
      email: "second@example.test",
      phone: "0800",
      department: "Example",
      password: "synthetic-password",
    });
    await s.execute({
      type: "visit",
      purpose: "Demo second visit",
      host: "IFT",
      start: "2026-10-03T08:00:00+01:00",
      expiry: "2026-10-03T17:00:00+01:00",
    });
    const visit = (await s.snapshot()).visits[0];
    await login("admin");
    await s.execute({
      type: "visitor-approve",
      id: visit.id,
      note: "External approval example",
    });
    await s.execute({
      type: "visitor-assign",
      id: visit.id,
      cardId: "pool-04",
    });
    expect((await s.records("events", { person: "Tunde" })).items[0]).toEqual(
      previous,
    );
    await login("super");
    await s.execute({ type: "demo-clock", clock: "2026-11-18T12:00:00Z" });
    const historical = (await s.records("events", {})).items.find(
      (e) => e.id === previous.id,
    );
    expect(historical).toMatchObject({
      person: "Anonymous visitor",
      reference: "",
      assignmentId: (previous as import("../src/models").AccessEvent)
        .assignmentId,
    });
  });
});

it("privileged designation cannot bypass a failed previous-card disable on a second attempt", async () => {
  await login("super");
  await s.execute({ type: "role", personId: "user", role: "SUPER_ADMIN" });
  await controls({ failDisable: true });
  expect(
    (await s.execute({ type: "designate", cardId: "card-user" })).result,
  ).toBe("FAILED");
  expect(
    (await s.execute({ type: "designate", cardId: "card-user" })).result,
  ).toBe("FAILED");
  expect(
    (await s.snapshot()).cards.find((c) => c.id === "card-user")?.privileged,
  ).toBe(false);
  await controls({ failDisable: false });
  await s.execute({ type: "designate", cardId: "card-user" });
  expect(
    (await s.snapshot()).cards.find((c) => c.id === "card-super")?.disabled,
  ).toBe(true);
});

it("HTTP adapter exposes disconnected state rather than fictional backend success", async () => {
  const { createHttpService } = await import("../src/services/httpService");
  const http = createHttpService("/api");
  expect(http.mode).toBe("http");
  await expect(http.currentIdentity()).rejects.toMatchObject({
    code: "NOT_CONNECTED",
  });
  await expect(
    http.execute({ type: "unlock", doorId: "door-1", confirmed: true }),
  ).rejects.toMatchObject({ code: "NOT_CONNECTED" });
});

it("restored visitor cannot restart its original personal-data retention deadline", async () => {
  await visitorAssigned();
  await s.execute({
    type: "visitor-return",
    id: "VISIT-1001",
    returned: true,
    condition: "GOOD",
  });
  await login("super");
  await s.execute({ type: "demo-clock", clock: "2026-10-20T12:00:00Z" });
  const row = (await s.recovery()).people[0];
  await s.execute({ type: "restore", family: "people", id: row.id });
  await login("visitor");
  await expect(
    s.execute({
      type: "visit",
      purpose: "Repeat visit",
      host: "IFT",
      start: "2026-10-21T08:00:00+01:00",
      expiry: "2026-10-21T17:00:00+01:00",
    }),
  ).rejects.toMatchObject({ code: "CONFLICT" });
  expect((await s.snapshot()).people[0].purgeAt).toBe(row.purgeAt);
});
it("privileged schedule remains unconfirmed when controller is disconnected", async () => {
  await login("super");
  await controls({ offline: true });
  expect(
    (await s.execute({ type: "designate", cardId: "card-super" })).result,
  ).toBe("FAILED");
  const result = await s.snapshot();
  expect(
    result.notifications.some(
      (n) => n.title === "Privileged schedule unconfirmed",
    ),
  ).toBe(true);
});
