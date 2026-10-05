import { useEffect, useState } from "react";
import { useApp } from "../context";
import { service } from "../services";
import { can } from "../permissions";
import type { Filters, AccessEvent, Audit, Page, Assignment } from "../models";
import { Heading } from "./Access";
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
export function CardsActivity() {
  const { data, act } = useApp(),
    [tab, setTab] = useState(
      can(data.identity.role, "issue") ? "cards" : "activity",
    ),
    [revoke, setRevoke] = useState<Assignment | null>(null);
  return (
    <>
      <Heading
        title="Cards & access activity"
        description="Current credentials, assignment history and recorded reader events."
      />
      <div className="tabs">
        <button
          className={tab === "cards" ? "active" : ""}
          onClick={() => setTab("cards")}
        >
          Cards
        </button>
        <button
          className={tab === "activity" ? "active" : ""}
          onClick={() => setTab("activity")}
        >
          Access activity
        </button>
      </div>
      {tab === "activity" ? (
        <Records family="events" />
      ) : (
        <div className="record-grid">
          {data.cards.map((c) => (
            <Panel key={c.id}>
              <div className="padded">
                <div className="spread">
                  <Icon name="card" size={27} />
                  <Badge value={c.revoked ? "REVOKED" : c.kind} />
                </div>
                <h2>{c.number}</h2>
                {c.privileged && (
                  <p className="callout">
                    Designated Super Admin card · unrestricted 24/7, including
                    holidays.
                  </p>
                )}
                {c.poolState && <Badge value={c.poolState} />}
                <p>
                  Permission:{" "}
                  {c.disabled
                    ? "Confirmed disabled (simulated)"
                    : "Enabled or removal unconfirmed"}
                </p>
                {data.assignments
                  .filter((a) => a.cardId === c.id)
                  .map((a) => {
                    const p = data.people.find((p) => p.id === a.personId);
                    return (
                      <div className="record" key={a.id}>
                        <strong>
                          {p
                            ? p.firstName + " " + p.lastName
                            : "Anonymous historical person"}
                        </strong>
                        <small>
                          {a.id} · {a.start} → {a.expiry}
                        </small>
                        <Badge value={a.state} />
                        <Badge value={a.sync} />
                        <details>
                          <summary>Per-controller confirmation</summary>
                          {a.confirmations.map((x) => (
                            <p key={x.controllerId}>
                              {x.controllerId} · intended {x.intendedState}
                              {x.lastConfirmedState && (
                                <>
                                  <br />
                                  Last simulated confirmation:{" "}
                                  {x.lastConfirmedState}
                                </>
                              )}
                              <br />
                              <Badge value={x.state} /> ·{" "}
                              {x.confirmed ? "Confirmed" : "Not confirmed"}
                            </p>
                          ))}
                        </details>
                        {can(data.identity.role, "issue") && (
                          <div className="button-row">
                            {["ACTIVE", "PENDING"].includes(a.state) && (
                              <button
                                className="danger"
                                onClick={() => setRevoke(a)}
                              >
                                Revoke assignment
                              </button>
                            )}
                            {a.sync !== "SYNCHRONIZED" && (
                              <Action
                                onClick={() =>
                                  act({
                                    type: "retry-sync",
                                    assignmentId: a.id,
                                  })
                                }
                              >
                                Retry simulated sync
                              </Action>
                            )}
                          </div>
                        )}
                      </div>
                    );
                  })}
              </div>
            </Panel>
          ))}
        </div>
      )}
      {revoke && (
        <Modal
          title="Administratively revoke card"
          onClose={() => setRevoke(null)}
        >
          <p>
            Personal-card revocation is permanent. Failed disable creates a
            critical warning and blocks replacement activation.
          </p>
          <FormFields
            fields={[
              {
                name: "reason",
                label: "Revocation reason",
                options: ["MISSING", "DAMAGED", "NOT_PERMITTED"].map(
                  (value) => ({ value, label: value.replaceAll("_", " ") }),
                ),
              },
            ]}
            submit="Confirm revocation"
            onSubmit={async (d) => {
              await act({
                type: "revoke",
                assignmentId: revoke.id,
                reason: text(d, "reason") as
                  "MISSING" | "DAMAGED" | "NOT_PERMITTED",
              });
              setRevoke(null);
            }}
          />
        </Modal>
      )}
    </>
  );
}
export function Records({ family }: { family: "events" | "audits" }) {
  const { data, notify } = useApp(),
    [filters, setFilters] = useState<Filters>({}),
    [page, setPage] = useState(1),
    [records, setRecords] = useState<Page<AccessEvent | Audit> | null>(null),
    [error, setError] = useState(""),
    [retry, setRetry] = useState(0),
    [loading, setLoading] = useState(true);
  useEffect(() => {
    let live = true;
    setLoading(true);
    setError("");
    service
      .records(family, { ...filters, page, pageSize: 15 })
      .then((p) => {
        if (live) setRecords(p);
      })
      .catch((e) => {
        if (live) setError(e.message);
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
  }, [family, filters, page, retry, data.clock]);
  const fields =
    family === "events"
      ? [
          ["person", "Person"],
          ["reference", "Registration / staff ID"],
          ["card", "Card identifier"],
          ["controller", "Controller"],
          ["door", "Door"],
          ["type", "Event type"],
        ]
      : [
          ["actor", "Actor"],
          ["action", "Action"],
          ["target", "Target"],
        ];
  const exportFile = async (format: "csv" | "pdf") => {
    try {
      const blob = await service.exportRecords(family, filters, format),
        url = URL.createObjectURL(blob),
        a = document.createElement("a");
      a.href = url;
      a.download = `ift-${family}.${format}`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      notify(`Exported filtered ${family}. Export recorded in audit history.`);
    } catch (e) {
      notify((e as Error).message, true);
    }
  };
  const update = (name: string, value: string) => {
    setFilters((f) => ({ ...f, [name]: value }));
    setPage(1);
  };
  return (
    <Panel
      title={family === "events" ? "Access activity" : "Audit history"}
      subtitle="Africa/Lagos · synthetic demonstration records"
      action={
        can(data.identity.role, "export") && (
          <div className="button-row">
            <Action onClick={() => exportFile("csv")}>
              <Icon name="download" size={16} /> CSV
            </Action>
            <Action onClick={() => exportFile("pdf")}>PDF</Action>
          </div>
        )
      }
    >
      <details className="filters" open>
        <summary>Filter records</summary>
        <div className="filter-grid">
          <label>
            From
            <input
              type="date"
              value={filters.from ?? ""}
              onChange={(e) => update("from", e.target.value)}
            />
          </label>
          <label>
            Through
            <input
              type="date"
              value={filters.to ?? ""}
              onChange={(e) => update("to", e.target.value)}
            />
          </label>
          {fields.map(([name, label]) => (
            <label key={name}>
              {label}
              <input
                value={String(filters[name as keyof Filters] ?? "")}
                onChange={(e) => update(name, e.target.value)}
                placeholder={
                  name === "controller"
                    ? "controller-1"
                    : name === "door"
                      ? "door-1"
                      : undefined
                }
              />
            </label>
          ))}
          {family === "events" && (
            <label>
              Entry / Exit
              <select
                value={filters.direction ?? ""}
                onChange={(e) => update("direction", e.target.value)}
              >
                <option value="">Both readers</option>
                <option>ENTRY</option>
                <option>EXIT</option>
              </select>
            </label>
          )}
          <label>
            Result
            {family === "events" ? (
              <select
                value={filters.result ?? ""}
                onChange={(e) => update("result", e.target.value)}
              >
                <option value="">All results</option>
                <option>GRANTED</option>
                <option>DENIED</option>
              </select>
            ) : (
              <input
                value={filters.result ?? ""}
                onChange={(e) => update("result", e.target.value)}
              />
            )}
          </label>
        </div>
        <button
          onClick={() => {
            setFilters({});
            setPage(1);
          }}
        >
          Clear filters
        </button>
      </details>
      {error ? (
        <div className="padded">
          <p role="alert">{error}</p>
          <button onClick={() => setRetry((r) => r + 1)}>Retry</button>
        </div>
      ) : loading ? (
        <div className="empty" role="status">
          Loading records…
        </div>
      ) : records?.items.length ? (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                {(family === "events"
                  ? [
                      "Time · Lagos",
                      "Person / ID",
                      "Card / assignment",
                      "Controller / door",
                      "Reader / event",
                      "Result",
                    ]
                  : ["Time · Lagos", "Actor", "Action", "Target", "Result"]
                ).map((c) => (
                  <th key={c}>{c}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {records.items.map((row) => (
                <tr key={row.id}>
                  <td>
                    <Time value={row.at} />
                  </td>
                  {"card" in row ? (
                    <>
                      <td>
                        <strong>{row.person}</strong>
                        <small>{row.reference}</small>
                      </td>
                      <td>
                        {row.card}
                        <small>{row.assignmentId ?? "No owner"}</small>
                      </td>
                      <td>
                        {row.controller}
                        <small>{row.door}</small>
                      </td>
                      <td>
                        {row.direction}
                        <small>{row.type}</small>
                      </td>
                      <td>
                        <Badge value={row.result} />
                      </td>
                    </>
                  ) : (
                    <>
                      <td>{row.actor}</td>
                      <td>{row.action}</td>
                      <td>{row.target}</td>
                      <td>
                        <Badge value={row.result} />
                      </td>
                    </>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <Empty>No records match your filters.</Empty>
      )}
      <div className="pagination">
        <span>
          {records?.total ?? 0} records · page {page}
        </span>
        <div className="button-row">
          <button disabled={page === 1} onClick={() => setPage((p) => p - 1)}>
            Previous
          </button>
          <button
            disabled={page * 15 >= (records?.total ?? 0)}
            onClick={() => setPage((p) => p + 1)}
          >
            Next
          </button>
        </div>
      </div>
    </Panel>
  );
}
