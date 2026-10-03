import { MockService } from "./mockService";
import { createHttpService } from "./httpService";
export const service =
  import.meta.env.VITE_SERVICE_MODE === "http"
    ? createHttpService(import.meta.env.VITE_API_BASE_URL ?? "/api")
    : new MockService();
