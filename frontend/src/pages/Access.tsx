import { useEffect, useState } from "react";
import { Link } from "react-router";
import { useApp } from "../context";
import { can } from "../permissions";
import { service } from "../services";
import type { Application, Assignment, ScanSession } from "../models";
import {
  Action,
  Badge,
  Empty,
  FormFields,
  Icon,
  Modal,
  Panel,
  Time,
  text,
} from "../components/ui";
export function MyAccess() {
  const { data, act } = useApp(),
    [missing, setMissing] = useState<Assignment | null>(null);
  const p = data.people.find((p) => p.id === data.identity.personId),
    assignments = data.assignments.filter(
      (a) => a.personId === data.identity.personId,
    ),
    current = assignments.find((a) => ["ACTIVE", "PENDING"].includes(a.state)),
    card = data.cards.find((c) => c.id === current?.cardId);
  if (!p)
    return (
      <Empty>
        Your visitor details have reached their retention deadline. Access has
        ended.
      </Empty>
    );
  return (
    <>
      <Heading
        title="My Access"
        description="Your card, your requests and your library access."
      />
      <div className="access-grid">
        <Panel title="Library card" subtitle={p.reference}>
          <div className="card-preview">
            <div>
              <Icon name="book" size={28} />
              <span>IFT E-LIBRARY</span>
            </div>
            <strong>{card?.number ?? "No active card"}</strong>
            <div>
              <span>
                {p.firstName} {p.lastName}
              </span>
              <Icon name="card" size={30} />
            </div>
          </div>
          <div className="padded">
            <div className="spread">
              <strong>Assignment status</strong>
              <Badge value={current?.state ?? "UNASSIGNED"} />
            </div>
            <dl>
              <dt>Account</dt>
              <dd>
                <Badge value={p.status} />
              </dd>
              <dt>Access starts</dt>
              <dd>{p.start ?? "Not set by Admin"}</dd>
              <dt>Access expires</dt>
              <dd>{p.expiry ?? "Not set by Admin"}</dd>
              <dt>Controller sync</dt>
              <dd>
                <Badge value={current?.sync ?? p.sync} />
              </dd>
            </dl>
            {current && (
              <button
                className="danger full"
                onClick={() => setMissing(current)}
              >
                Report card missing
              </button>
            )}
            {assignments.some(
              (a) => a.state === "REVOKED" && a.sync !== "SYNCHRONIZED",
            ) && (
              <div className="callout warning">
                Revoked locally — controller disable not confirmed.
              </div>
            )}
            <small className="muted">
              Account status, card assignment and hardware confirmation are
              separate.
            </small>
          </div>
        </Panel>
        <div className="stack">
          {p.type === "VISITOR" ? (
            <VisitorSelf />
          ) : (
            <>
              <Panel title="Apply for a library card">
                <div className="padded">
                  <p>
                    Pay outside the application, then submit your evidence for
                    review. Approval does not activate a card.
                  </p>
                  <Action
                    className="primary"
                    onClick={() =>
                      act({
                        type: "application",
                        kind: assignments.length ? "REPLACEMENT" : "FIRST",
                      })
                    }
                  >
                    {assignments.length
                      ? "Request replacement"
                      : "Request first card"}{" "}
                    <Icon name="arrow" size={16} />
                  </Action>
                </div>
              </Panel>
              <Panel title="Payment instructions">
                <div className="padded">
                  <span className="eyebrow">
                    {service.mode === "mock"
                      ? "FICTIONAL EXAMPLE SETTINGS"
                      : "CONFIGURED BY THE LOCAL ADMINISTRATOR"}
                  </span>
                  <p>{data.settings.payment}</p>
                  <small>
                    Evidence: JPEG, PNG or PDF · up to{" "}
                    {data.settings.uploadLimit / 1048576} MiB
                  </small>
                </div>
              </Panel>
            </>
          )}
          <Panel title="Need to update your details?">
            <div className="padded">
              <p>
                You can update your phone and photo. Contact an administrator
                for identity or access-date changes.
              </p>
              <Link to="/profile" className="text-link">
                Go to my profile <Icon name="arrow" size={16} />
              </Link>
            </div>
          </Panel>
        </div>
      </div>
      {p.type !== "VISITOR" && (
        <>
          <h2 className="section-title">My applications</h2>
          <RequestList own />
        </>
      )}
      <Panel title="Assignment history">
        <div className="record-list">
          {assignments.map((a) => (
            <div className="record" key={a.id}>
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
          {!assignments.length && <Empty>No card assignments yet.</Empty>}
        </div>
      </Panel>
      {missing && (
        <Modal
          title="Report this card missing?"
          onClose={() => setMissing(null)}
        >
          <p>
            This irreversible report revokes card{" "}
            <strong>
              {data.cards.find((c) => c.id === missing.cardId)?.number}
            </strong>{" "}
            immediately in the demo and attempts controller disable. The revoked
            personal card cannot be reactivated.
          </p>
          <p>
            If the controller is unreachable, physical disable remains
            unconfirmed. Apply for a replacement afterward.
          </p>
          <Action
            className="danger"
            onClick={async () => {
              await act({
                type: "revoke",
                assignmentId: missing.id,
                reason: "MISSING",
                own: true,
              });
              setMissing(null);
            }}
          >
            Confirm missing-card report
          </Action>
        </Modal>
      )}
    </>
  );
}
export function Heading({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="page-heading">
      <div>
        <span className="eyebrow">IFT E-LIBRARY</span>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {action}
    </div>
  );
}
export function Profile() {
  const { data, act } = useApp();
  const p = data.people.find((p) => p.id === data.identity.personId);
  if (!p) return <Empty>Profile no longer retained.</Empty>;
  return (
    <>
      <Heading
        title="My profile"
        description="Keep your contact details up to date."
      />
      <div className="access-grid">
        <Panel title="Personal details">
          <div className="padded profile-details">
            {p.photo ? (
              <img className="profile-photo" src={p.photo} alt="Profile" />
            ) : (
              <span className="avatar large">
                {p.firstName[0]}
                {p.lastName[0]}
              </span>
            )}
            <h2>
              {p.firstName} {p.lastName}
            </h2>
            <Badge value={p.type} />
            <dl>
              <dt>Internal ID</dt>
              <dd>{p.id}</dd>
              <dt>Institutional / visitor ID</dt>
              <dd>{p.reference}</dd>
              <dt>Email</dt>
              <dd>{p.email}</dd>
              <dt>Department</dt>
              <dd>{p.department}</dd>
              <dt>Account</dt>
              <dd>
                <Badge value={p.status} />
              </dd>
            </dl>
            <small>
              Identity and access dates are controlled by administrators. Email
              ownership has not been verified.
            </small>
          </div>
        </Panel>
        <Panel title="Edit phone and photo">
          <div className="padded">
            <FormFields
              key={p.id}
              fields={[
                { name: "phone", label: "Phone", type: "tel", value: p.phone },
                {
                  name: "photo",
                  label: "Replace photo",
                  type: "file",
                  required: false,
                  accept: "image/jpeg,image/png",
                  hint: "Uploaded through your selected service; HTTP mode stores the photo on the protected local backend.",
                },
              ]}
              onSubmit={(d) => {
                const f = d.get("photo") as File;
                return act({
                  type: "profile",
                  phone: text(d, "phone"),
                  photo: f?.size ? f : undefined,
                });
              }}
            />
          </div>
        </Panel>
      </div>
    </>
  );
}
export function Requests() {
  const { data } = useApp();
  return (
    <>
      <Heading
        title="Card applications"
        description="Review payment evidence. Issue a card only after approval."
      />
      <div className="callout">
        Payment review and card activation are separate recorded actions.{" "}
        {data.identity.role === "MANAGER" &&
          "Your Manager role permits reviews; issuance is reserved for Admin and Super Admin."}
      </div>
      <RequestList />
    </>
  );
}
export function RequestList({ own = false }: { own?: boolean }) {
  const { data, act } = useApp(),
    [review, setReview] = useState<Application | null>(null),
    [issue, setIssue] = useState<Application | null>(null),
    [preview, setPreview] = useState<Application | null>(null),
    [filter, setFilter] = useState("");
  const apps = data.applications.filter(
    (a) =>
      (!own || a.personId === data.identity.personId) &&
      (!filter || a.state === filter),
  );
  return (
    <>
      <label className="inline-filter">
        Request state
        <select value={filter} onChange={(e) => setFilter(e.target.value)}>
          <option value="">All states</option>
          {[
            "DRAFT",
            "SUBMITTED",
            "REJECTED",
            "APPROVED",
            "ISSUANCE_PENDING",
            "ISSUED",
          ].map((s) => (
            <option key={s}>{s}</option>
          ))}
        </select>
      </label>
      <div className="record-grid">
        {apps.map((a) => {
          const p = data.people.find((p) => p.id === a.personId);
          return (
            <Panel key={a.id}>
              <div className="padded">
                <div className="spread">
                  <span className="eyebrow">
                    {a.id} · {a.kind}
                  </span>
                  <Badge value={a.state} />
                </div>
                <h2>{p ? p.firstName + " " + p.lastName : "Applicant"}</h2>
                <small>{p?.reference}</small>
                {a.reason && (
                  <p className="callout warning">Rejected: {a.reason}</p>
                )}
                {a.evidence && (
                  <div className="evidence-row">
                    <Icon name="card" />
                    <span>
                      {a.evidence.name}
                      <small>{(a.evidence.size / 1024).toFixed(1)} KiB</small>
                    </span>
                    <button onClick={() => setPreview(a)}>View evidence</button>
                  </div>
                )}
                {own && ["DRAFT", "REJECTED"].includes(a.state) && (
                  <FormFields
                    fields={[
                      {
                        name: "evidence",
                        label:
                          a.state === "REJECTED"
                            ? "Resubmit payment evidence"
                            : "Payment evidence",
                        type: "file",
                        accept: "image/jpeg,image/png,application/pdf",
                      },
                    ]}
                    submit="Submit for review"
                    onSubmit={(d) =>
                      act({
                        type: "submit",
                        id: a.id,
                        file: d.get("evidence") as File,
                      })
                    }
                  />
                )}
                <div className="button-row">
                  {!own &&
                    can(data.identity.role, "review") &&
                    a.state === "SUBMITTED" && (
                      <>
                        <Action
                          className="primary"
                          onClick={() =>
                            act({ type: "review", id: a.id, approve: true })
                          }
                        >
                          Approve payment
                        </Action>
                        <button className="danger" onClick={() => setReview(a)}>
                          Reject
                        </button>
                      </>
                    )}
                  {!own &&
                    can(data.identity.role, "issue") &&
                    ["APPROVED", "ISSUANCE_PENDING"].includes(a.state) && (
                      <button className="primary" onClick={() => setIssue(a)}>
                        {a.state === "ISSUANCE_PENDING"
                          ? "Resolve pending issuance"
                          : "Issue card"}
                      </button>
                    )}
                </div>
                <details>
                  <summary>Review and issuance history</summary>
                  {a.history.map((h, i) => (
                    <p key={i}>
                      <strong>{h.action}</strong> · {h.actor}
                      <br />
                      <Time value={h.at} />
                      {h.reason && (
                        <>
                          <br />
                          {h.reason}
                        </>
                      )}
                    </p>
                  ))}
                  <p>
                    Reviewer: {a.reviewer ?? "—"} ·{" "}
                    <Time value={a.reviewedAt} />
                    <br />
                    Confirmed issuer: {a.issuer ?? "—"} ·{" "}
                    <Time value={a.issuedAt} />
                  </p>
                </details>
              </div>
            </Panel>
          );
        })}
      </div>
      {!apps.length && <Empty>No applications match this view.</Empty>}
      {review && (
        <Modal title="Reject payment evidence" onClose={() => setReview(null)}>
          <FormFields
            fields={[
              {
                name: "reason",
                label: "Reason visible to applicant",
                type: "textarea",
              },
            ]}
            submit="Reject evidence"
            onSubmit={async (d) => {
              await act({
                type: "review",
                id: review.id,
                approve: false,
                reason: text(d, "reason"),
              });
              setReview(null);
            }}
          />
        </Modal>
      )}
      {issue && <Issue app={issue} onClose={() => setIssue(null)} />}{" "}
      {preview && (
        <Modal title="Payment evidence" onClose={() => setPreview(null)}>
          {preview.evidence?.url ? (
            <>
              <a
                href={preview.evidence.url}
                download={preview.evidence.name}
                className="text-link"
              >
                Download evidence
              </a>
              {preview.evidence.type === "application/pdf" ? (
                <object
                  className="receipt-preview"
                  data={preview.evidence.url}
                  type="application/pdf"
                >
                  <p>PDF preview unavailable. Use the download link.</p>
                </object>
              ) : (
                <img
                  className="receipt-preview"
                  src={preview.evidence.url}
                  alt="Submitted payment evidence"
                />
              )}
            </>
          ) : (
            <div className="callout">
              This seeded receipt is fictional metadata. No real receipt exists;
              upload a demo file to preview it.
            </div>
          )}
        </Modal>
      )}
    </>
  );
}
function Issue({ app, onClose }: { app: Application; onClose: () => void }) {
  const { data, act, notify } = useApp(),
    [number, setNumber] = useState(
      data.cards.find((c) => c.id === app.cardId)?.number ?? "",
    ),
    [scan, setScan] = useState<ScanSession | null>(null),
    [remaining, setRemaining] = useState(30),
    [error, setError] = useState("");
  useEffect(() => {
    if (!scan || !["WAITING", "DETECTED"].includes(scan.state)) return;
    const t = setInterval(() => {
      setRemaining(
        Math.max(
          0,
          Math.ceil((Date.parse(scan.expiresAt) - Date.now()) / 1000),
        ),
      );
      service
        .pollScan(scan.id)
        .then(setScan)
        .catch((e) => setError(e.message));
    }, 1000);
    return () => clearInterval(t);
  }, [scan?.id, scan?.state]);
  useEffect(
    () => () => {
      if (scan && ["WAITING", "DETECTED"].includes(scan.state))
        void service.cancelScan(scan.id).catch(() => {});
    },
    [scan?.id],
  );
  return (
    <Modal title="Enrol and issue card" onClose={onClose}>
      <p>
        Applicant: {data.people.find((p) => p.id === app.personId)?.firstName}.
        Access dates must be set by Admin. Replacements require confirmed
        removal of the previous credential.
      </p>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setError("");
          try {
            const result = await act({ type: "issue", id: app.id, number });
            if (result.result === "CONFIRMED") onClose();
          } catch (e) {
            setError((e as Error).message);
          }
        }}
      >
        <label>
          Card number
          <input
            value={number}
            onChange={(e) => setNumber(e.target.value)}
            inputMode="numeric"
            required
            placeholder="e.g. 00004201"
          />
        </label>
        <div className="button-row">
          <Action
            onClick={async () => {
              try {
                setScan(await service.startScan());
              } catch (e) {
                notify((e as Error).message, true);
              }
            }}
          >
            Simulate scan card
          </Action>
          <button className="primary" type="submit" disabled={!number.trim()}>
            Confirm enrolment & activate
          </button>
        </div>
      </form>
      {scan && (
        <div className="callout">
          <strong>Enrolment session {scan.id}</strong>
          <p>
            {scan.state} · {remaining}s remaining
          </p>
          {scan.state === "DETECTED" && (
            <>
              <p>
                Detected: <strong>{scan.number}</strong>
              </p>
              <Action
                onClick={async () => {
                  try {
                    setNumber(await service.confirmScan(scan.id));
                    setScan({ ...scan, state: "CONFIRMED" });
                  } catch (e) {
                    setError((e as Error).message);
                  }
                }}
              >
                Use confirmed card number
              </Action>
            </>
          )}
          {["WAITING", "DETECTED"].includes(scan.state) && (
            <Action
              onClick={async () => {
                await service.cancelScan(scan.id);
                setScan({ ...scan, state: "CANCELLED" });
              }}
            >
              Cancel scan
            </Action>
          )}
        </div>
      )}
      {error && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
      <small>
        Normal access swipes never enrol a card. All scanning and activation are
        simulated.
      </small>
    </Modal>
  );
}
function VisitorSelf() {
  const { data, act } = useApp();
  const ended = !!data.people.find((p) => p.id === data.identity.personId)
    ?.accessEndedAt;
  return (
    <Panel title="My visitor request">
      <div className="padded">
        <p>
          Visitor cards are fee-free. An administrator records external approval
          before assignment.
        </p>
        {data.visits.map((v) => (
          <div key={v.id} className="record">
            <strong>
              {v.id} · {v.purpose}
            </strong>
            <Badge value={v.state} />
            <small>
              {v.start} → {v.expiry}
            </small>
          </div>
        ))}
        {ended && (
          <p className="callout">
            This temporary account's access has ended. Restoring personal
            details does not renew access or extend the original retention
            deadline.
          </p>
        )}
        {!ended && !data.visits.some((v) => v.state !== "ENDED") && (
          <FormFields
            fields={[
              { name: "purpose", label: "Visit purpose" },
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
            submit="Submit visit request"
            onSubmit={(d) =>
              act({
                type: "visit",
                purpose: text(d, "purpose"),
                host: text(d, "host"),
                start: text(d, "start") + ":00+01:00",
                expiry: text(d, "expiry") + ":00+01:00",
              })
            }
          />
        )}
      </div>
    </Panel>
  );
}
