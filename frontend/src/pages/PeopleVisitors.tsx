import { useState } from "react";
import { useApp } from "../context";
import { service } from "../services";
import { lagosDate, lagosDateTime, administrativeDate } from "../dates";
import type { Person, Visit } from "../models";
import { Heading } from "./Access";
import {
  Action,
  Badge,
  Empty,
  FormFields,
  Modal,
  Panel,
  text,
} from "../components/ui";
export function People() {
  const { data, act } = useApp(),
    [search, setSearch] = useState(""),
    [edit, setEdit] = useState<Person | "new" | null>(null);
  const [recover, setRecover] = useState<Person | null>(null);
  const [recoveryMessage, setRecoveryMessage] = useState("");
  const people = data.people.filter((p) =>
    `${p.firstName} ${p.lastName} ${p.reference} ${p.email}`
      .toLowerCase()
      .includes(search.toLowerCase()),
  );
  return (
    <>
      <Heading
        title="People"
        description="Manage ordinary person records, validity dates and card assignment history."
        action={
          <button className="primary" onClick={() => setEdit("new")}>
            + Add person
          </button>
        }
      />
      <label className="search-field">
        Find a person
        <input
          type="search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Name, ID or email"
        />
      </label>
      <div className="record-grid">
        {people.map((p) => (
          <Panel key={p.id}>
            <div className="padded">
              <div className="spread">
                <span className="avatar">
                  {p.firstName[0]}
                  {p.lastName[0]}
                </span>
                <Badge value={p.status} />
              </div>
              <h2>
                {p.firstName} {p.lastName}
              </h2>
              <p>
                {p.reference} · {p.type}
              </p>
              <small>
                {p.email}
                <br />
                {p.department}
              </small>
              <dl>
                <dt>Starts</dt>
                <dd>{lagosDateTime(p.start)}</dd>
                <dt>Expires</dt>
                <dd>{lagosDateTime(p.expiry)}</dd>
                <dt>Date sync</dt>
                <dd>
                  <Badge value={p.sync} />
                </dd>
              </dl>
              <div className="button-row">
                <button onClick={() => setEdit(p)}>Edit record</button>
                {service.mode === "http" && p.accountRecoveryAllowed && (
                  <button
                    onClick={() => {
                      setRecoveryMessage("");
                      setRecover(p);
                    }}
                  >
                    Account recovery
                  </button>
                )}
                {p.sync !== "SYNCHRONIZED" && (
                  <Action
                    disabled={!data.controllerWritesAvailable}
                    onClick={() => act({ type: "retry-sync", personId: p.id })}
                  >
                    Request date synchronization
                  </Action>
                )}
              </div>
              <details>
                <summary>Assignment history</summary>
                {data.assignments
                  .filter((a) => a.personId === p.id)
                  .map((a) => (
                    <div key={a.id} className="record">
                      <strong>
                        {data.cards.find((c) => c.id === a.cardId)?.number}
                      </strong>
                      <Badge value={a.state} />
                      <Badge value={a.sync} />
                      <small>
                        {a.start} → {a.expiry}
                      </small>
                    </div>
                  ))}
              </details>
            </div>
          </Panel>
        ))}
      </div>
      {!people.length && <Empty />}
      {recoveryMessage && <p role="status">{recoveryMessage}</p>}
      {recover && (
        <Modal
          title="Identity-verified account recovery"
          onClose={() => setRecover(null)}
        >
          <p>
            Confirm {recover.firstName} {recover.lastName}’s identity in person
            before sending a single-use recovery link to their registered email.
            Privileged accounts require Super Admin. No access permissions
            change.
          </p>
          <FormFields
            fields={[
              {
                name: "confirmed",
                label: "Identity verification",
                options: [
                  { value: "NO", label: "Not yet verified" },
                  { value: "YES", label: "Identity verified in person" },
                ],
              },
            ]}
            submit="Send recovery email"
            onSubmit={async (d) => {
              if (text(d, "confirmed") !== "YES")
                throw new Error("Confirm identity before initiating recovery.");
              setRecoveryMessage(
                (await service.adminRecovery(recover.id, true)).message,
              );
              setRecover(null);
            }}
          />
        </Modal>
      )}
      {edit && (
        <Modal
          title={edit === "new" ? "Add ordinary person" : "Edit person"}
          onClose={() => setEdit(null)}
        >
          <p>
            Identity fields belong to Admin. Date changes remain pending until
            controller confirmation. New records do not create
            administrative accounts.
          </p>
          <FormFields
            fields={[
              {
                name: "type",
                label: "Person type",
                value: edit === "new" ? "STUDENT" : edit.type,
                options: [
                  { value: "STUDENT", label: "Student" },
                  { value: "STAFF", label: "Staff" },
                ],
              },
              ...[
                ["reference", "Registration number / staff ID"],
                ["firstName", "First name"],
                ["lastName", "Last name"],
                ["email", "University email"],
                ["phone", "Phone"],
                ["department", "Department"],
              ].map(([name, label]) => ({
                name,
                label,
                type: name === "email" ? "email" : "text",
                value:
                  edit === "new"
                    ? ""
                    : String(edit[name as keyof Person] ?? ""),
              })),
              {
                name: "status",
                label: "Account status",
                value: edit === "new" ? "ACTIVE" : edit.status,
                options: [
                  { value: "ACTIVE", label: "Active" },
                  { value: "DISABLED", label: "Disabled" },
                ],
              },
              {
                name: "start",
                label: "Access start date · Lagos",
                type: "date",
                required: false,
                value: edit === "new" ? "" : lagosDate(edit.start),
              },
              {
                name: "expiry",
                label: "Access expiry date · Lagos",
                type: "date",
                required: false,
                value: edit === "new" ? "" : lagosDate(edit.expiry),
              },
            ]}
            onSubmit={async (d) => {
              await act({
                type: "person",
                id: edit === "new" ? undefined : edit.id,
                data: {
                  ...(edit === "new" ? {} : edit),
                  type: text(d, "type") as Person["type"],
                  reference: text(d, "reference"),
                  firstName: text(d, "firstName"),
                  lastName: text(d, "lastName"),
                  email: text(d, "email"),
                  phone: text(d, "phone"),
                  department: text(d, "department"),
                  status: text(d, "status") as Person["status"],
                  start: administrativeDate(
                    text(d, "start"),
                    edit === "new" ? undefined : edit.start,
                  ),
                  expiry: administrativeDate(
                    text(d, "expiry"),
                    edit === "new" ? undefined : edit.expiry,
                  ),
                },
              });
              setEdit(null);
            }}
          />
        </Modal>
      )}
    </>
  );
}
export function Visitors() {
  const { data, act } = useApp(),
    [approval, setApproval] = useState<Visit | null>(null),
    [assign, setAssign] = useState<Visit | null>(null),
    [checkout, setCheckout] = useState<Visit | null>(null),
    [requestVisit, setRequestVisit] = useState(false);
  return (
    <>
      <Heading
        title="Visitors"
        description="Individual visitor accounts. Reusable cards, safely returned."
        action={
          service.mode === "http" && data.adminVisitorRequestAvailable ? (
            <button className="primary" onClick={() => setRequestVisit(true)}>
              Request on behalf of visitor
            </button>
          ) : undefined
        }
      />
      <div className="callout">
        An Exit swipe records departure only. Physical card return and confirmed
        disable are required before a card becomes available.
      </div>
      <h2 className="section-title">Visit requests & current visits</h2>
      <div className="record-grid">
        {data.visits.map((v) => {
          const p = data.people.find((p) => p.id === v.personId);
          return (
            <Panel key={v.id}>
              <div className="padded">
                <div className="spread">
                  <span className="eyebrow">{v.id}</span>
                  <Badge value={v.state} />
                </div>
                <h2>
                  {p ? p.firstName + " " + p.lastName : "Anonymous visitor"}
                </h2>
                <small>
                  {p?.reference ?? "Personal details no longer retained"}
                </small>
                <p>
                  {v.purpose} · Host: {v.host}
                </p>
                <p>
                  {lagosDateTime(v.start)}
                  <br />
                  {lagosDateTime(v.expiry)}
                </p>
                {v.approval && (
                  <p className="callout">
                    External approval recorded by {v.approval.actor}:{" "}
                    {v.approval.reason}
                  </p>
                )}
                <div className="button-row">
                  {v.state === "REQUESTED" && (
                    <button className="primary" onClick={() => setApproval(v)}>
                      Confirm external approval
                    </button>
                  )}
                  {v.state === "APPROVED" && (
                    <button className="primary" onClick={() => setAssign(v)}>
                      Assign pool card
                    </button>
                  )}
                  {v.assignmentId &&
                    ["PENDING", "ACTIVE", "ENDED"].includes(v.state) &&
                    !v.returned && (
                      <button onClick={() => setCheckout(v)}>
                        Record return / checkout
                      </button>
                    )}
                  {v.assignmentId &&
                    data.assignments.find((a) => a.id === v.assignmentId)
                      ?.sync !== "SYNCHRONIZED" && (
                      <Action
                        onClick={() =>
                          act({
                            type: "retry-sync",
                            assignmentId: v.assignmentId,
                          })
                        }
                      >
                        Request synchronization
                      </Action>
                    )}
                </div>
                <details>
                  <summary>Visit assignment history</summary>
                  {data.assignments
                    .filter((a) => a.id === v.assignmentId)
                    .map((a) => (
                      <p key={a.id}>
                        {a.id} ·{" "}
                        {data.cards.find((c) => c.id === a.cardId)?.number}
                        <br />
                        <Badge value={a.state} /> <Badge value={a.sync} />
                        <br />
                        Physical return: {a.returnedAt ?? "Not recorded"}
                      </p>
                    ))}
                </details>
              </div>
            </Panel>
          );
        })}
      </div>
      {!data.visits.length && <Empty />}
      {requestVisit && (
        <Modal
          title="Request on behalf of visitor"
          onClose={() => setRequestVisit(false)}
        >
          <p>
            This creates a request only. Record genuine external approval
            separately before assigning a card.
          </p>
          <FormFields
            fields={[
              {
                name: "personId",
                label: "Visitor",
                options: data.people
                  .filter(
                    (p) =>
                      p.type === "VISITOR" &&
                      p.status === "ACTIVE" &&
                      !p.accessEndedAt,
                  )
                  .map((p) => ({
                    value: p.id,
                    label: p.firstName + " " + p.lastName,
                  })),
              },
              { name: "purpose", label: "Purpose" },
              { name: "host", label: "Host / department" },
              {
                name: "start",
                label: "Start · Africa/Lagos",
                type: "datetime-local",
              },
              {
                name: "expiry",
                label: "Expiry · Africa/Lagos",
                type: "datetime-local",
              },
            ]}
            submit="Create request"
            onSubmit={async (d) => {
              await act({
                type: "visitor-request",
                personId: text(d, "personId"),
                purpose: text(d, "purpose"),
                host: text(d, "host"),
                start: text(d, "start") + ":00+01:00",
                expiry: text(d, "expiry") + ":00+01:00",
              });
              setRequestVisit(false);
            }}
          />
        </Modal>
      )}
      <h2 className="section-title">Permanent visitor card pool</h2>
      <div className="record-grid">
        {data.cards
          .filter((c) => c.kind === "VISITOR")
          .map((c) => (
            <Panel key={c.id}>
              <div className="padded">
                <h2>Card {c.number}</h2>
                <Badge value={c.poolState!} />
                <p>
                  Controller permission:{" "}
                  {data.controllerMode === "SIMULATED" || service.mode === "mock"
                    ? c.disabled ? "Disabled (simulated)" : "Enabled (simulated)"
                    : c.controllerPermissionState === "DISABLED" ? "Disabled — device verified"
                    : c.controllerPermissionState === "ENABLED" ? "Enabled — device verified"
                    : "Controller confirmation pending"}
                </p>
                <small>{c.id} · independent temporary assignments</small>
              </div>
            </Panel>
          ))}
      </div>
      {approval && (
        <Modal
          title="Record external approval"
          onClose={() => setApproval(null)}
        >
          <p>
            Approval happens through other channels. Record its reference before
            card issuance.
          </p>
          <FormFields
            fields={[
              {
                name: "note",
                label: "External approval reference / confirmation",
                type: "textarea",
              },
            ]}
            submit="Confirm approval"
            onSubmit={async (d) => {
              await act({
                type: "visitor-approve",
                id: approval.id,
                note: text(d, "note"),
              });
              setApproval(null);
            }}
          />
        </Modal>
      )}
      {assign && (
        <Modal
          title="Assign a disabled pool card"
          onClose={() => setAssign(null)}
        >
          <FormFields
            fields={[
              {
                name: "cardId",
                label: "Available disabled card",
                options: data.cards
                  .filter(
                    (c) => c.poolState === "AVAILABLE_DISABLED" && c.disabled,
                  )
                  .map((c) => ({ value: c.id, label: c.number })),
              },
            ]}
            submit="Assign visitor card"
            onSubmit={async (d) => {
              await act({
                type: "visitor-assign",
                id: assign.id,
                cardId: text(d, "cardId"),
              });
              setAssign(null);
            }}
          />
        </Modal>
      )}
      {checkout && (
        <Modal
          title="Visitor return and checkout"
          onClose={() => setCheckout(null)}
        >
          <p>
            Cards remain unavailable after expiry or failed disable. An exit
            event is not a return.
          </p>
          <FormFields
            fields={[
              {
                name: "returned",
                label: "Physical card return",
                options: [
                  { value: "no", label: "Not returned" },
                  { value: "yes", label: "Returned and physically received" },
                ],
              },
              {
                name: "condition",
                label: "Card condition",
                options: [
                  {
                    value: "GOOD",
                    label: "Good — return to pool after confirmed disable",
                  },
                  { value: "MISSING", label: "Missing — unavailable" },
                  { value: "DAMAGED", label: "Damaged — unavailable" },
                ],
              },
            ]}
            submit="End access and disable"
            onSubmit={async (d) => {
              await act({
                type: "visitor-return",
                id: checkout.id,
                returned: text(d, "returned") === "yes",
                condition: text(d, "condition") as
                  "GOOD" | "MISSING" | "DAMAGED",
              });
              setCheckout(null);
            }}
          />
        </Modal>
      )}
    </>
  );
}
