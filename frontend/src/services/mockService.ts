import type { AccessControlService } from "./accessControlService";
import {
  ServiceError,
  validEmail,
  validateDates,
  csvCell,
  calendarMonths,
} from "./accessControlService";
import { can, type Capability } from "../permissions";
import type {
  Identity,
  Snapshot,
  Command,
  OperationResult,
  Assignment,
  Card,
  Filters,
  AccessEvent,
  Audit,
  ScanSession,
  Registration,
  Person,
  Confirmation,
  Settings,
} from "../models";
import { seed } from "./fixtures";
const day = 86400000;
const plus = (iso: string, days: number) =>
  new Date(Date.parse(iso) + days * day).toISOString();
const copy = <T>(v: T): T => structuredClone(v);
export class MockService implements AccessControlService {
  async unlockStatus(): Promise<Awaited<ReturnType<AccessControlService["unlockStatus"]>>> {
    throw new ServiceError("NOT_CONNECTED", "Real door control is unavailable in demo mode.");
  }
  async remoteUnlock(): Promise<Awaited<ReturnType<AccessControlService["remoteUnlock"]>>> {
    throw new ServiceError("NOT_CONNECTED", "Real door control is unavailable in demo mode.");
  }
  async confirmPhysicalUnlock(): Promise<Awaited<ReturnType<AccessControlService["confirmPhysicalUnlock"]>>> {
    return {
      operationId: "demo-unlock",
      status: "CONFIRMED",
      readbackVerified: false,
      physicalState: "RELEASED_AND_RELOCKED_OBSERVED",
      message: "Door 1 release and return to secured state confirmed by operator.",
    };
  }
  async configureUnlockDuration(): Promise<Awaited<ReturnType<AccessControlService["configureUnlockDuration"]>>> { return this.remoteUnlock(); }
  async controllerAccess(): Promise<import("./accessControlService").ControllerAccess> {
    throw new ServiceError("NOT_CONNECTED", "Real controller access is unavailable in demo mode.");
  }
  async authorizeControllerCards(): Promise<import("./accessControlService").ControllerAccess> { return this.controllerAccess(); }
  async closeControllerAccess(): Promise<import("./accessControlService").ControllerAccess> { return this.controllerAccess(); }
  async readControllerCard(): Promise<import("./accessControlService").ControllerCardRead> {
    throw new ServiceError("NOT_CONNECTED", "Real controller access is unavailable in demo mode.");
  }
  async executeControllerOperation(): Promise<{ operationId: string; status: string; readbackVerified: boolean }> {
    throw new ServiceError("NOT_CONNECTED", "Real controller access is unavailable in demo mode.");
  }
  async recoveryOptions() {
    return {
      available: false,
      contact:
        "Contact the IFT E-Library administrator. Email recovery requires the local backend.",
    };
  }
  async requestPasswordReset(_email: string) {
    return {
      message:
        "Email recovery is unavailable in the demo. Contact the administrator.",
    };
  }
  async resetPassword(
    _token: string,
    _password: string,
    _confirmation: string,
  ): Promise<{ message: string }> {
    throw new ServiceError(
      "FORBIDDEN",
      "Password recovery requires the local backend.",
    );
  }
  async adminRecovery(
    _personId: string,
    _identityConfirmed: boolean,
  ): Promise<{ message: string }> {
    this.require("people");
    throw new ServiceError(
      "FORBIDDEN",
      "Password recovery requires the local backend.",
    );
  }
  readonly mode = "mock" as const;
  private state = seed("2026-10-03T10:00:00.000Z");
  private identity: Identity | null = null;
  private privilegedHistory = new Set(["card-super"]);
  private clock = "2026-10-03T10:00:00.000Z";
  private settings: Settings = {
    payment:
      "Fictional demo only: pay at the example IFT library desk, reference your registration or staff ID. No real bank account is configured.",
    uploadLimit: 5 * 1024 * 1024,
    privilegedCardId: "card-super",
  };
  private schedule: Snapshot["schedule"] = {
    weekdays: [1, 2, 3, 4, 5],
    opens: "08:00",
    closes: "18:00",
    exceptions: [{ date: "2026-10-01", closed: true, label: "Demo holiday" }],
    sync: "SYNCHRONIZED",
  };
  private controls: Snapshot["controls"] = {
    offline: false,
    failDisable: false,
    delayActivation: false,
    unlockTimeout: false,
  };
  private listeners = new Set<() => void>();
  private timer?: ReturnType<typeof setInterval>;
  private scans = new Map<string, ScanSession>();
  private passwords = new Map<string, string>();
  private urls = new Set<string>();
  private seq = 2000;
  constructor(
    private demoEnabled = import.meta.env?.DEV ?? false,
    private latency = 100,
  ) {}
  private id(prefix: string) {
    return `${prefix}-${++this.seq}`;
  }
  private async wait() {
    if (this.latency) await new Promise((r) => setTimeout(r, this.latency));
  }
  private actor() {
    if (!this.identity)
      throw new ServiceError("UNAUTHENTICATED", "Please sign in.");
    const current = this.state.identities.find(
      (i) => i.id === this.identity!.id,
    );
    if (!current)
      throw new ServiceError(
        "UNAUTHENTICATED",
        "Account no longer retained. Please sign out.",
      );
    if (
      this.state.people.find((p) => p.id === current.personId)?.status ===
      "DISABLED"
    )
      throw new ServiceError("FORBIDDEN", "Account is disabled.");
    this.identity = copy(current);
    return this.identity;
  }
  private require(cap: Capability) {
    const actor = this.actor();
    if (!can(actor.role, cap))
      throw new ServiceError(
        "FORBIDDEN",
        "Your role cannot perform this action.",
      );
    return actor;
  }
  private person(id: string) {
    const p = this.state.people.find((p) => p.id === id && !p.deletedAt);
    if (!p) throw new ServiceError("VALIDATION_ERROR", "Person not found.");
    return p;
  }
  private audit(
    action: string,
    target: string,
    result = "SUCCESS",
    personId?: string,
  ) {
    this.state.audits.unshift({
      id: this.id("AUD"),
      at: this.clock,
      actor: this.identity?.name ?? "System demo",
      action,
      target,
      result,
      personId,
    });
  }
  private notice(
    title: string,
    detail: string,
    severity: Snapshot["notifications"][number]["severity"] = "INFO",
    personId?: string,
  ) {
    this.state.notifications.unshift({
      id: this.id("NOT"),
      at: this.clock,
      title,
      detail,
      severity,
      personId,
      read: false,
    });
  }
  private emit() {
    this.listeners.forEach((l) => l());
  }
  private confirmations(
    intendedState: Confirmation["intendedState"],
  ): Confirmation[] {
    return this.state.devices.map((d) => {
      const ok =
        d.online &&
        !this.controls.offline &&
        !(intendedState === "DISABLED" && this.controls.failDisable) &&
        !(intendedState !== "DISABLED" && this.controls.delayActivation);
      if (ok) d.lastCommunication = this.clock;
      return {
        controllerId: d.id,
        confirmed: ok,
        state: ok
          ? "SYNCHRONIZED"
          : d.online &&
              !this.controls.offline &&
              !(intendedState === "DISABLED" && this.controls.failDisable)
            ? "PENDING"
            : "FAILED",
        intendedState,
        at: ok ? this.clock : undefined,
      };
    });
  }
  private sync(a: Assignment, intended: Confirmation["intendedState"]) {
    a.confirmations = this.confirmations(intended);
    const confirmed =
      a.confirmations.length > 0 && a.confirmations.every((c) => c.confirmed);
    a.sync = confirmed
      ? "SYNCHRONIZED"
      : a.confirmations.some((c) => c.state === "FAILED")
        ? "FAILED"
        : "PENDING";
    const card = this.state.cards.find((c) => c.id === a.cardId)!;
    if (confirmed) {
      card.disabled = intended === "DISABLED";
      if (intended === "ENABLED") {
        a.state = "ACTIVE";
        if (card.kind === "VISITOR") card.poolState = "ASSIGNED";
      }
    } else
      this.notice(
        intended === "DISABLED"
          ? "Revoked locally — controller disable not confirmed."
          : "Controller synchronization pending",
        `Card ${card.number}; all target controllers require confirmation.`,
        intended === "DISABLED" ? "CRITICAL" : "WARNING",
        a.personId,
      );
    return {
      intendedState: intended,
      confirmations: copy(a.confirmations),
      result: confirmed
        ? "CONFIRMED"
        : a.sync === "FAILED"
          ? "FAILED"
          : "PENDING",
      message: confirmed
        ? "Simulated controller confirmation received."
        : intended === "DISABLED"
          ? "Revoked locally — controller disable not confirmed."
          : "Activation pending; access is not confirmed.",
    } as OperationResult;
  }
  private disable(a: Assignment, reason: Assignment["reason"]) {
    a.state = "REVOKED";
    a.reason = reason;
    const card = this.state.cards.find((c) => c.id === a.cardId)!;
    if (card.kind === "PERSONAL") {
      card.revoked = true;
      card.privileged = false;
      if (this.settings.privilegedCardId === card.id)
        this.settings.privilegedCardId = undefined;
    }
    return this.sync(a, "DISABLED");
  }
  private oldDisabled(personId: string) {
    return this.state.assignments
      .filter(
        (a) =>
          a.personId === personId &&
          ["REVOKED", "EXPIRED", "CLOSED"].includes(a.state) &&
          this.state.cards.find((c) => c.id === a.cardId)?.kind === "PERSONAL",
      )
      .every(
        (a) =>
          a.sync === "SYNCHRONIZED" &&
          this.state.cards.find((c) => c.id === a.cardId)?.disabled,
      );
  }
  private newAssignment(
    person: Person,
    card: Card,
    start: string,
    expiry: string,
  ) {
    validateDates(start, expiry);
    if (person.status !== "ACTIVE")
      throw new ServiceError("CONFLICT", "This account is disabled.");
    if (Date.parse(expiry) <= Date.parse(this.clock))
      throw new ServiceError(
        "VALIDATION_ERROR",
        "The access period has expired.",
      );
    if (
      this.state.assignments.some(
        (a) =>
          (a.personId === person.id || a.cardId === card.id) &&
          ["PENDING", "ACTIVE"].includes(a.state),
      )
    )
      throw new ServiceError(
        "CONFLICT",
        "A person and physical card may each have only one current assignment.",
      );
    const a: Assignment = {
      id: this.id("ASG"),
      personId: person.id,
      cardId: card.id,
      start,
      expiry,
      state: "PENDING",
      sync: "PENDING",
      confirmations: [],
    };
    this.state.assignments.push(a);
    return a;
  }
  async currentIdentity() {
    await this.wait();
    return copy(this.identity);
  }
  async demoIdentities() {
    await this.wait();
    return this.demoEnabled ? copy(this.state.identities) : [];
  }
  async selectDemo(id: string) {
    await this.wait();
    if (!this.demoEnabled)
      throw new ServiceError(
        "FORBIDDEN",
        "Identity switching is development-only.",
      );
    const i = this.state.identities.find((i) => i.id === id);
    if (!i)
      throw new ServiceError("VALIDATION_ERROR", "Unknown demo identity.");
    this.identity = copy(i);
    this.emit();
    return copy(i);
  }
  async login(email: string, password: string) {
    await this.wait();
    const i = this.state.identities.find(
      (i) => i.email.toLowerCase() === email.trim().toLowerCase(),
    );
    if (!i || this.passwords.get(i.id) !== password)
      throw new ServiceError(
        "VALIDATION_ERROR",
        "Invalid demo account credentials. Use demo entry for seeded identities.",
      );
    if (this.person(i.personId).status !== "ACTIVE")
      throw new ServiceError("FORBIDDEN", "Account is disabled.");
    this.identity = copy(i);
    return copy(i);
  }
  async logout() {
    await this.wait();
    this.identity = null;
    this.emit();
  }
  async register(data: Registration) {
    await this.wait();
    if (
      !["STUDENT", "STAFF", "VISITOR"].includes(data.type) ||
      !validEmail(data.email, data.type !== "VISITOR")
    )
      throw new ServiceError(
        "VALIDATION_ERROR",
        data.type === "VISITOR"
          ? "Enter a valid email."
          : "Use an email at exactly futo.edu.ng.",
        "email",
      );
    if (
      !data.firstName.trim() ||
      !data.lastName.trim() ||
      !data.phone.trim() ||
      !data.department.trim() ||
      (data.type !== "VISITOR" && !data.reference.trim()) ||
      data.password.length < 8
    )
      throw new ServiceError(
        "VALIDATION_ERROR",
        "Complete all required fields; password needs at least eight characters.",
      );
    if (
      this.state.people.some(
        (p) =>
          p.email.toLowerCase() === data.email.trim().toLowerCase() ||
          (data.type !== "VISITOR" && p.reference === data.reference.trim()),
      )
    )
      throw new ServiceError(
        "CONFLICT",
        "That email or registration/staff ID is already registered.",
      );
    const id = this.id("PER"),
      p: Person = {
        id,
        type: data.type,
        reference:
          data.type === "VISITOR" ? this.id("VIS") : data.reference.trim(),
        firstName: data.firstName.trim(),
        lastName: data.lastName.trim(),
        email: data.email.trim(),
        phone: data.phone.trim(),
        department: data.department.trim(),
        status: "ACTIVE",
        sync: "PENDING",
        photo: data.photo ? this.upload(data.photo, true).url : undefined,
      };
    this.state.people.push(p);
    const i: Identity = {
      id: this.id("ACC"),
      personId: id,
      role: "USER",
      name: p.firstName + " " + p.lastName,
      email: p.email,
    };
    this.state.identities.push(i);
    this.passwords.set(i.id, data.password);
    this.identity = i;
    this.audit("REGISTER", p.id, "SUCCESS", p.id);
    this.emit();
    return copy(i);
  }
  private upload(file: File, photo = false) {
    if (
      file.size > this.settings.uploadLimit ||
      file.size === 0 ||
      !(
        photo
          ? ["image/jpeg", "image/png"]
          : ["image/jpeg", "image/png", "application/pdf"]
      ).includes(file.type)
    )
      throw new ServiceError(
        "VALIDATION_ERROR",
        `Use ${photo ? "JPEG or PNG" : "JPEG, PNG or PDF"} under ${(this.settings.uploadLimit / 1048576).toFixed(1)} MiB.`,
      );
    const url =
      typeof URL.createObjectURL === "function"
        ? URL.createObjectURL(file)
        : "";
    this.urls.add(url);
    return { name: file.name, type: file.type, size: file.size, url };
  }
  private revokeUrl(url?: string) {
    if (url && this.urls.has(url)) {
      URL.revokeObjectURL(url);
      this.urls.delete(url);
    }
  }
  async snapshot() {
    await this.wait();
    this.retain();
    const i = this.actor(),
      privileged = i.role !== "USER";
    return copy({
      identity: i,
      people: this.state.people.filter(
        (p) => !p.deletedAt && (privileged || p.id === i.personId),
      ),
      cards: this.state.cards.filter(
        (c) =>
          privileged ||
          this.state.assignments.some(
            (a) => a.personId === i.personId && a.cardId === c.id,
          ),
      ),
      assignments: this.state.assignments.filter(
        (a) => privileged || a.personId === i.personId,
      ),
      applications: can(i.role, "review")
        ? this.state.applications
        : this.state.applications.filter((a) => a.personId === i.personId),
      visits: can(i.role, "visitors")
        ? this.state.visits
        : this.state.visits.filter((v) => v.personId === i.personId),
      devices: privileged ? this.state.devices : [],
      notifications: this.state.notifications.filter((n) =>
        i.role === "USER" ? n.personId === i.personId : true,
      ),
      schedule: this.schedule,
      settings: this.settings,
      controls: this.controls,
      clock: this.clock,
    });
  }
  async execute(c: Command): Promise<OperationResult> {
    await this.wait();
    this.retain();
    const actor = this.actor();
    let output: OperationResult = {
      intendedState: "UPDATED",
      confirmations: [],
      result: "CONFIRMED",
      message: "Saved in the synthetic demo.",
    };
    switch (c.type) {
      case "profile": {
        const p = this.person(actor.personId);
        if (!c.phone.trim())
          throw new ServiceError("VALIDATION_ERROR", "Phone is required.");
        const photo = c.photo ? this.upload(c.photo, true) : undefined;
        p.phone = c.phone.trim();
        if (photo) {
          this.revokeUrl(p.photo);
          p.photo = photo.url;
        }
        break;
      }
      case "person": {
        this.require("people");
        if (c.data.type === "VISITOR")
          throw new ServiceError(
            "VALIDATION_ERROR",
            "Use visitor registration for visitor accounts.",
          );
        if (
          !validEmail(c.data.email, true) ||
          !c.data.firstName.trim() ||
          !c.data.lastName.trim() ||
          !c.data.reference.trim()
        )
          throw new ServiceError(
            "VALIDATION_ERROR",
            "Identity fields and exact university email are required.",
          );
        if (c.data.start || c.data.expiry)
          validateDates(c.data.start ?? "", c.data.expiry ?? "");
        if (
          this.state.people.some(
            (p) =>
              p.id !== c.id &&
              (p.reference === c.data.reference ||
                p.email.toLowerCase() === c.data.email.trim().toLowerCase()),
          )
        )
          throw new ServiceError(
            "CONFLICT",
            "Email or institutional ID already exists.",
          );
        if (c.id) {
          const p = this.person(c.id);
          const account = this.state.identities.find(
            (i) => i.personId === p.id,
          );
          if (account && account.role !== "USER") this.require("settings");
          const datesChanged =
            p.start !== c.data.start || p.expiry !== c.data.expiry;
          Object.assign(p, c.data);
          if (datesChanged) {
            p.sync = "PENDING";
            this.state.assignments
              .filter(
                (a) =>
                  a.personId === p.id &&
                  ["ACTIVE", "PENDING"].includes(a.state),
              )
              .forEach((a) => {
                a.start = p.start!;
                a.expiry = p.expiry!;
                a.sync = "OUT_OF_SYNC";
              });
          }
          if (p.status === "DISABLED") {
            for (const a of this.state.assignments.filter(
              (a) =>
                a.personId === p.id && ["ACTIVE", "PENDING"].includes(a.state),
            ))
              output = this.disable(a, "NOT_PERMITTED");
          }
          if (account) {
            account.email = p.email;
            account.name = p.firstName + " " + p.lastName;
          }
        } else
          this.state.people.push({
            ...c.data,
            id: this.id("PER"),
            sync: "PENDING",
          });
        break;
      }
      case "dates": {
        this.require("people");
        validateDates(c.start, c.expiry);
        const p = this.person(c.personId);
        p.start = c.start;
        p.expiry = c.expiry;
        p.sync = "PENDING";
        this.state.assignments
          .filter(
            (a) =>
              a.personId === p.id && ["ACTIVE", "PENDING"].includes(a.state),
          )
          .forEach((a) => {
            a.start = c.start;
            a.expiry = c.expiry;
            a.sync = "OUT_OF_SYNC";
          });
        output.message =
          "Access dates saved — controller synchronization pending.";
        break;
      }
      case "application": {
        const p = this.person(actor.personId);
        if (p.type === "VISITOR")
          throw new ServiceError(
            "FORBIDDEN",
            "Visitor cards do not require payment.",
          );
        if (
          this.state.applications.some(
            (a) => a.personId === p.id && a.state !== "ISSUED",
          )
        )
          throw new ServiceError(
            "CONFLICT",
            "You already have an open application.",
          );
        const hasHistory = this.state.assignments.some(
          (a) => a.personId === p.id,
        );
        if (c.kind === "FIRST" && hasHistory)
          throw new ServiceError(
            "CONFLICT",
            "Choose replacement for an existing card holder.",
          );
        if (c.kind === "REPLACEMENT" && !hasHistory)
          throw new ServiceError("CONFLICT", "Request a first card.");
        this.state.applications.push({
          id: this.id("REQ"),
          personId: p.id,
          kind: c.kind,
          state: "DRAFT",
          history: [{ at: this.clock, actor: actor.name, action: "DRAFT" }],
        });
        break;
      }
      case "submit": {
        const a = this.state.applications.find(
          (a) => a.id === c.id && a.personId === actor.personId,
        );
        if (!a)
          throw new ServiceError(
            "FORBIDDEN",
            "Only the applicant may submit evidence.",
          );
        if (!["DRAFT", "REJECTED"].includes(a.state))
          throw new ServiceError(
            "CONFLICT",
            "This request is already submitted.",
          );
        const evidence = this.upload(c.file);
        this.revokeUrl(a.evidence?.url);
        a.evidence = evidence;
        a.state = "SUBMITTED";
        a.history.push({
          at: this.clock,
          actor: actor.name,
          action: "SUBMITTED",
        });
        break;
      }
      case "review": {
        this.require("review");
        const a = this.state.applications.find((a) => a.id === c.id);
        if (!a || a.state !== "SUBMITTED")
          throw new ServiceError(
            "CONFLICT",
            "Only submitted evidence can be reviewed.",
          );
        if (!c.approve && !c.reason?.trim())
          throw new ServiceError(
            "VALIDATION_ERROR",
            "A rejection reason is required.",
          );
        a.state = c.approve ? "APPROVED" : "REJECTED";
        a.reason = c.approve ? undefined : c.reason!.trim();
        a.reviewer = actor.name;
        a.reviewedAt = this.clock;
        a.history.push({
          at: this.clock,
          actor: actor.name,
          action: a.state,
          reason: a.reason,
        });
        this.notice(
          "Payment review: " + a.state,
          a.reason ??
            "Approved. Card issuance is a separate administrative action.",
          "INFO",
          a.personId,
        );
        break;
      }
      case "issue": {
        this.require("issue");
        const app = this.state.applications.find((a) => a.id === c.id);
        if (!app || !["APPROVED", "ISSUANCE_PENDING"].includes(app.state))
          throw new ServiceError(
            "CONFLICT",
            "Payment must be approved before issuance.",
          );
        const p = this.person(app.personId);
        validateDates(p.start ?? "", p.expiry ?? "");
        if (!/^\d{4,32}$/.test(c.number))
          throw new ServiceError(
            "VALIDATION_ERROR",
            "Enter 4–32 card digits; leading zeros are preserved.",
          );
        if (app.assignmentId) {
          const a = this.state.assignments.find(
            (a) => a.id === app.assignmentId,
          )!;
          if (
            this.state.cards.find((card) => card.id === a.cardId)!.number !==
            c.number
          )
            throw new ServiceError(
              "CONFLICT",
              "Retry using the pending card number.",
            );
          if (!this.oldDisabled(p.id))
            throw new ServiceError(
              "CONFLICT",
              "Old-card disable must be confirmed before replacement activation.",
            );
          output = this.sync(a, "ENABLED");
          if (output.result === "CONFIRMED") {
            app.state = "ISSUED";
            app.issuer = actor.name;
            app.issuedAt = this.clock;
          }
          break;
        }
        let card = this.state.cards.find((card) => card.number === c.number);
        if (
          card &&
          (card.revoked ||
            card.kind === "VISITOR" ||
            this.state.assignments.some(
              (a) =>
                a.cardId === card!.id &&
                ["ACTIVE", "PENDING"].includes(a.state),
            ))
        )
          throw new ServiceError(
            "CONFLICT",
            "Card is assigned, revoked or reserved for the visitor pool.",
          );
        if (app.kind === "REPLACEMENT") {
          for (const old of this.state.assignments.filter(
            (a) =>
              a.personId === p.id && ["ACTIVE", "PENDING"].includes(a.state),
          ))
            this.disable(old, "DAMAGED");
          if (!this.oldDisabled(p.id)) {
            app.state = "ISSUANCE_PENDING";
            output = {
              intendedState: "DISABLED",
              confirmations: this.state.assignments
                .filter((a) => a.personId === p.id && a.state === "REVOKED")
                .flatMap((a) => a.confirmations),
              result: "FAILED",
              message: "Replacement blocked: old-card disable not confirmed.",
            };
            break;
          }
        }
        if (!card) {
          card = {
            id: this.id("CARD"),
            number: c.number,
            kind: "PERSONAL",
            revoked: false,
            disabled: true,
            privileged: false,
          };
          this.state.cards.push(card);
        }
        const a = this.newAssignment(p, card, p.start!, p.expiry!);
        app.assignmentId = a.id;
        app.cardId = card.id;
        app.state = "ISSUANCE_PENDING";
        output = this.sync(a, "ENABLED");
        if (output.result === "CONFIRMED") {
          app.state = "ISSUED";
          app.issuer = actor.name;
          app.issuedAt = this.clock;
        }
        app.history.push({
          at: this.clock,
          actor: actor.name,
          action: app.state,
        });
        break;
      }
      case "revoke": {
        const a = this.state.assignments.find((a) => a.id === c.assignmentId);
        if (!a)
          throw new ServiceError("VALIDATION_ERROR", "Assignment not found.");
        if (c.own) {
          if (a.personId !== actor.personId || c.reason !== "MISSING")
            throw new ServiceError(
              "FORBIDDEN",
              "You may report only your own card missing.",
            );
        } else this.require("issue");
        if (!["ACTIVE", "PENDING"].includes(a.state))
          throw new ServiceError(
            "CONFLICT",
            "Card assignment is already ended.",
          );
        output = this.disable(a, c.reason);
        const card = this.state.cards.find((card) => card.id === a.cardId)!;
        if (card.kind === "VISITOR")
          card.poolState =
            c.reason === "DAMAGED"
              ? "DAMAGED"
              : c.reason === "MISSING"
                ? "MISSING"
                : "QUARANTINED";
        break;
      }
      case "retry-sync": {
        this.require("issue");
        if (c.assignmentId) {
          const a = this.state.assignments.find((a) => a.id === c.assignmentId);
          if (!a)
            throw new ServiceError("VALIDATION_ERROR", "Assignment not found.");
          const disabling = ["REVOKED", "CLOSED", "EXPIRED"].includes(a.state);
          if (!disabling && !this.oldDisabled(a.personId))
            throw new ServiceError(
              "CONFLICT",
              "Old credential disable remains unconfirmed.",
            );
          output = this.sync(
            a,
            disabling
              ? "DISABLED"
              : a.state === "PENDING"
                ? "ENABLED"
                : "UPDATED",
          );
          if (output.result === "CONFIRMED") {
            for (const app of this.state.applications.filter(
              (app) =>
                app.assignmentId === a.id && app.state === "ISSUANCE_PENDING",
            )) {
              app.state = "ISSUED";
              app.issuer = actor.name;
              app.issuedAt = this.clock;
            }
            const v = this.state.visits.find((v) => v.assignmentId === a.id);
            if (v) {
              const card = this.state.cards.find((c) => c.id === a.cardId)!;
              if (a.state === "ACTIVE") v.state = "ACTIVE";
              else if (
                a.returnedAt &&
                card.poolState === "RETURN_PENDING_DISABLE"
              )
                card.poolState = "AVAILABLE_DISABLED";
            }
          }
        }
        if (c.personId) {
          const p = this.person(c.personId);
          const confirmations = this.confirmations("UPDATED");
          p.sync = confirmations.every((c) => c.confirmed)
            ? "SYNCHRONIZED"
            : "FAILED";
          output = {
            intendedState: "UPDATED",
            confirmations,
            result: p.sync === "SYNCHRONIZED" ? "CONFIRMED" : "FAILED",
            message: "Simulated date synchronization: " + p.sync,
          };
          this.state.assignments
            .filter((a) => a.personId === p.id && a.state === "ACTIVE")
            .forEach((a) => {
              a.sync = p.sync;
              a.confirmations = confirmations;
            });
        }
        if (c.schedule) {
          const conf = this.confirmations("UPDATED");
          this.schedule.sync = conf.every((c) => c.confirmed)
            ? "SYNCHRONIZED"
            : "FAILED";
          output = {
            intendedState: "UPDATED",
            confirmations: conf,
            result:
              this.schedule.sync === "SYNCHRONIZED" ? "CONFIRMED" : "FAILED",
            message:
              "Simulated schedule synchronization: " + this.schedule.sync,
          };
        }
        break;
      }
      case "visitor-request":
      case "visit": {
        if (c.type === "visitor-request") this.require("visitors");
        const p = this.person(
          c.type === "visitor-request" ? c.personId : actor.personId,
        );
        if (p.type !== "VISITOR")
          throw new ServiceError(
            "FORBIDDEN",
            "Only visitor accounts submit visit requests.",
          );
        validateDates(c.start, c.expiry);
        if (!c.purpose.trim() || !c.host.trim())
          throw new ServiceError(
            "VALIDATION_ERROR",
            "Purpose and host are required.",
          );
        if (
          this.state.visits.some(
            (v) => v.personId === p.id && v.state !== "ENDED",
          )
        )
          throw new ServiceError("CONFLICT", "An open visit already exists.");
        if (p.accessEndedAt)
          throw new ServiceError(
            "CONFLICT",
            "This temporary account has ended. Restoring its details does not renew access or change its original purge deadline.",
          );
        this.state.visits.push({
          id: this.id("VISIT"),
          personId: p.id,
          purpose: c.purpose,
          host: c.host,
          start: c.start,
          expiry: c.expiry,
          state: "REQUESTED",
          returned: false,
        });
        break;
      }
      case "visitor-approve": {
        this.require("visitors");
        const v = this.state.visits.find((v) => v.id === c.id);
        if (!v || v.state !== "REQUESTED")
          throw new ServiceError(
            "CONFLICT",
            "Visit cannot be approved in this state.",
          );
        if (!c.note.trim())
          throw new ServiceError(
            "VALIDATION_ERROR",
            "Record the external approval reference.",
          );
        v.approval = {
          at: this.clock,
          actor: actor.name,
          action: "EXTERNAL_APPROVAL_CONFIRMED",
          reason: c.note,
        };
        v.state = "APPROVED";
        break;
      }
      case "visitor-assign": {
        this.require("visitors");
        const v = this.state.visits.find((v) => v.id === c.id);
        const card = this.state.cards.find((card) => card.id === c.cardId);
        if (!v || v.state !== "APPROVED" || !v.approval)
          throw new ServiceError(
            "CONFLICT",
            "External approval must be confirmed first.",
          );
        if (!card || card.poolState !== "AVAILABLE_DISABLED" || !card.disabled)
          throw new ServiceError(
            "CONFLICT",
            "Pool card must be available and confirmed disabled.",
          );
        const p = this.person(v.personId),
          a = this.newAssignment(p, card, v.start, v.expiry);
        p.start = v.start;
        p.expiry = v.expiry;
        v.assignmentId = a.id;
        v.state = "PENDING";
        card.poolState = "ASSIGNMENT_PENDING";
        output = this.sync(a, "ENABLED");
        if (output.result === "CONFIRMED") v.state = "ACTIVE";
        break;
      }
      case "visitor-return": {
        this.require("visitors");
        const v = this.state.visits.find((v) => v.id === c.id);
        const a = this.state.assignments.find((a) => a.id === v?.assignmentId);
        if (!v || !a)
          throw new ServiceError("CONFLICT", "No assigned visitor card.");
        if (c.condition === "GOOD" && !c.returned)
          throw new ServiceError(
            "VALIDATION_ERROR",
            "Record physical card return before checkout.",
          );
        const card = this.state.cards.find((card) => card.id === a.cardId)!;
        v.returned = c.returned;
        v.state = "ENDED";
        a.state = "CLOSED";
        a.returnedAt = c.returned ? this.clock : undefined;
        const p = this.person(v.personId);
        p.accessEndedAt ??= this.clock;
        p.purgeAt ??= plus(p.accessEndedAt, 45);
        card.poolState =
          c.condition === "GOOD" ? "RETURN_PENDING_DISABLE" : c.condition;
        output = this.sync(a, "DISABLED");
        if (
          output.result === "CONFIRMED" &&
          c.returned &&
          c.condition === "GOOD"
        )
          card.poolState = "AVAILABLE_DISABLED";
        break;
      }
      case "schedule": {
        this.require("schedules");
        if (
          !/^\d{2}:\d{2}$/.test(c.schedule.opens) ||
          !/^\d{2}:\d{2}$/.test(c.schedule.closes) ||
          c.schedule.opens >= c.schedule.closes ||
          !c.schedule.weekdays.length ||
          c.schedule.weekdays.some((d) => d < 0 || d > 6) ||
          c.schedule.exceptions.some(
            (e) =>
              !/^\d{4}-\d{2}-\d{2}$/.test(e.date) ||
              (!e.closed && (!e.opens || !e.closes || e.opens >= e.closes)),
          )
        )
          throw new ServiceError(
            "VALIDATION_ERROR",
            "Set valid working days, opening hours and exception hours.",
          );
        this.schedule = copy({ ...c.schedule, sync: "PENDING" });
        output.message = "Schedule saved — application to controllers pending.";
        break;
      }
      case "unlock": {
        this.require("unlock");
        const d = this.state.devices.find((d) => d.doorId === c.doorId);
        if (!d || !c.confirmed)
          throw new ServiceError(
            "VALIDATION_ERROR",
            "Confirm the named door before sending.",
          );
        const uncertain = this.controls.unlockTimeout,
          failed = !d.online || this.controls.offline;
        output = {
          intendedState: "TIMED_UNLOCK_5_SECONDS",
          confirmations:
            failed || uncertain ? [] : this.confirmations("UPDATED"),
          result: failed ? "FAILED" : uncertain ? "UNCERTAIN" : "CONFIRMED",
          message: failed
            ? "Controller disconnected — command not sent."
            : uncertain
              ? "Unlock timed out — outcome uncertain. Do not automatically repeat."
              : "Simulated 5-second command acknowledged; physical door state UNKNOWN.",
        };
        this.notice(
          "Remote timed unlock",
          `${actor.name}: ${d.door}; ${output.message}`,
          uncertain || failed ? "WARNING" : "INFO",
        );
        break;
      }
      case "settings": {
        this.require("settings");
        if (
          !c.settings.payment.trim() ||
          !Number.isFinite(c.settings.uploadLimit) ||
          c.settings.uploadLimit < 1024 ||
          c.settings.uploadLimit > 20 * 1048576
        )
          throw new ServiceError(
            "VALIDATION_ERROR",
            "Payment instructions and upload limit (up to 20 MiB) are required.",
          );
        this.settings = {
          ...this.settings,
          payment: c.settings.payment,
          uploadLimit: c.settings.uploadLimit,
        };
        break;
      }
      case "role": {
        this.require("settings");
        const i = this.state.identities.find((i) => i.personId === c.personId);
        if (!i)
          throw new ServiceError(
            "VALIDATION_ERROR",
            "This record has no account.",
          );
        if (i.personId === actor.personId && c.role !== "SUPER_ADMIN")
          throw new ServiceError(
            "CONFLICT",
            "Cannot remove your own Super Admin role in this demo.",
          );
        i.role = c.role;
        break;
      }
      case "designate": {
        this.require("settings");
        const card = this.state.cards.find((card) => card.id === c.cardId);
        const a = this.state.assignments.find(
          (a) => a.cardId === card?.id && a.state === "ACTIVE",
        );
        if (
          !card ||
          !a ||
          card.kind !== "PERSONAL" ||
          card.revoked ||
          a.sync !== "SYNCHRONIZED" ||
          !this.state.identities.some(
            (i) => i.personId === a.personId && i.role === "SUPER_ADMIN",
          )
        )
          throw new ServiceError(
            "CONFLICT",
            "Designate an active synchronized personal card assigned to a Super Admin.",
          );
        const old = this.state.cards.find(
          (card) =>
            this.privilegedHistory.has(card.id) &&
            card.id !== c.cardId &&
            !card.disabled,
        );
        if (old) {
          const oldAssignment = this.state.assignments.find(
            (a) => a.cardId === old.id,
          );
          if (oldAssignment) {
            output = ["ACTIVE", "PENDING"].includes(oldAssignment.state)
              ? this.disable(oldAssignment, "NOT_PERMITTED")
              : this.sync(oldAssignment, "DISABLED");
            if (output.result !== "CONFIRMED") {
              output.message =
                "Designation blocked: previous privileged credential disable unconfirmed.";
              break;
            }
          }
          if (!old.disabled)
            throw new ServiceError(
              "CONFLICT",
              "Previous privileged credential disable unconfirmed.",
            );
        }
        const confirmations = this.confirmations("UPDATED");
        if (!confirmations.every((c) => c.confirmed)) {
          output = {
            intendedState: "UNRESTRICTED_SCHEDULE",
            confirmations,
            result: confirmations.some((c) => c.state === "FAILED")
              ? "FAILED"
              : "PENDING",
            message:
              "Privileged schedule synchronization unconfirmed. Designation has not been applied.",
          };
          this.notice(
            "Privileged schedule unconfirmed",
            output.message,
            "WARNING",
          );
          break;
        }
        output.confirmations = confirmations;
        output.intendedState = "UNRESTRICTED_SCHEDULE";
        this.state.cards.forEach((card) => (card.privileged = false));
        card.privileged = true;
        this.privilegedHistory.add(card.id);
        this.settings.privilegedCardId = card.id;
        output.message =
          "Designated card: separate unrestricted 24/7 schedule, including holidays (simulated).";
        break;
      }
      case "device": {
        this.require("settings");
        const d = this.state.devices.find((d) => d.id === c.id);
        if (!d || !c.name.trim())
          throw new ServiceError(
            "VALIDATION_ERROR",
            "Controller and name required.",
          );
        d.name = c.name.trim();
        break;
      }
      case "restore": {
        this.require("recovery");
        const family =
          c.family === "people"
            ? this.state.people
            : c.family === "events"
              ? this.state.events
              : this.state.audits;
        const row = family.find((r) => r.id === c.id);
        if (
          !row?.deletedAt ||
          !row.purgeAt ||
          Date.parse(row.purgeAt) <= Date.parse(this.clock)
        )
          throw new ServiceError("CONFLICT", "Record is not recoverable.");
        delete row.deletedAt;
        row.restored = true;
        output.message =
          "Restored. Original purge deadline is unchanged; access remains ended.";
        break;
      }
      case "notice-read": {
        const n = this.state.notifications.find((n) => n.id === c.id);
        if (!n || (actor.role === "USER" && n.personId !== actor.personId))
          throw new ServiceError("FORBIDDEN", "Notification not permitted.");
        n.read = true;
        break;
      }
      case "pool-card": {
        this.require("settings");
        throw new ServiceError(
          "VALIDATION_ERROR",
          "New pool assets are configured in HTTP mode; mock mode uses its seeded pool.",
        );
      }
      case "demo-controls": {
        if (!this.demoEnabled)
          throw new ServiceError(
            "FORBIDDEN",
            "Development controls unavailable.",
          );
        const changed = c.controls.offline !== this.controls.offline;
        this.controls = copy(c.controls);
        this.state.devices.forEach((d) => (d.online = !c.controls.offline));
        if (changed)
          this.notice(
            c.controls.offline
              ? "Controller disconnected"
              : "Controller reconnected",
            "Simulated connection change.",
            "WARNING",
          );
        break;
      }
      case "demo-clock": {
        if (!this.demoEnabled)
          throw new ServiceError(
            "FORBIDDEN",
            "Development controls unavailable.",
          );
        if (
          !Number.isFinite(Date.parse(c.clock)) ||
          Date.parse(c.clock) < Date.parse(this.clock)
        )
          throw new ServiceError(
            "VALIDATION_ERROR",
            "Demo clock can only advance. Reset to go back.",
          );
        this.clock = new Date(c.clock).toISOString();
        this.retain();
        break;
      }
      case "demo-event": {
        if (!this.demoEnabled)
          throw new ServiceError(
            "FORBIDDEN",
            "Development controls unavailable.",
          );
        this.makeEvent(c.event);
        break;
      }
    }
    this.audit(
      c.type,
      ("id" in c
        ? c.id
        : "personId" in c
          ? c.personId
          : "assignmentId" in c
            ? c.assignmentId
            : c.type) ?? c.type,
      output.result,
      actor.personId,
    );
    this.emit();
    return copy(output);
  }
  private scheduleAllows() {
    const lagos = new Date(Date.parse(this.clock) + 3600000),
      date = lagos.toISOString().slice(0, 10),
      time = lagos.toISOString().slice(11, 16),
      ex = this.schedule.exceptions.find((e) => e.date === date);
    if (ex)
      return (
        !ex.closed &&
        time >= (ex.opens ?? this.schedule.opens) &&
        time < (ex.closes ?? this.schedule.closes)
      );
    return (
      this.schedule.weekdays.includes(lagos.getUTCDay()) &&
      time >= this.schedule.opens &&
      time < this.schedule.closes
    );
  }
  private makeEvent(kind: string) {
    const a =
        this.state.assignments.find(
          (a) => a.state === "ACTIVE" && a.personId === this.identity?.personId,
        ) ??
        this.state.assignments.find(
          (a) => a.state === "ACTIVE" && a.personId === "user",
        ) ??
        this.state.assignments.find((a) => a.state === "ACTIVE"),
      revoked = this.state.assignments.find((a) => a.state === "REVOKED"),
      chosen = kind === "REVOKED" ? revoked : a;
    const p = this.state.people.find((p) => p.id === chosen?.personId),
      card = this.state.cards.find((c) => c.id === chosen?.cardId),
      special = [
        "UNKNOWN",
        "FORCED_OPEN",
        "HELD_OPEN",
        "READER_FAULT",
      ].includes(kind);
    const granted =
      !special &&
      kind !== "REVOKED" &&
      !!chosen &&
      !!p &&
      !card?.revoked &&
      p.status === "ACTIVE" &&
      Date.parse(chosen.start) <= Date.parse(this.clock) &&
      Date.parse(chosen.expiry) > Date.parse(this.clock) &&
      (card?.privileged || this.scheduleAllows());
    const evt: AccessEvent = {
      id: this.id("EVT"),
      at: this.clock,
      personId: special ? undefined : p?.id,
      person:
        special || !p ? "Unknown credential" : p.firstName + " " + p.lastName,
      reference: special ? "" : (p?.reference ?? ""),
      card: special ? "008899" : (card?.number ?? "000000"),
      assignmentId: special ? undefined : chosen?.id,
      controller: "controller-1",
      door: "door-1",
      direction: kind === "EXIT" ? "EXIT" : "ENTRY",
      result: granted ? "GRANTED" : "DENIED",
      type: special ? kind : kind === "REVOKED" ? "REVOKED_CARD" : "CARD_SWIPE",
      simulated: true,
    };
    this.state.events.unshift(evt);
    if (!granted)
      this.notice(
        kind === "UNKNOWN"
          ? "Unknown card attempt"
          : kind === "REVOKED"
            ? "Revoked card attempt"
            : special
              ? kind
              : "Access denied",
        `${evt.card} at ${evt.direction}; simulated hardware event.`,
        kind === "REVOKED" || kind === "FORCED_OPEN" ? "CRITICAL" : "WARNING",
      );
    this.emit();
  }
  subscribe(listener: () => void) {
    this.listeners.add(listener);
    if (!this.timer)
      this.timer = setInterval(() => {
        if (
          this.identity &&
          can(this.identity.role, "dashboard") &&
          !this.controls.offline
        ) {
          this.clock = plus(this.clock, 5 / 86400);
          this.makeEvent(Math.random() > 0.8 ? "UNKNOWN" : "ENTRY");
        }
      }, 12000);
    return () => {
      this.listeners.delete(listener);
      if (!this.listeners.size && this.timer) {
        clearInterval(this.timer);
        this.timer = undefined;
      }
    };
  }
  private retain() {
    const now = Date.parse(this.clock);
    for (const a of this.state.assignments.filter(
      (a) =>
        ["ACTIVE", "PENDING"].includes(a.state) && Date.parse(a.expiry) <= now,
    )) {
      a.state = "EXPIRED";
      this.sync(a, "DISABLED");
      const v = this.state.visits.find((v) => v.assignmentId === a.id);
      if (v) {
        v.state = "ENDED";
        const p = this.state.people.find((p) => p.id === v.personId);
        if (p) {
          p.accessEndedAt = a.expiry;
          p.purgeAt = plus(a.expiry, 45);
        }
        const card = this.state.cards.find((c) => c.id === a.cardId)!;
        card.poolState = "QUARANTINED";
      }
      this.audit("ASSIGNMENT_EXPIRED", a.id);
    }
    for (const v of this.state.visits.filter(
      (v) => v.state !== "ENDED" && Date.parse(v.expiry) <= now,
    )) {
      v.state = "ENDED";
      const p = this.state.people.find((p) => p.id === v.personId);
      if (p) {
        p.accessEndedAt = v.expiry;
        p.purgeAt = plus(v.expiry, 45);
      }
    }
    for (const p of this.state.people.filter(
      (p) => p.type === "VISITOR" && p.accessEndedAt,
    )) {
      p.purgeAt ??= plus(p.accessEndedAt!, 45);
      if (!p.restored && now >= Date.parse(plus(p.accessEndedAt!, 15)))
        p.deletedAt ??= plus(p.accessEndedAt!, 15);
      if (now >= Date.parse(p.purgeAt)) {
        this.revokeUrl(p.photo);
        this.state.events
          .filter((e) => e.personId === p.id)
          .forEach((e) => {
            e.person = "Anonymous visitor";
            e.reference = "";
          });
        this.state.audits.forEach((a) => {
          if (
            a.personId === p.id ||
            a.actor === p.firstName + " " + p.lastName
          ) {
            a.actor = "Anonymous visitor";
            a.target = "Anonymous reference";
          }
        });
        this.state.visits
          .filter((v) => v.personId === p.id)
          .forEach((v) => {
            v.purpose = "Purged";
            v.host = "Purged";
            if (v.approval) v.approval.reason = undefined;
          });
        this.state.notifications = this.state.notifications.filter(
          (n) => n.personId !== p.id,
        );
        this.state.identities = this.state.identities.filter(
          (i) => i.personId !== p.id,
        );
      }
    }
    this.state.people = this.state.people.filter(
      (p) => !p.purgeAt || now < Date.parse(p.purgeAt),
    );
    for (const e of this.state.events) {
      const original = e.at ?? e.receivedAt ?? this.clock;
      e.purgeAt ??= plus(original, 120);
      if (!e.restored && now >= Date.parse(plus(original, 90)))
        e.deletedAt ??= plus(original, 90);
    }
    this.state.events = this.state.events.filter(
      (e) => now < Date.parse(e.purgeAt!),
    );
    for (const a of this.state.audits) {
      const soft = calendarMonths(a.at, 6);
      a.purgeAt ??= plus(soft, 30);
      if (!a.restored && now >= Date.parse(soft)) a.deletedAt ??= soft;
    }
    this.state.audits = this.state.audits.filter(
      (a) => now < Date.parse(a.purgeAt!),
    );
  }
  private filtered(family: "events" | "audits", f: Filters) {
    return (family === "events" ? this.state.events : this.state.audits)
      .filter((row) => !row.deletedAt)
      .filter((row) =>
        Object.entries(f).every(([key, value]) => {
          if (!value || ["page", "pageSize"].includes(key)) return true;
          if (key === "from" || key === "to") {
            if (!row.at) return false;
            const boundary = /^\d{4}-\d{2}-\d{2}$/.test(String(value))
              ? String(value) + "T00:00:00+01:00"
              : String(value);
            return key === "from"
              ? Date.parse(row.at) >= Date.parse(boundary)
              : Date.parse(row.at) < Date.parse(plus(boundary, 1));
          }
          const v = (row as unknown as Record<string, unknown>)[key];
          if (
            ["result", "direction", "controller", "door", "type"].includes(key)
          )
            return v === value;
          return String(v ?? "")
            .toLowerCase()
            .includes(String(value).toLowerCase());
        }),
      );
  }
  async records(family: "events" | "audits", filters: Filters) {
    await this.wait();
    this.require("logs");
    this.retain();
    const rows = this.filtered(family, filters),
      page = Math.max(1, filters.page ?? 1),
      pageSize = Math.min(100, Math.max(1, filters.pageSize ?? 20));
    return copy({
      items: rows.slice((page - 1) * pageSize, page * pageSize),
      total: rows.length,
      page,
      pageSize,
    });
  }
  async exportRecords(
    family: "events" | "audits",
    filters: Filters,
    format: "csv" | "pdf",
  ) {
    await this.wait();
    this.require("export");
    this.retain();
    const rows = copy(this.filtered(family, filters));
    const keys =
      family === "events"
        ? [
            "id",
            "at",
            "person",
            "reference",
            "card",
            "assignmentId",
            "controller",
            "door",
            "direction",
            "result",
            "type",
          ]
        : ["id", "at", "actor", "action", "target", "result"];
    let blob: Blob;
    if (format === "csv")
      blob = new Blob(
        [
          "\uFEFF" +
            keys.map(csvCell).join(",") +
            "\r\n" +
            rows
              .map((r) =>
                keys
                  .map((k) =>
                    csvCell((r as unknown as Record<string, unknown>)[k]),
                  )
                  .join(","),
              )
              .join("\r\n"),
        ],
        { type: "text/csv;charset=utf-8" },
      );
    else {
      const { jsPDF } = await import("jspdf");
      const doc = new jsPDF();
      doc.setFontSize(15);
      doc.text("IFT E-Library • filtered demo records", 14, 18);
      doc.setFontSize(9);
      let y = 28;
      doc.text(
        `${family}: ${rows.length} records | Africa/Lagos | simulated`,
        14,
        y,
      );
      y += 10;
      for (const row of rows) {
        const lines = doc.splitTextToSize(
          keys
            .map(
              (k) =>
                `${k}: ${String((row as unknown as Record<string, unknown>)[k] ?? "")}`,
            )
            .join(" | "),
          180,
        ) as string[];
        if (y + lines.length * 4 > 280) {
          doc.addPage();
          y = 18;
        }
        doc.text(lines, 14, y);
        y += lines.length * 4 + 5;
      }
      blob = doc.output("blob");
    }
    this.audit(
      "EXPORT_" + format.toUpperCase(),
      family + ": " + rows.length + " filtered records",
    );
    this.emit();
    return blob;
  }
  async recovery() {
    await this.wait();
    this.require("recovery");
    this.retain();
    return copy({
      people: this.state.people.filter((p) => p.deletedAt),
      events: this.state.events.filter((e) => e.deletedAt),
      audits: this.state.audits.filter((a) => a.deletedAt),
    });
  }
  async startScan() {
    await this.wait();
    this.require("issue");
    const s: ScanSession = {
      id: this.id("SCAN"),
      state: "WAITING",
      expiresAt: new Date(Date.now() + 30000).toISOString(),
    };
    this.scans.set(s.id, s);
    this.audit("SCAN_STARTED", s.id);
    return copy(s);
  }
  async pollScan(id: string) {
    await this.wait();
    this.require("issue");
    const s = this.scans.get(id);
    if (!s) throw new ServiceError("VALIDATION_ERROR", "Unknown scan session.");
    if (
      ["WAITING", "DETECTED"].includes(s.state) &&
      Date.now() >= Date.parse(s.expiresAt)
    )
      s.state = "TIMED_OUT";
    else if (s.state === "WAITING") {
      s.state = "DETECTED";
      s.number = "00" + String(++this.seq).padStart(6, "0");
    }
    return copy(s);
  }
  async confirmScan(id: string) {
    const s = await this.pollScan(id);
    if (s.state !== "DETECTED" || !s.number)
      throw new ServiceError(
        "CONFLICT",
        "Scan expired or cancelled. Start a new enrolment session.",
      );
    this.scans.get(id)!.state = "CONFIRMED";
    this.audit("SCAN_CONFIRMED", id);
    return s.number;
  }
  async cancelScan(id: string) {
    await this.wait();
    this.require("issue");
    const s = this.scans.get(id);
    if (s) {
      s.state = "CANCELLED";
      this.audit("SCAN_CANCELLED", id);
    }
  }
  async resetDemo() {
    if (!this.demoEnabled)
      throw new ServiceError("FORBIDDEN", "Reset is development-only.");
    this.urls.forEach((url) => this.revokeUrl(url));
    this.clock = "2026-10-03T10:00:00.000Z";
    this.state = seed(this.clock);
    this.identity = null;
    this.passwords.clear();
    this.scans.clear();
    this.privilegedHistory = new Set(["card-super"]);
    this.controls = {
      offline: false,
      failDisable: false,
      delayActivation: false,
      unlockTimeout: false,
    };
    this.settings = {
      payment:
        "Fictional demo only: pay at the example IFT library desk. No real bank account is configured.",
      uploadLimit: 5 * 1048576,
      privilegedCardId: "card-super",
    };
    this.schedule = {
      weekdays: [1, 2, 3, 4, 5],
      opens: "08:00",
      closes: "18:00",
      exceptions: [],
      sync: "SYNCHRONIZED",
    };
    this.emit();
  }
}
