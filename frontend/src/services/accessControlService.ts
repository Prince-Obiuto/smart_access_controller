import type {
  Identity,
  Snapshot,
  Registration,
  Command,
  OperationResult,
  Filters,
  AccessEvent,
  Audit,
  Page,
  ScanSession,
} from "../models";
export type ErrorCode =
  | "FORBIDDEN"
  | "VALIDATION_ERROR"
  | "CONFLICT"
  | "CONTROLLER_UNAVAILABLE"
  | "UNAUTHENTICATED"
  | "NOT_CONNECTED"
  | "INVALID_RESET"
  | "RATE_LIMITED"
  | "DELIVERY_UNAVAILABLE";
export class ServiceError extends Error {
  constructor(
    public code: ErrorCode,
    message: string,
    public field?: string,
  ) {
    super(message);
    this.name = "ServiceError";
  }
}
export interface ControllerAccess {
  enabled: boolean;
  expired: boolean;
  expiresAt?: string;
  template: number;
  cards: string[];
  operations: { id: string; card: string; desired: string; status: string; message: string }[];
}
export interface ControllerCardRead {
  card: string;
  readAt: string;
  record: null | { type: number; valid: number; doorRights: Record<string, number>; plansByDoor: Record<string, number[]>; start: number[]; expiry: number[] };
}
export interface AccessControlService {
  readonly mode: "mock" | "http";
  unlockStatus(): Promise<{ doorId: string; configuredSeconds: number; requiredSeconds: number; ready: boolean; sensor: string }>;
  remoteUnlock(): Promise<{ operationId: string; status: string; readbackVerified: boolean; physicalState: string; message: string }>;
  configureUnlockDuration(): Promise<{ operationId: string; status: string; readbackVerified: boolean; physicalState: string; message: string }>;
  controllerAccess(): Promise<ControllerAccess>;
  authorizeControllerCards(cards: { number: string; kind: "PERSONAL" | "VISITOR"; personIds: string[] }[]): Promise<ControllerAccess>;
  closeControllerAccess(): Promise<ControllerAccess>;
  readControllerCard(number: string): Promise<ControllerCardRead>;
  executeControllerOperation(id: string): Promise<{ operationId: string; status: string; readbackVerified: boolean; message?: string }>;
  currentIdentity(): Promise<Identity | null>;
  login(email: string, password: string): Promise<Identity>;
  logout(): Promise<void>;
  recoveryOptions(): Promise<{ available: boolean; contact: string }>;
  requestPasswordReset(email: string): Promise<{ message: string }>;
  resetPassword(
    token: string,
    password: string,
    confirmation: string,
  ): Promise<{ message: string }>;
  adminRecovery(
    personId: string,
    identityConfirmed: boolean,
  ): Promise<{ message: string }>;
  register(data: Registration): Promise<Identity>;
  demoIdentities(): Promise<Identity[]>;
  selectDemo(id: string): Promise<Identity>;
  snapshot(): Promise<Snapshot>;
  execute(command: Command): Promise<OperationResult>;
  records(
    family: "events" | "audits",
    filters: Filters,
  ): Promise<Page<AccessEvent | Audit>>;
  exportRecords(
    family: "events" | "audits",
    filters: Filters,
    format: "csv" | "pdf",
  ): Promise<Blob>;
  recovery(): Promise<{
    people: Snapshot["people"];
    events: AccessEvent[];
    audits: Audit[];
  }>;
  startScan(): Promise<ScanSession>;
  pollScan(id: string): Promise<ScanSession>;
  confirmScan(id: string): Promise<string>;
  cancelScan(id: string): Promise<void>;
  subscribe(listener: () => void): () => void;
  resetDemo(): Promise<void>;
}
export function validEmail(email: string, university = false) {
  const value = email.trim();
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value)) return false;
  return !university || value.split("@")[1].toLowerCase() === "futo.edu.ng";
}
export function validateDates(start: string, expiry: string) {
  if (
    !start ||
    !expiry ||
    !Number.isFinite(Date.parse(start)) ||
    !Number.isFinite(Date.parse(expiry)) ||
    Date.parse(expiry) <= Date.parse(start)
  )
    throw new ServiceError(
      "VALIDATION_ERROR",
      "Access expiry must follow the start date.",
    );
}
export function csvCell(value: unknown) {
  let s = String(value ?? "");
  if (/^[\s]*[=+\-@\t\r]/.test(s)) s = "'" + s;
  return '"' + s.replaceAll('"', '""') + '"';
}
export function calendarMonths(iso: string, months: number) {
  const d = new Date(iso),
    day = d.getUTCDate();
  d.setUTCDate(1);
  d.setUTCMonth(d.getUTCMonth() + months);
  const last = new Date(
    Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 0),
  ).getUTCDate();
  d.setUTCDate(Math.min(day, last));
  return d.toISOString();
}
