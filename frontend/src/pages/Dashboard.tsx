import { useEffect, useState } from "react";
import { RemoteUnlock } from "../components/RemoteUnlock";
import { Link } from "react-router";
import { useApp } from "../context";
import { can } from "../permissions";
import { service } from "../services";
import type { AccessEvent } from "../models";
import {
  Badge,
  Time,
  Panel,
  Icon,
  Modal,
  Action,
  Empty,
} from "../components/ui";
export function Dashboard() {
  const { data, act, notify } = useApp(),
    [events, setEvents] = useState<AccessEvent[]>([]),
    [unlock, setUnlock] = useState(false);
  useEffect(() => {
    let live = true;
    service
      .records("events", { pageSize: 7 })
      .then((p) => {
        if (live) setEvents(p.items as AccessEvent[]);
      })
      .catch((e) => notify(e.message, true));
    return () => {
      live = false;
    };
  }, [data, notify]);
  const active = data.assignments.filter((a) => a.state === "ACTIVE").length,
    pending = data.applications.filter((a) => a.state === "SUBMITTED").length,
    critical = data.notifications.filter(
      (n) => n.severity === "CRITICAL" && !n.read,
    ).length;
  return (
    <>
      <div className="page-heading">
        <div>
          <span className="eyebrow">LIBRARY OPERATIONS</span>
          <h1>Dashboard</h1>
          <p>A clear view of your library, from entry to exit.</p>
        </div>
        <div className="live-label">
          {data.controllerMode === "HIKVISION_CONTROLLED" ? (
            <Icon name="warning" size={15} />
          ) : (
            <span className="status-dot" />
          )}{" "}
          {data.controllerMode === "HIKVISION_CONTROLLED"
            ? data.controllerReadStatus === "CONNECTED"
              ? "Real event monitor"
              : "Event connection blocked"
            : "Simulated live monitor"}
        </div>
      </div>
      <div className="metrics">
        {[
          {
            label: "Active assignments",
            value: active,
            icon: "card",
            note: "Personal and visitor cards",
          },
          {
            label: "Payment reviews",
            value: pending,
            icon: "clock",
            note: "Awaiting a decision",
          },
          {
            label: "Connected controllers",
            value:
              data.devices.filter((d) => d.online).length +
              "/" +
              data.devices.length,
            icon: "door",
            note:
              data.controllerMode === "HIKVISION_CONTROLLED"
                ? data.controllerReadStatus === "CONNECTED"
                  ? data.controllerMode === "HIKVISION_CONTROLLED"
                    ? "Connected · selected card access"
                    : "Controller connected"
                  : "Controller connection unverified"
                : "Local network · simulated",
          },
          {
            label: "Critical alerts",
            value: critical,
            icon: "shield",
            note: critical ? "Action needed" : "No critical alerts",
          },
        ].map((m) => (
          <div className="metric" key={m.label}>
            <div className="metric-top">
              <span>{m.label}</span>
              <span className="metric-icon">
                <Icon name={m.icon} />
              </span>
            </div>
            <strong>{m.value}</strong>
            <small>{m.note}</small>
          </div>
        ))}
      </div>
      <div className="dashboard-grid">
        <Panel
          title="Recent access activity"
          subtitle="Entry and Exit readers · one library door"
          action={
            <Link className="text-link" to="/cards-activity">
              View activity <Icon name="arrow" size={16} />
            </Link>
          }
        >
          <EventTable events={events} />
          <div className="panel-footer">
            {data.controllerMode === "HIKVISION_CONTROLLED" ? (
              <Icon name="warning" size={15} />
            ) : (
              <span className="status-dot" />
            )}{" "}
            {data.controllerMode === "HIKVISION_CONTROLLED"
              ? data.controllerReadStatus === "CONNECTED"
                ? `History: ${data.controllerHistoryStatus ?? "UNKNOWN"} · Live subscription: ${data.controllerLiveStatus ?? "UNKNOWN"}. A grant does not prove physical passage. ${data.controllerReaderMappingVerified ? "Reader directions verified by ordinary swipes." : "Reader mapping awaits swipe verification."}`
                : "Controller event collection is unavailable. A swipe does not prove physical passage."
              : "Demo events update automatically. No real controller connection."}
          </div>
        </Panel>
        <div className="stack">
          <Panel title="Door & controller">
            <div className="device-body">
              {data.devices.map((d) => (
                <div key={d.id}>
                  <div className="device-illustration">
                    <Icon name="door" size={38} />
                    <span>
                      <strong>{d.door}</strong>
                      <small>Entry reader / Exit reader</small>
                    </span>
                  </div>
                  <dl>
                    <dt>Controller</dt>
                    <dd>{d.model}</dd>
                    <dt>Connection</dt>
                    <dd>
                      <Badge
                        value={
                          data.controllerMode === "HIKVISION_CONTROLLED"
                            ? d.online
                              ? "ONLINE"
                              : "UNKNOWN"
                            : d.online
                              ? "ONLINE"
                              : "FAILED"
                        }
                      />
                    </dd>
                    <dt>Door sensor</dt>
                    <dd>
                      <Badge value={d.sensor} />
                    </dd>
                    <dt>Last communication</dt>
                    <dd>
                      <Time value={d.lastCommunication} />
                    </dd>
                  </dl>
                  {can(data.identity.role, "unlock") && d.doorId === "door-1" && data.controllerMode === "HIKVISION_CONTROLLED" && service.mode === "http" && <RemoteUnlock />}
                  {can(data.identity.role, "unlock") &&
                    data.controllerWritesAvailable !== false && (
                      <button
                        className="primary full"
                        onClick={() => setUnlock(true)}
                      >
                        <Icon name="door" /> Timed unlock
                      </button>
                    )}
                  <small className="muted">
                    Command acknowledgement does not prove the door opened.
                  </small>
                </div>
              ))}
            </div>
          </Panel>
          <Panel
            title="Security alerts"
            action={
              <Link to="/notifications" className="text-link">
                View all
              </Link>
            }
          >
            <div className="alert-list">
              {data.notifications.slice(0, 3).map((n) => (
                <div className="alert-item" key={n.id}>
                  <span className="alert-icon">
                    <Icon name="warning" size={18} />
                  </span>
                  <div>
                    <strong>{n.title}</strong>
                    <small>{n.detail}</small>
                    <Time value={n.at} />
                  </div>
                </div>
              ))}
              {!data.notifications.length && <Empty>No alerts.</Empty>}
            </div>
          </Panel>
        </div>
      </div>
      <div className="quick-strip">
        <div>
          <Icon name="book" />
          <div>
            <strong>Keep the library moving</strong>
            <p>Review applications and manage access from one place.</p>
          </div>
        </div>
        <Link to="/requests">
          Open payment reviews <Icon name="arrow" size={17} />
        </Link>
      </div>
      {unlock && (
        <Modal title="Confirm timed unlock" onClose={() => setUnlock(false)}>
          <p>
            Send a simulated <strong>5-second timed unlock</strong> to{" "}
            <strong>{data.devices[0]?.door}</strong>?
          </p>
          <p>
            The door sensor is UNKNOWN. A timed-out command has an uncertain
            outcome and will not be repeated automatically.
          </p>
          <Action
            className="primary"
            onClick={async () => {
              await act({
                type: "unlock",
                doorId: data.devices[0].doorId,
                confirmed: true,
              });
              setUnlock(false);
            }}
          >
            Confirm unlock for 5 seconds
          </Action>
        </Modal>
      )}
    </>
  );
}
export function EventTable({ events }: { events: AccessEvent[] }) {
  return events.length ? (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th>Person / credential</th>
            <th>Reader</th>
            <th>Time · Lagos</th>
            <th>Result</th>
          </tr>
        </thead>
        <tbody>
          {events.map((e) => (
            <tr key={e.id}>
              <td>
                <div className="person-cell">
                  <span className={`avatar ${e.personId ? "" : "unknown"}`}>
                    {e.personId
                      ? e.person
                          .split(" ")
                          .map((n) => n[0])
                          .slice(0, 2)
                          .join("")
                      : "?"}
                  </span>
                  <div>
                    <strong>{e.person}</strong>
                    <small>
                      {e.card} · {e.type.replaceAll("_", " ").toLowerCase()}
                      {e.simulated ? " · Simulated" : " · Device event"}
                    </small>
                  </div>
                </div>
              </td>
              <td>
                <span className="direction">
                  <Icon name="arrow" size={15} />
                  {e.direction === "ENTRY"
                    ? "Entry"
                    : e.direction === "EXIT"
                      ? "Exit"
                      : "Unknown"}
                </span>
                {!e.simulated && (
                  <small>
                    Device reader {e.deviceReader ?? "unknown"} · mapping{" "}
                    {e.readerMappingVerified ? "verified" : "unverified"}
                  </small>
                )}
              </td>
              <td>
                <Time value={e.at} />
                {!!e.timestampFlags?.length && (
                  <small>{e.timestampFlags.join(", ")}</small>
                )}
              </td>
              <td>
                <Badge value={e.result} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  ) : (
    <Empty />
  );
}
