import type { AccessControlService } from "./accessControlService";
import { ServiceError, type ErrorCode } from "./accessControlService";
import type {
  Command,
  Registration,
  Identity,
  Snapshot,
  Filters,
  Page,
  AccessEvent,
  Audit,
  ScanSession,
  OperationResult,
} from "../models";

/** Same-origin session transport. No identity selector, tokens in storage or
 * automatic command retries. A lost write response may have committed. */
export function createHttpService(baseUrl: string): AccessControlService {
  const base = baseUrl.replace(/\/$/, "");
  async function request<T>(
    path: string,
    options: RequestInit = {},
    blob = false,
  ): Promise<T> {
    try {
      const response = await fetch(base + path, {
        ...options,
        credentials: "same-origin",
        cache: "no-store",
      });
      if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        throw new ServiceError(
          (error.code ??
            (response.status === 401
              ? "UNAUTHENTICATED"
              : response.status === 403
                ? "FORBIDDEN"
                : "NOT_CONNECTED")) as ErrorCode,
          error.message ?? `API request failed (${response.status}).`,
          error.field,
        );
      }
      return (blob ? await response.blob() : await response.json()) as T;
    } catch (error) {
      if (error instanceof ServiceError) throw error;
      throw new ServiceError(
        "NOT_CONNECTED",
        options.method && options.method !== "GET"
          ? "API connection lost. The operation may have committed; check its status before repeating."
          : "Unable to reach the backend. Check the local server and retry.",
      );
    }
  }
  async function write<T>(
    path: string,
    body: unknown,
    blob = false,
    method = "POST",
  ): Promise<T> {
    const { csrfToken } = await request<{ csrfToken: string }>("/auth/csrf");
    const form = body instanceof FormData;
    return request<T>(
      path,
      {
        method,
        headers: {
          "X-CSRF-Token": csrfToken,
          "Idempotency-Key": crypto.randomUUID(),
          ...(!form ? { "Content-Type": "application/json" } : {}),
        },
        body: form ? body : JSON.stringify(body),
      },
      blob,
    );
  }
  function queries(filters: Filters) {
    const params = new URLSearchParams();
    Object.entries(filters).forEach(([k, v]) => {
      if (v !== undefined && v !== "") params.set(k, String(v));
    });
    return params.toString();
  }
  return {
    mode: "http",
    recoveryOptions: () => request("/auth/recovery-options"),
    requestPasswordReset: (email) => write("/auth/forgot-password", { email }),
    resetPassword: (token, password, confirmation) =>
      write("/auth/reset-password", { token, password, confirmation }),
    adminRecovery: (personId, identityConfirmed) =>
      write("/auth/admin-recovery", { personId, identityConfirmed }),
    currentIdentity: () => request<Identity | null>("/auth/me"),
    login: (email, password) =>
      write<Identity>("/auth/login", { email, password }),
    logout: async () => {
      await write("/auth/logout", {});
    },
    register: (data: Registration) => {
      const form = new FormData();
      const { photo, ...payload } = data;
      form.set("payload", JSON.stringify(payload));
      if (photo) form.set("photo", photo);
      return write<Identity>("/auth/register", form);
    },
    demoIdentities: async () => [],
    selectDemo: async () => {
      throw new ServiceError(
        "FORBIDDEN",
        "HTTP mode identity comes from the authenticated server session.",
      );
    },
    snapshot: () => request<Snapshot>("/workspace"),
    execute: (command: Command) => {
      if (command.type === "submit") {
        const form = new FormData();
        form.set("file", command.file);
        return write<OperationResult>(
          `/applications/${encodeURIComponent(command.id)}/evidence`,
          form,
        );
      }
      if (command.type === "profile") {
        const form = new FormData();
        form.set("phone", command.phone);
        if (command.photo) form.set("photo", command.photo);
        return write<OperationResult>("/me/profile", form);
      }
      if (command.type === "person") {
        // The mock view contains internal/retention/sync fields. Never allow those
        // to become server mass-assignment inputs when spreading an edit record.
        const p = command.data;
        return write<OperationResult>("/commands", {
          ...command,
          data: {
            type: p.type,
            reference: p.reference,
            firstName: p.firstName,
            lastName: p.lastName,
            email: p.email,
            phone: p.phone,
            department: p.department,
            status: p.status,
            start: p.start,
            expiry: p.expiry,
          },
        });
      }
      return write<OperationResult>("/commands", command);
    },
    records: (family, filters) =>
      request<Page<AccessEvent | Audit>>(
        `/records/${family}?${queries(filters)}`,
      ),
    exportRecords: (family, filters, format) =>
      write<Blob>(`/exports/${family}/${format}`, filters, true),
    recovery: () => request("/recovery"),
    startScan: () => write<ScanSession>("/enrolment-sessions", {}),
    pollScan: (id) =>
      request<ScanSession>(`/enrolment-sessions/${encodeURIComponent(id)}`),
    confirmScan: (id) =>
      write<string>(
        `/enrolment-sessions/${encodeURIComponent(id)}/confirm`,
        {},
      ),
    cancelScan: async (id) => {
      await write(`/enrolment-sessions/${encodeURIComponent(id)}/cancel`, {});
    },
    subscribe: (listener) => {
      const timer = setInterval(() => {
        if (document.visibilityState === "visible") listener();
      }, 4000);
      const visible = () => {
        if (document.visibilityState === "visible") listener();
      };
      document.addEventListener("visibilitychange", visible);
      return () => {
        clearInterval(timer);
        document.removeEventListener("visibilitychange", visible);
      };
    },
    resetDemo: async () => {
      throw new ServiceError(
        "FORBIDDEN",
        "Durable HTTP data cannot be reset by the frontend demo controls.",
      );
    },
  };
}
