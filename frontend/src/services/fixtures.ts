import type {
  Identity,
  Person,
  Card,
  Assignment,
  Application,
  Visit,
  Device,
  AccessEvent,
  Audit,
  Notice,
} from "../models";
export function seed(now: string) {
  const people: Person[] = [
    [
      "super",
      "STAFF",
      "IFT/STAFF/001",
      "Chinedu",
      "Okafor",
      "super@futo.edu.ng",
      "Library administration",
    ],
    [
      "admin",
      "STAFF",
      "IFT/STAFF/002",
      "Adaeze",
      "Nwosu",
      "admin@futo.edu.ng",
      "Library services",
    ],
    [
      "manager",
      "STAFF",
      "IFT/STAFF/003",
      "Ibrahim",
      "Bello",
      "manager@futo.edu.ng",
      "Finance",
    ],
    [
      "management",
      "STAFF",
      "IFT/STAFF/004",
      "Ngozi",
      "Eze",
      "management@futo.edu.ng",
      "Management",
    ],
    [
      "user",
      "STUDENT",
      "2023/IFT/0142",
      "Amara",
      "Okeke",
      "amara@futo.edu.ng",
      "Information Technology",
    ],
    [
      "student2",
      "STUDENT",
      "2022/IFT/0086",
      "David",
      "Afolabi",
      "david@futo.edu.ng",
      "Information Technology",
    ],
    [
      "visitor",
      "VISITOR",
      "VIS-0001",
      "Tunde",
      "Adeyemi",
      "tunde@example.test",
      "Guest researcher",
    ],
  ].map(([id, type, reference, firstName, lastName, email, department]) => ({
    id,
    type: type as Person["type"],
    reference,
    firstName,
    lastName,
    email,
    phone: "08000000000",
    department,
    status: "ACTIVE",
    start: "2026-01-01",
    expiry: "2027-09-30",
    sync: "SYNCHRONIZED",
  }));
  const identities: Identity[] = people
    .filter((p) => !["student2"].includes(p.id))
    .map((p) => ({
      id: "account-" + p.id,
      personId: p.id,
      name: p.firstName + " " + p.lastName,
      email: p.email,
      role:
        (
          {
            super: "SUPER_ADMIN",
            admin: "ADMIN",
            manager: "MANAGER",
            management: "MANAGEMENT",
          } as const
        )[p.id as "super"] ?? "USER",
    }));
  const cards: Card[] = [
    {
      id: "card-super",
      number: "000001",
      kind: "PERSONAL",
      revoked: false,
      disabled: false,
      privileged: true,
    },
    {
      id: "card-user",
      number: "004201",
      kind: "PERSONAL",
      revoked: false,
      disabled: false,
      privileged: false,
    },
    ...["01", "02", "03", "04"].map((n) => ({
      id: "pool-" + n,
      number: "0090" + n,
      kind: "VISITOR" as const,
      revoked: false,
      disabled: true,
      privileged: false,
      poolState: "AVAILABLE_DISABLED" as const,
    })),
  ];
  const assignments: Assignment[] = ["super", "user"].map((p) => ({
    id: "assignment-" + p,
    personId: p,
    cardId: "card-" + p,
    start: "2026-01-01",
    expiry: "2027-09-30",
    state: "ACTIVE",
    sync: "SYNCHRONIZED",
    confirmations: [
      {
        controllerId: "controller-1",
        confirmed: true,
        state: "SYNCHRONIZED",
        intendedState: "ENABLED",
        at: now,
      },
    ],
  }));
  const evidence = {
    name: "fictional-demo-receipt.pdf",
    type: "application/pdf",
    size: 300,
    url: "",
  };
  const applications: Application[] = [
    {
      id: "REQ-1001",
      personId: "student2",
      kind: "FIRST",
      state: "SUBMITTED",
      evidence,
      history: [{ at: now, actor: "David Afolabi", action: "SUBMITTED" }],
    },
  ];
  const visits: Visit[] = [
    {
      id: "VISIT-1001",
      personId: "visitor",
      purpose: "Research consultation",
      host: "IFT department",
      start: "2026-10-03T08:00:00+01:00",
      expiry: "2026-10-03T17:00:00+01:00",
      state: "REQUESTED",
      returned: false,
    },
  ];
  const devices: Device[] = [
    {
      id: "controller-1",
      name: "Library controller",
      model: "Hikvision DS-K2802",
      online: true,
      lastCommunication: now,
      doorId: "door-1",
      door: "E-Library main door",
      sensor: "UNKNOWN",
    },
  ];
  const events: AccessEvent[] = Array.from({ length: 8 }, (_, i) => ({
    id: "EVT-" + (1000 + i),
    at: new Date(Date.parse(now) - i * 7 * 60000).toISOString(),
    personId: i === 2 ? undefined : "user",
    person: i === 2 ? "Unknown credential" : "Amara Okeke",
    reference: i === 2 ? "" : "2023/IFT/0142",
    card: i === 2 ? "008899" : "004201",
    assignmentId: i === 2 ? undefined : "assignment-user",
    controller: "controller-1",
    door: "door-1",
    direction: i % 2 ? "EXIT" : "ENTRY",
    result: i === 2 ? "DENIED" : "GRANTED",
    type: i === 2 ? "UNKNOWN_CARD" : "CARD_SWIPE",
    simulated: true,
  }));
  const audits: Audit[] = [
    {
      id: "AUD-1001",
      at: now,
      actor: "System demo",
      action: "DEMO_INITIALIZED",
      target: "Synthetic records",
      result: "SUCCESS",
    },
  ];
  const notifications: Notice[] = [
    {
      id: "NOT-1001",
      at: now,
      severity: "WARNING",
      title: "Unknown card attempt",
      detail:
        "Unrecognized credential 008899 at Entry reader. Simulated event.",
      read: false,
    },
  ];
  return {
    people,
    identities,
    cards,
    assignments,
    applications,
    visits,
    devices,
    events,
    audits,
    notifications,
  };
}
