import { useEffect, useState } from "react";
import { useApp } from "../context";
import { service } from "../services";
import type {
  ControllerAccess as Access,
  ControllerCardRead,
  ManagementPreview,
} from "../services/accessControlService";
import {
  Action,
  Badge,
  FormFields,
  Modal,
  Panel,
  Time,
  text,
} from "../components/ui";

export function PreviewChange({
  preview,
  onResult,
  onClose,
}: {
  preview: ManagementPreview;
  onResult: (p: ManagementPreview) => Promise<void>;
  onClose: () => void;
}) {
  const [error, setError] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  return (
    <Modal title="Review exact controller change" onClose={onClose}>
      <p>
        Operation <strong>{preview.id}</strong> ·{" "}
        <Badge value={preview.status} />
      </p>
      <p role="status">{preview.message}</p>
      {error && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
      <p>
        {preview.kind} {preview.slot}. Preview expires{" "}
        <Time value={preview.expiresAt} />.
      </p>
      <details>
        <summary>Current controller state</summary>
        <pre className="controller-json">
          {JSON.stringify(preview.before, null, 2)}
        </pre>
      </details>
      <details open>
        <summary>Exact proposed state</summary>
        <pre className="controller-json">
          {JSON.stringify(preview.proposed, null, 2)}
        </pre>
      </details>
      {preview.actual !== undefined && (
        <details open>
          <summary>Actual controller readback</summary>
          <pre className="controller-json">
            {JSON.stringify(preview.actual, null, 2)}
          </pre>
        </details>
      )}
      {preview.readbackOnly && (
        <p>Verified by GET only; no card command was resent.</p>
      )}
      <p>
        Affected cards:{" "}
        {preview.affected
          .map((c) => `${c.card} (door ${c.doors.join(", ") || "none"})`)
          .join("; ") || "No currently authorized card references this slot."}
      </p>
      {preview.limitations.map((l) => (
        <p className="muted" key={l}>
          {l}
        </p>
      ))}
      {preview.status === "PENDING" && (
        <>
          <label>
            <input
              type="checkbox"
              checked={confirmed}
              onChange={(e) => setConfirmed(e.target.checked)}
            />{" "}
            I authorize this exact change and the listed card impact.
          </label>
          <Action
            className="primary"
            disabled={!confirmed}
            onClick={async () => {
              try {
                setError("");
                await onResult(
                  await service.applyControllerPreview(
                    preview.id,
                    preview.affected.map((c) => c.card),
                  ),
                );
              } catch (e) {
                setError((e as Error).message);
              }
            }}
          >
            Apply reviewed change once
          </Action>
        </>
      )}
      {preview.status === "REQUIRES_RECONCILIATION" &&
        preview.kind === "CARD" && (
          <Action
            onClick={async () => {
              try {
                setError("");
                await onResult(
                  await service.reconcileControllerPreview(
                    preview.id,
                    preview.affected.map((c) => c.card),
                  ),
                );
              } catch (e) {
                setError((e as Error).message);
              }
            }}
          >
            Verify actual state without resending
          </Action>
        )}
      {preview.validityCompatibilityUsed && (
        <p className="callout">
          Actual validity limit flags remain zero. Readback uses this device’s
          pinned observation profile; backend-stopped expiry acceptance is still
          pending.
        </p>
      )}
      {preview.status !== "PENDING" && (
        <p>
          Refresh from controller before preparing another change. An uncertain
          attempt is never automatically replayed.
        </p>
      )}
    </Modal>
  );
}

export function ControllerAccess() {
  const { data, refresh, act } = useApp();
  const [access, setAccess] = useState<Access | null>(null);
  const [error, setError] = useState("");
  const [record, setRecord] = useState<ControllerCardRead | null>(null);
  const [adopted, setAdopted] = useState("");
  const [operation, setOperation] = useState<
    Access["operations"][number] | null
  >(null);
  const [preview, setPreview] = useState<ManagementPreview | null>(null);
  const load = async () => {
    try {
      setAccess(await service.controllerAccess());
    } catch (e) {
      setError((e as Error).message);
    }
  };
  useEffect(() => {
    void load();
  }, []);
  const run = async (work: () => Promise<unknown>) => {
    try {
      setError("");
      await work();
    } catch (e) {
      setError((e as Error).message);
      throw e;
    }
  };
  return (
    <div className="access-grid">
      <Panel title="Controller card access">
        <div className="padded">
          <p>
            Save approved issuance, validity, replacement, revocation or visitor
            changes normally. Review and apply one pending operation here.
            Application roles do not grant physical access.
          </p>
          {error && (
            <p role="alert" className="form-error">
              {error}
            </p>
          )}
          <Action onClick={load}>Refresh controller operations</Action>
          {access?.legacyScopeEnabled && (
            <p className="callout">
              A legacy operator scope is open. Close it before using normal web
              management.
              {data.identity.role === "SUPER_ADMIN" && (
                <Action
                  onClick={() =>
                    run(async () => {
                      await service.closeControllerAccess();
                      await load();
                    })
                  }
                >
                  Close legacy authorization
                </Action>
              )}
            </p>
          )}
          {data.assignments
            .filter(
              (a) =>
                ["PENDING", "ACTIVE"].includes(a.state) &&
                a.sync === "OUT_OF_SYNC",
            )
            .map((a) => {
              const card = data.cards.find((c) => c.id === a.cardId);
              const person = data.people.find((p) => p.id === a.personId);
              if (!card || card.revoked) return null;
              return (
                <div className="callout" key={a.id}>
                  <p>
                    {person?.firstName} {person?.lastName} · {card.number} ·
                    updated local dates await a current operation.
                  </p>
                  <Action
                    onClick={() =>
                      run(async () => {
                        await act({ type: "retry-sync", assignmentId: a.id });
                        await load();
                      })
                    }
                  >
                    Prepare current assignment operation
                  </Action>
                </div>
              );
            })}
          {access?.operations.map((o) => (
            <div key={o.id} className="callout">
              <p>
                <strong>{o.card}</strong> · {o.desired} ·{" "}
                <Badge value={o.status} />
              </p>
              <p>{o.message}</p>
              <Action
                onClick={() => {
                  setOperation(o);
                  setError("");
                }}
              >
                Compare this operation
              </Action>
            </div>
          ))}
          {access && !access.operations.length && (
            <p>No current card operations await execution.</p>
          )}
          {access?.management?.map((p) => (
            <div key={p.id} className="callout">
              <p>
                {p.kind} {p.slot} · <Badge value={p.status} />
              </p>
              <p>{p.message}</p>
              <Action onClick={() => setPreview(p)}>
                Inspect operation {p.id.slice(0, 8)}
              </Action>
            </div>
          ))}
        </div>
      </Panel>
      <Panel title="Read and reconcile controller card">
        <div className="padded">
          <p>
            Read any complete card number, including an externally configured or
            revoked card. Reading sends no permission change.
          </p>
          <FormFields
            fields={[{ name: "number", label: "Card number" }]}
            submit="Refresh card from controller"
            onSubmit={(form) =>
              run(async () =>
                setRecord(
                  await service.readControllerCard(text(form, "number").trim()),
                ),
              )
            }
          />
          {record && (
            <div className="callout">
              <p>
                <strong>{record.card}</strong> · <Time value={record.readAt} />
              </p>
              <p>
                Local assignment:{" "}
                {record.local?.personId
                  ? data.people.find((p) => p.id === record.local?.personId)
                      ?.reference || record.local.personId
                  : "Unassigned"}
                . {record.local?.revoked ? "Permanently revoked." : ""}
              </p>
              {!record.record ? (
                <p>No controller card record.</p>
              ) : (
                <>
                  <p>
                    Card type:{" "}
                    {record.record.type === 3
                      ? "Blacklisted"
                      : record.record.type === 1
                        ? "Ordinary"
                        : record.record.type}
                    .
                  </p>
                  <p>
                    Door rights:{" "}
                    {record.record.doors
                      .flatMap((v, i) => (v ? [i + 1] : []))
                      .join(", ") || "None"}
                    .
                  </p>
                  <p>
                    Validity: {record.record.start.join(" / ")} to{" "}
                    {record.record.expiry.join(" / ")} (Lagos).
                  </p>
                  <p>
                    Validity flags: enabled {record.record.validityEnabled},
                    start {record.record.beginFlag}, end {record.record.endFlag}
                    .
                  </p>
                  <details>
                    <summary>Complete permission readback</summary>
                    <pre className="controller-json">
                      {JSON.stringify(record.record, null, 2)}
                    </pre>
                  </details>
                </>
              )}
            </div>
          )}
          {record &&
            data.identity.role === "SUPER_ADMIN" &&
            !record.local?.revoked && (
              <FormFields
                fields={[
                  {
                    name: "reason",
                    label:
                      "Reason to adopt this observed reconciliation baseline",
                  },
                ]}
                submit="Adopt observed baseline only"
                onSubmit={(f) =>
                  run(async () =>
                    setAdopted(
                      (
                        await service.adoptControllerCardBaseline(
                          record.card,
                          text(f, "reason"),
                        )
                      ).message,
                    ),
                  )
                }
              />
            )}
          {adopted && <p role="status">{adopted}</p>}
          <p className="muted">
            External permissions require an explicit reconciliation reason.
            Payment and visitor approvals, permanent revocation and replacement
            sequencing still apply.
          </p>
        </div>
      </Panel>
      {operation && !preview && (
        <Modal
          title="Compare approved card operation"
          onClose={() => setOperation(null)}
        >
          <p>
            Card {operation.card} · {operation.desired}. No write occurs during
            preview.
          </p>
          {error && (
            <p role="alert" className="form-error">
              {error}
            </p>
          )}
          <FormFields
            fields={[
              {
                name: "template",
                label: "Existing template number",
                type: "number",
                value: "3",
                min: "1",
                max: "65535",
              },
              {
                name: "reconcile",
                label: "External controller permissions",
                options: [
                  { value: "NO", label: "Compare first" },
                  {
                    value: "YES",
                    label: "Reconcile with approved local rights",
                  },
                ],
              },
              {
                name: "reason",
                label: "Reconciliation reason (required for external changes)",
                required: false,
              },
            ]}
            submit="Prepare fresh comparison"
            onSubmit={(f) =>
              run(async () =>
                setPreview(
                  await service.previewControllerCard(
                    operation.id,
                    Number(text(f, "template")),
                    text(f, "reconcile") === "YES",
                    text(f, "reason"),
                  ),
                ),
              )
            }
          />
        </Modal>
      )}
      {preview && (
        <PreviewChange
          key={preview.id}
          preview={preview}
          onClose={() => {
            setPreview(null);
            setOperation(null);
          }}
          onResult={async (p) => {
            setPreview(p);
            await load();
            await refresh();
          }}
        />
      )}
    </div>
  );
}
