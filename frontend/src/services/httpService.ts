import type { AccessControlService } from "./accessControlService";
import { ServiceError } from "./accessControlService";
// Deliberately disconnected. Proposed mappings are documented; no Hikvision endpoints are assumed.
export function createHttpService(baseUrl: string): AccessControlService {
  const disconnected = async (): Promise<never> => {
    throw new ServiceError(
      "NOT_CONNECTED",
      `HTTP adapter is not connected. Configure the future FastAPI contract at ${baseUrl}.`,
    );
  };
  return {
    mode: "http",
    currentIdentity: disconnected,
    login: disconnected,
    logout: disconnected,
    register: disconnected,
    demoIdentities: async () => [],
    selectDemo: disconnected,
    snapshot: disconnected,
    execute: disconnected,
    records: disconnected,
    exportRecords: disconnected,
    recovery: disconnected,
    startScan: disconnected,
    pollScan: disconnected,
    confirmScan: disconnected,
    cancelScan: disconnected,
    subscribe: () => () => {},
    resetDemo: disconnected,
  };
}
