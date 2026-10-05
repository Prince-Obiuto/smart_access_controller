export type Role = "SUPER_ADMIN" | "ADMIN" | "MANAGER" | "MANAGEMENT" | "USER";
export type PersonType = "STUDENT" | "STAFF" | "VISITOR";
export type SyncState = "PENDING" | "SYNCHRONIZED" | "FAILED" | "OUT_OF_SYNC";
export interface Identity {
  id: string;
  personId: string;
  role: Role;
  name: string;
  email: string;
}
export interface Person {
  id: string;
  type: PersonType;
  reference: string;
  firstName: string;
  lastName: string;
  email: string;
  phone: string;
  department: string;
  photo?: string;
  status: "ACTIVE" | "DISABLED";
  start?: string;
  expiry?: string;
  sync: SyncState;
  accessEndedAt?: string;
  deletedAt?: string;
  purgeAt?: string;
  restored?: boolean;
}
export interface Card {
  id: string;
  number: string;
  kind: "PERSONAL" | "VISITOR";
  revoked: boolean;
  poolState?:
    | "AVAILABLE_DISABLED"
    | "ASSIGNMENT_PENDING"
    | "ASSIGNED"
    | "RETURN_PENDING_DISABLE"
    | "QUARANTINED"
    | "MISSING"
    | "DAMAGED";
  disabled: boolean;
  privileged: boolean;
}
export interface Confirmation {
  controllerId: string;
  confirmed: boolean;
  state: SyncState;
  intendedState: "ENABLED" | "DISABLED" | "UPDATED";
  lastConfirmedState?: string;
  at?: string;
}
export interface Assignment {
  id: string;
  personId: string;
  cardId: string;
  start: string;
  expiry: string;
  state: "PENDING" | "ACTIVE" | "REVOKED" | "EXPIRED" | "CLOSED";
  sync: SyncState;
  confirmations: Confirmation[];
  reason?: "MISSING" | "DAMAGED" | "NOT_PERMITTED";
  returnedAt?: string;
}
export interface Evidence {
  name: string;
  type: string;
  size: number;
  url: string;
}
export interface History {
  at: string;
  actor: string;
  action: string;
  reason?: string;
}
export interface Application {
  id: string;
  personId: string;
  kind: "FIRST" | "REPLACEMENT";
  state:
    | "DRAFT"
    | "SUBMITTED"
    | "REJECTED"
    | "APPROVED"
    | "ISSUANCE_PENDING"
    | "ISSUED";
  evidence?: Evidence;
  history: History[];
  reviewer?: string;
  reviewedAt?: string;
  issuer?: string;
  issuedAt?: string;
  cardId?: string;
  reason?: string;
  assignmentId?: string;
}
export interface Visit {
  id: string;
  personId: string;
  purpose: string;
  host: string;
  start: string;
  expiry: string;
  state: "REQUESTED" | "APPROVED" | "PENDING" | "ACTIVE" | "ENDED";
  approval?: History;
  assignmentId?: string;
  returned: boolean;
}
export interface Device {
  id: string;
  name: string;
  model: string;
  online: boolean;
  lastCommunication?: string;
  doorId: string;
  door: string;
  sensor: "OPEN" | "CLOSED" | "UNKNOWN";
}
export interface AccessEvent {
  id: string;
  at: string;
  personId?: string;
  person: string;
  reference: string;
  card: string;
  assignmentId?: string;
  controller: string;
  door: string;
  direction: "ENTRY" | "EXIT";
  result: "GRANTED" | "DENIED";
  type: string;
  simulated: true;
  deletedAt?: string;
  purgeAt?: string;
  restored?: boolean;
}
export interface Audit {
  id: string;
  at: string;
  actor: string;
  action: string;
  target: string;
  result: string;
  personId?: string;
  deletedAt?: string;
  purgeAt?: string;
  restored?: boolean;
}
export interface Notice {
  id: string;
  at: string;
  severity: "INFO" | "WARNING" | "CRITICAL";
  title: string;
  detail: string;
  personId?: string;
  read: boolean;
}
export interface Schedule {
  weekdays: number[];
  opens: string;
  closes: string;
  exceptions: {
    date: string;
    closed: boolean;
    opens?: string;
    closes?: string;
    label: string;
  }[];
  sync: SyncState;
}
export interface Settings {
  payment: string;
  uploadLimit: number;
  privilegedCardId?: string;
}
export interface DemoControls {
  offline: boolean;
  failDisable: boolean;
  delayActivation: boolean;
  unlockTimeout: boolean;
}
export interface Snapshot {
  controllerMode?: "SIMULATED";
  simulationControlsEnabled?: boolean;
  identity: Identity;
  people: Person[];
  cards: Card[];
  assignments: Assignment[];
  applications: Application[];
  visits: Visit[];
  devices: Device[];
  notifications: Notice[];
  schedule: Schedule;
  settings: Settings;
  controls: DemoControls;
  clock: string;
}
export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  pageSize: number;
}
export interface Filters {
  from?: string;
  to?: string;
  person?: string;
  reference?: string;
  card?: string;
  controller?: string;
  door?: string;
  direction?: string;
  result?: string;
  type?: string;
  actor?: string;
  action?: string;
  target?: string;
  page?: number;
  pageSize?: number;
}
export interface Registration {
  type: PersonType;
  reference: string;
  firstName: string;
  lastName: string;
  email: string;
  phone: string;
  department: string;
  password: string;
  photo?: File;
}
export interface OperationResult {
  intendedState: string;
  confirmations: Confirmation[];
  result: "CONFIRMED" | "PENDING" | "FAILED" | "UNCERTAIN";
  message: string;
}
export interface ScanSession {
  id: string;
  state: "WAITING" | "DETECTED" | "CANCELLED" | "CONFIRMED" | "TIMED_OUT";
  expiresAt: string;
  number?: string;
}
export type Command =
  | { type: "pool-card"; number: string }
  | { type: "profile"; phone: string; photo?: File }
  | { type: "person"; id?: string; data: Omit<Person, "id" | "sync"> }
  | { type: "dates"; personId: string; start: string; expiry: string }
  | { type: "application"; kind: "FIRST" | "REPLACEMENT" }
  | { type: "submit"; id: string; file: File }
  | { type: "review"; id: string; approve: boolean; reason?: string }
  | { type: "issue"; id: string; number: string }
  | {
      type: "revoke";
      assignmentId: string;
      reason: "MISSING" | "DAMAGED" | "NOT_PERMITTED";
      own?: boolean;
    }
  | {
      type: "retry-sync";
      assignmentId?: string;
      personId?: string;
      schedule?: boolean;
    }
  | {
      type: "visit";
      purpose: string;
      host: string;
      start: string;
      expiry: string;
    }
  | { type: "visitor-approve"; id: string; note: string }
  | { type: "visitor-assign"; id: string; cardId: string }
  | {
      type: "visitor-return";
      id: string;
      returned: boolean;
      condition: "GOOD" | "MISSING" | "DAMAGED";
    }
  | { type: "schedule"; schedule: Schedule }
  | { type: "unlock"; doorId: string; confirmed: boolean }
  | { type: "settings"; settings: Settings }
  | { type: "role"; personId: string; role: Role }
  | { type: "designate"; cardId: string }
  | { type: "device"; id: string; name: string }
  | { type: "restore"; family: "people" | "events" | "audits"; id: string }
  | { type: "notice-read"; id: string }
  | { type: "demo-controls"; controls: DemoControls }
  | { type: "demo-clock"; clock: string }
  | {
      type: "demo-event";
      event:
        | "ENTRY"
        | "EXIT"
        | "UNKNOWN"
        | "REVOKED"
        | "FORCED_OPEN"
        | "HELD_OPEN"
        | "READER_FAULT";
    };
