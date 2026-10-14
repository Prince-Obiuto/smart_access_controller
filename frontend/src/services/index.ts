import { MockService } from "./mockService";
import { createHttpService } from "./httpService";
import type { AccessControlService } from "./accessControlService";
export const service: AccessControlService =
  import.meta.env.VITE_SERVICE_MODE === "mock"
    ? new MockService()
    : createHttpService(import.meta.env.VITE_API_BASE_URL ?? "/api");
