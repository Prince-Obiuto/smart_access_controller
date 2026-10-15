import { useEffect, useRef, useId, useState, type ReactNode } from "react";
export function Icon({ name, size = 20 }: { name: string; size?: number }) {
  const paths: Record<string, ReactNode> = {
    dashboard: (
      <>
        <rect x="3" y="3" width="7" height="7" rx="1" />
        <rect x="14" y="3" width="7" height="7" rx="1" />
        <rect x="3" y="14" width="7" height="7" rx="1" />
        <rect x="14" y="14" width="7" height="7" rx="1" />
      </>
    ),
    people: (
      <>
        <circle cx="9" cy="8" r="3" />
        <path d="M3 21v-3a6 6 0 0 1 12 0v3m1-16a3 3 0 0 1 0 6m2 4a5 5 0 0 1 3 5" />
      </>
    ),
    card: (
      <>
        <rect x="2" y="5" width="20" height="14" rx="3" />
        <path d="M2 10h20M6 15h4" />
      </>
    ),
    clock: (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="M12 7v5l3 2" />
      </>
    ),
    bell: (
      <>
        <path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9m-12 12h6" />
      </>
    ),
    settings: (
      <>
        <circle cx="12" cy="12" r="3" />
        <path d="m9 3 6 0 1 3 3 1 2 5-2 5-3 1-1 3H9l-1-3-3-1-2-5 2-5 3-1Z" />
      </>
    ),
    arrow: <path d="M4 12h16m-6-6 6 6-6 6" />,
    menu: <path d="M3 6h18M3 12h18M3 18h18" />,
    close: <path d="m6 6 12 12M6 18 18 6" />,
    check: <path d="m5 12 4 4 10-10" />,
    shield: (
      <>
        <path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6Z" />
        <path d="m8 12 3 3 5-5" />
      </>
    ),
    door: (
      <>
        <path d="M4 21h16M6 21V3h12v18M14 12h1" />
      </>
    ),
    download: (
      <>
        <path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5" />
      </>
    ),
    logout: (
      <>
        <path d="M9 3H4v18h5m6-15 6 6-6 6m-8-6h14" />
      </>
    ),
    search: (
      <>
        <circle cx="10" cy="10" r="6" />
        <path d="m15 15 6 6" />
      </>
    ),
    book: (
      <>
        <path d="M12 6C8 3 4 3 2 4v15c4-1 6-1 10 2 4-3 6-3 10-2V4c-4-1-6-1-10 2v15" />
      </>
    ),
    warning: (
      <>
        <path d="m12 3 10 18H2Z" />
        <path d="M12 9v5m0 3v1" />
      </>
    ),
  };
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {paths[name] ?? paths.card}
    </svg>
  );
}
export const pretty = (s: string) =>
  s
    .replaceAll("_", " ")
    .toLowerCase()
    .replace(/\b\w/g, (c) => c.toUpperCase());
export function Badge({ value }: { value: string }) {
  const bad = /FAILED|REVOKED|CRITICAL|DENIED|MISSING|DAMAGED|REJECTED/.test(
      value,
    ),
    warn = /PENDING|UNKNOWN|WARNING|OUT_OF_SYNC|QUARANTINED|UNCERTAIN|REQUIRES_RECONCILIATION/.test(
      value,
    );
  return (
    <span className={`badge ${bad ? "bad" : warn ? "warn" : "good"}`}>
      <span className="status-dot" />
      {pretty(value)}
    </span>
  );
}
export function Time({ value }: { value?: string }) {
  return (
    <>
      {value
        ? new Date(value).toLocaleString("en-GB", {
            timeZone: "Africa/Lagos",
            day: "2-digit",
            month: "short",
            hour: "2-digit",
            minute: "2-digit",
          })
        : "—"}
    </>
  );
}
export function Empty({
  children = "No records found.",
}: {
  children?: ReactNode;
}) {
  return (
    <div className="empty">
      <Icon name="book" size={30} />
      <p>{children}</p>
    </div>
  );
}
export function Action({
  children,
  onClick,
  className = "",
  disabled = false,
}: {
  children: ReactNode;
  onClick: () => Promise<unknown> | void;
  className?: string;
  disabled?: boolean;
}) {
  const [busy, setBusy] = useState(false);
  return (
    <button
      type="button"
      className={className}
      disabled={disabled || busy}
      onClick={async () => {
        setBusy(true);
        try {
          await onClick();
        } catch {
          /* reported by caller */
        } finally {
          setBusy(false);
        }
      }}
    >
      {busy ? "Working…" : children}
    </button>
  );
}
export interface Field {
  name: string;
  label: string;
  type?: string;
  options?: { value: string; label: string }[];
  value?: string;
  required?: boolean;
  hint?: string;
  accept?: string;
  min?: string;
  max?: string;
}
export function FormFields({
  fields,
  onSubmit,
  submit = "Save",
  children,
}: {
  fields: Field[];
  onSubmit: (data: FormData) => Promise<unknown>;
  submit?: string;
  children?: ReactNode;
}) {
  const formId = useId();
  const [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  return (
    <form
      onSubmit={async (e) => {
        e.preventDefault();
        setError("");
        setBusy(true);
        try {
          await onSubmit(new FormData(e.currentTarget));
        } catch (e) {
          setError(e instanceof Error ? e.message : "Action failed.");
        } finally {
          setBusy(false);
        }
      }}
    >
      <div className="form-grid">
        {fields.map((f) => (
          <label key={f.name}>
            {f.label}
            {f.options ? (
              <select
                aria-label={f.label}
                aria-describedby={
                  f.hint ? `${formId}-${f.name}-hint` : undefined
                }
                name={f.name}
                defaultValue={f.value}
                required={f.required ?? true}
              >
                {f.options.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            ) : f.type === "textarea" ? (
              <textarea
                aria-label={f.label}
                aria-describedby={
                  f.hint ? `${formId}-${f.name}-hint` : undefined
                }
                name={f.name}
                defaultValue={f.value}
                required={f.required ?? true}
              />
            ) : (
              <input
                aria-label={f.label}
                aria-describedby={
                  f.hint ? `${formId}-${f.name}-hint` : undefined
                }
                name={f.name}
                type={f.type ?? "text"}
                defaultValue={f.type === "file" ? undefined : f.value}
                required={f.required ?? true}
                accept={f.accept}
                min={f.min}
                max={f.max}
              />
            )}{" "}
            {f.hint && <small id={`${formId}-${f.name}-hint`}>{f.hint}</small>}
          </label>
        ))}
      </div>
      {children}
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      <button className="primary form-submit" disabled={busy}>
        {busy ? "Working…" : submit}
      </button>
    </form>
  );
}
export function Modal({
  title,
  children,
  onClose,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current!;
    const previous = document.activeElement as HTMLElement | null;
    d.showModal();
    return () => {
      d.close();
      previous?.focus();
    };
  }, []);
  return (
    <dialog
      ref={ref}
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
      aria-labelledby="dialog-title"
    >
      <div className="dialog-head">
        <h2 id="dialog-title">{title}</h2>
        <button
          className="icon-button"
          aria-label="Close dialog"
          onClick={onClose}
        >
          <Icon name="close" />
        </button>
      </div>
      {children}
    </dialog>
  );
}
export function Panel({
  title,
  subtitle,
  children,
  action,
  className = "",
}: {
  title?: string;
  subtitle?: string;
  children: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <section className={`panel ${className}`}>
      {title && (
        <div className="panel-head">
          <div>
            <h2>{title}</h2>
            {subtitle && <p>{subtitle}</p>}
          </div>
          {action}
        </div>
      )}
      {children}
    </section>
  );
}
export const text = (d: FormData, name: string) => String(d.get(name) ?? "");

export function BrandMark() {
  const [failed, setFailed] = useState(false);
  const logo = import.meta.env.VITE_DEPARTMENT_LOGO;
  return logo && !failed ? (
    <img
      className="brand-logo"
      src={logo}
      alt="Department logo"
      onError={() => setFailed(true)}
    />
  ) : (
    <Icon name="book" size={25} />
  );
}
