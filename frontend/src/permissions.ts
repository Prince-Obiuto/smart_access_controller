import type { Role } from "./models";
import matrix from "../../shared/permissions.json";
export type Capability = keyof typeof matrix;
export const permissions = matrix as Record<Capability, readonly Role[]>;
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
