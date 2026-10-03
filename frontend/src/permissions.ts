import type { Role } from "./models";
export type Capability =
  | "dashboard"
  | "review"
  | "people"
  | "issue"
  | "visitors"
  | "unlock"
  | "schedules"
  | "logs"
  | "export"
  | "settings"
  | "recovery";
const operators: Role[] = ["SUPER_ADMIN", "ADMIN"];
export const permissions: Record<Capability, readonly Role[]> = {
  dashboard: [...operators, "MANAGER"],
  review: [...operators, "MANAGER"],
  people: operators,
  issue: operators,
  visitors: operators,
  unlock: operators,
  schedules: operators,
  logs: [...operators, "MANAGER", "MANAGEMENT"],
  export: [...operators, "MANAGEMENT"],
  settings: ["SUPER_ADMIN"],
  recovery: ["SUPER_ADMIN"],
};
export const can = (role: Role, capability: Capability) =>
  permissions[capability].includes(role);
export const landing = (role: Role) =>
  role === "USER"
    ? "/my-access"
    : role === "MANAGEMENT"
      ? "/cards-activity"
      : "/dashboard";
export const roles: Role[] = [
  "SUPER_ADMIN",
  "ADMIN",
  "MANAGER",
  "MANAGEMENT",
  "USER",
];
