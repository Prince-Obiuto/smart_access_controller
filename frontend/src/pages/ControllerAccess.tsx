import { useEffect, useState } from "react";
import { useApp } from "../context";
import { service } from "../services";
import type {
  ControllerAccess as Access,
  ControllerCardRead,
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

export function ControllerAccess() {
  const { data, refresh, notify } = useApp();
  const [access, setAccess] = useState<Access | null>(null);
  const [error, setError] = useState("");
  const [record, setRecord] = useState<ControllerCardRead | null>(null);
  const [operation, setOperation] = useState<
    Access["operations"][number] | null
  >(null);
  const load = async () => {
    try {
      setAccess(await service.controllerAccess());
      setError("");
    } catch (e) {
      setError((e as Error).message);
    }
  };
  useEffect(() => {
    void load();
  }, []);
  const run = async (work: () => Promise<unknown>) => {
    try {
      await work();
      await load();
      await refresh();
    } catch (e) {
      setError((e as Error).message);
      throw e;
    }
  };
  const cards = data.cards.filter(
    (c) =>
      !c.revoked &&
      !c.privileged &&
      !["1051305927", "1052625607", "1050853895"].includes(c.number),
  );
  const people = data.people.filter(
    (p) =>
      p.status === "ACTIVE" &&
      !p.deletedAt &&
      p.controllerCardEligible !== false,
  );
  return (
    <div className="access-grid">
      <Panel title="Controller card access">
        <div className="padded">
          <p>
            Ordinary Door 1 access uses the existing Active hours configuration
            and each approved assignment’s validity dates.
          </p>
          {error && (
            <p role="alert" className="form-error">
              {error}
            </p>
          )}
          {access && (
            <p>
              <Badge
                value={
                  access.enabled
                    ? access.expired
                      ? "EXPIRED"
                      : "AUTHORIZED"
                    : "READ_ONLY"
                }
              />{" "}
              {access.expiresAt && (
                <>
                  Authorization ends <Time value={access.expiresAt} />.
                </>
              )}
            </p>
          )}
          <Action onClick={load}>Refresh controller operations</Action>
          {data.identity.role === "SUPER_ADMIN" && !access?.enabled && (
            <FormFields
              fields={[
                {
                  name: "cardId",
                  label: "Card",
                  options: cards.map((c) => ({ value: c.id, label: c.number })),
                },
                {
                  name: "personId",
                  label: "Assigned person or approved visitor",
                  options: people.map((p) => ({
                    value: p.id,
                    label: `${p.firstName} ${p.lastName} · ${p.reference}`,
                  })),
                },
                {
                  name: "nextPersonId",
                  label: "Next approved visitor (optional)",
                  required: false,
                  options: [
                    { value: "", label: "None" },
                    ...people
                      .filter((p) => p.type === "VISITOR")
                      .map((p) => ({
                        value: p.id,
                        label: `${p.firstName} ${p.lastName}`,
                      })),
                  ],
                },
                {
                  name: "replacementNumber",
                  label: "Fresh replacement card number (optional)",
                  required: false,
                },
                {
                  name: "confirmed",
                  label: "Authorize writes for these selected cards only",
                  options: [
                    { value: "NO", label: "Choose…" },
                    { value: "YES", label: "Yes, authorize selected cards" },
                  ],
                },
              ]}
              submit="Authorize selected cards"
              onSubmit={async (form) => {
                if (text(form, "confirmed") !== "YES")
                  throw new Error(
                    "Confirm the selected cards before authorization.",
                  );
                const card = cards.find((c) => c.id === text(form, "cardId"))!;
                const personIds = [
                  text(form, "personId"),
                  text(form, "nextPersonId"),
                ].filter(Boolean);
                const selected = [
                  { number: card.number, kind: card.kind, personIds },
                ];
                const replacement = text(form, "replacementNumber").trim();
                if (replacement) {
                  if (
                    card.kind !== "PERSONAL" ||
                    replacement === card.number ||
                    personIds.length !== 1
                  )
                    throw new Error(
                      "Replacement requires two distinct personal cards and one person.",
                    );
                  selected.push({
                    number: replacement,
                    kind: "PERSONAL",
                    personIds,
                  });
                }
                await run(() => service.authorizeControllerCards(selected));
              }}
            />
          )}
          {access?.enabled && data.identity.role === "SUPER_ADMIN" && (
            <Action onClick={() => run(() => service.closeControllerAccess())}>
              Lock controller writes
            </Action>
          )}
          <p className="muted">
            Authorization sends no card write. Save issuance, revocation or
            visitor assignment/return normally, then confirm its individual
            controller operation below. Locking writes preserves current card
            permissions.
          </p>
          {access?.operations.map((o) => (
            <div key={o.id} className="callout">
              <p>
                <strong>{o.card}</strong> · {o.desired} ·{" "}
                <Badge value={o.status} />
              </p>
              <p>{o.message}</p>
              <button
                disabled={
                  !access.enabled ||
                  (access.expired && o.desired !== "DISABLED")
                }
                onClick={() => setOperation(o)}
              >
                Review controller operation
              </button>
            </div>
          ))}
        </div>
      </Panel>
      <Panel title="Read controller card">
        <div className="padded">
          {access?.cards.map((number) => (
            <Action
              key={number}
              onClick={() =>
                run(async () =>
                  setRecord(await service.readControllerCard(number)),
                )
              }
            >
              Read {number}
            </Action>
          ))}
          {record && (
            <div className="callout">
              <p>
                <strong>{record.card}</strong> · <Time value={record.readAt} />
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
                    {Object.keys(record.record.doorRights).join(", ") || "None"}
                    .
                  </p>
                  <p>
                    Validity: {record.record.start.join(" / ")} to{" "}
                    {record.record.expiry.join(" / ")} (controller local time).
                  </p>
                </>
              )}
            </div>
          )}
          <p>
            Schedule editing and unrestricted-card setup remain unavailable
            until their controller capabilities are verified.
          </p>
        </div>
      </Panel>
      {operation && (
        <Modal
          title="Confirm controller card operation"
          onClose={() => setOperation(null)}
        >
          <p>
            Apply <strong>{operation.desired}</strong> to card{" "}
            <strong>{operation.card}</strong>? The current application approval,
            dates and replacement sequence will be checked again.
          </p>
          <Action
            className="primary"
            onClick={() =>
              run(async () => {
                const result = await service.executeControllerOperation(
                  operation.id,
                );
                notify(
                  result.readbackVerified && result.status === "CONFIRMED"
                    ? "Controller card state verified."
                    : `${result.status}: ${result.message ?? "Controller confirmation pending"}`,
                  result.status !== "CONFIRMED",
                );
                setOperation(null);
              })
            }
          >
            Apply this card operation
          </Action>
        </Modal>
      )}
    </div>
  );
}
