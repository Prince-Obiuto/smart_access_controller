import { useEffect, useState } from "react";
import { useApp } from "../context";
import { service } from "../services";
import { can, roles } from "../permissions";
import { Heading } from "./Access";
import { Records } from "./Records";
import {
  Action,
  Badge,
  Empty,
  FormFields,
  Panel,
  Time,
  text,
} from "../components/ui";
import type { AccessEvent, Audit, Person, Schedule } from "../models";
export function Administration() {
  const { data } = useApp();
  const tabs = [
    ...(can(data.identity.role, "schedules") ? ["Schedules"] : []),
    "Audit logs",
    ...(can(data.identity.role, "settings") ? ["Settings", "Recovery"] : []),
  ];
  const [tab, setTab] = useState(tabs[0]);
  return (
    <>
      <Heading
        title="Administration"
        description="Schedules, accountable operations and permitted system settings."
      />
      <div className="tabs">
        {tabs.map((t) => (
          <button
            key={t}
            className={tab === t ? "active" : ""}
            onClick={() => setTab(t)}
          >
            {t}
          </button>
        ))}
      </div>
      {tab === "Schedules" ? (
        <Schedules />
      ) : tab === "Audit logs" ? (
        <Records family="audits" />
      ) : tab === "Settings" ? (
        <Settings />
      ) : (
        <Recovery />
      )}
    </>
  );
}
function Schedules() {
  const { data, act } = useApp(),
    [days, setDays] = useState(data.schedule.weekdays),
    [month, setMonth] = useState(data.clock.slice(0, 7)),
    [selected, setSelected] = useState("");
  const n = new Date(
    Number(month.slice(0, 4)),
    Number(month.slice(5)),
    0,
  ).getDate();
  return (
    <div className="access-grid">
      <Panel
        title="Normal access schedule"
        action={<Badge value={data.schedule.sync} />}
      >
        <div className="padded">
          <p>
            Normal personal and visitor cards follow this schedule within their
            individual validity dates. Operational timezone: Africa/Lagos.
          </p>
          <fieldset>
            <legend>Working days</legend>
            <div className="day-buttons">
              {["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].map(
                (name, i) => (
                  <label key={name}>
                    <input
                      type="checkbox"
                      checked={days.includes(i)}
                      onChange={(e) =>
                        setDays(
                          e.target.checked
                            ? [...days, i]
                            : days.filter((d) => d !== i),
                        )
                      }
                    />
                    {name}
                  </label>
                ),
              )}
            </div>
          </fieldset>
          <FormFields
            fields={[
              {
                name: "opens",
                label: "Opening time",
                type: "time",
                value: data.schedule.opens,
              },
              {
                name: "closes",
                label: "Closing time",
                type: "time",
                value: data.schedule.closes,
              },
            ]}
            onSubmit={(d) =>
              act({
                type: "schedule",
                schedule: {
                  ...data.schedule,
                  weekdays: days,
                  opens: text(d, "opens"),
                  closes: text(d, "closes"),
                },
              })
            }
          />
          <div className="button-row">
            <Action onClick={() => act({ type: "retry-sync", schedule: true })}>
              Apply simulated schedule
            </Action>
          </div>
          <div className="callout">
            Exactly one designated Super Admin physical card has a separate
            unrestricted 24/7 schedule. A website role change never creates a
            privileged card.
          </div>
        </div>
      </Panel>
      <Panel title="Calendar exceptions">
        <div className="padded">
          <label>
            Calendar month
            <input
              type="month"
              value={month}
              onChange={(e) => setMonth(e.target.value)}
            />
          </label>
          <div className="calendar">
            {Array.from({ length: n }, (_, i) => {
              const date = month + "-" + String(i + 1).padStart(2, "0"),
                ex = data.schedule.exceptions.find((e) => e.date === date);
              return (
                <button
                  key={date}
                  className={`${date === selected ? "selected" : ""} ${ex ? "exception" : ""}`}
                  title={ex?.label ?? date}
                  aria-label={`Set exception for ${date}${ex ? " " + ex.label : ""}`}
                  onClick={() => setSelected(date)}
                >
                  {i + 1}
                  {ex && <span>•</span>}
                </button>
              );
            })}
          </div>
          <FormFields
            key={selected}
            fields={[
              {
                name: "date",
                label: "Exception date",
                type: "date",
                value: selected,
              },
              { name: "label", label: "Holiday / exception name" },
              {
                name: "closed",
                label: "Access on this date",
                options: [
                  { value: "yes", label: "Closed — holiday" },
                  { value: "no", label: "Special working hours" },
                ],
              },
              {
                name: "opens",
                label: "Special opening time",
                type: "time",
                required: false,
              },
              {
                name: "closes",
                label: "Special closing time",
                type: "time",
                required: false,
              },
            ]}
            submit="Save exception"
            onSubmit={(d) => {
              const date = text(d, "date"),
                ex: Schedule["exceptions"][number] = {
                  date,
                  label: text(d, "label"),
                  closed: text(d, "closed") === "yes",
                  opens: text(d, "opens") || undefined,
                  closes: text(d, "closes") || undefined,
                };
              return act({
                type: "schedule",
                schedule: {
                  ...data.schedule,
                  exceptions: [
                    ...data.schedule.exceptions.filter((e) => e.date !== date),
                    ex,
                  ],
                },
              });
            }}
          />
          {data.schedule.exceptions.map((ex) => (
            <div className="record" key={ex.date}>
              <strong>
                {ex.date} · {ex.label}
              </strong>
              <small>{ex.closed ? "Closed" : `${ex.opens}–${ex.closes}`}</small>
              <Action
                onClick={() =>
                  act({
                    type: "schedule",
                    schedule: {
                      ...data.schedule,
                      exceptions: data.schedule.exceptions.filter(
                        (e) => e.date !== ex.date,
                      ),
                    },
                  })
                }
              >
                Remove
              </Action>
            </div>
          ))}
        </div>
      </Panel>
    </div>
  );
}
function Settings() {
  const { data, act } = useApp();
  return (
    <div className="record-grid">
      {service.mode === "http" && (
        <Panel title="Visitor card pool">
          <div className="padded">
            <p>
              Add a reusable physical card asset. It becomes available only
              after simulated disable confirmation.
            </p>
            <FormFields
              fields={[{ name: "number", label: "Visitor pool card number" }]}
              submit="Add pool card"
              onSubmit={(d) =>
                act({ type: "pool-card", number: text(d, "number") })
              }
            />
          </div>
        </Panel>
      )}
      {service.mode === "http" && data.simulationControlsEnabled && (
        <BackendSimulation />
      )}
      <Panel title="Payment and uploads">
        <div className="padded">
          <FormFields
            fields={[
              {
                name: "payment",
                label:
                  service.mode === "mock"
                    ? "Fictional demo payment instructions"
                    : "Payment instructions",
                type: "textarea",
                value: data.settings.payment,
              },
              {
                name: "limit",
                label: "Evidence upload limit · MiB",
                type: "number",
                min: "1",
                max: "20",
                value: String(data.settings.uploadLimit / 1048576),
              },
            ]}
            onSubmit={(d) =>
              act({
                type: "settings",
                settings: {
                  ...data.settings,
                  payment: text(d, "payment"),
                  uploadLimit: Number(text(d, "limit")) * 1048576,
                },
              })
            }
          />
        </div>
      </Panel>
      <Panel title="Application roles">
        <div className="padded">
          <p>
            Roles are separate from person types and physical-card privileges.
          </p>
          <FormFields
            fields={[
              {
                name: "personId",
                label: "Person with account",
                options: data.people.map((p) => ({
                  value: p.id,
                  label: p.firstName + " " + p.lastName,
                })),
              },
              {
                name: "role",
                label: "Application role",
                options: roles.map((value) => ({ value, label: value })),
              },
            ]}
            onSubmit={(d) =>
              act({
                type: "role",
                personId: text(d, "personId"),
                role: text(d, "role") as (typeof roles)[number],
              })
            }
          />
        </div>
      </Panel>
      <Panel title="Designated privileged card">
        <div className="padded">
          <p>
            Current:{" "}
            {data.cards.find((c) => c.privileged)?.number ??
              "No active designation"}
            . Replacement must disable the previous credential first.
          </p>
          <FormFields
            fields={[
              {
                name: "cardId",
                label: "Active personal card of a Super Admin",
                options: data.cards
                  .filter((c) => c.kind === "PERSONAL" && !c.revoked)
                  .map((c) => ({ value: c.id, label: c.number })),
              },
            ]}
            submit="Manage designation"
            onSubmit={(d) =>
              act({ type: "designate", cardId: text(d, "cardId") })
            }
          />
        </div>
      </Panel>
      <Panel title="Controllers">
        <div className="padded">
          {data.devices.map((d) => (
            <FormFields
              key={d.id}
              fields={[
                { name: "name", label: `${d.model} · ${d.id}`, value: d.name },
              ]}
              submit="Update controller name"
              onSubmit={(form) =>
                act({ type: "device", id: d.id, name: text(form, "name") })
              }
            />
          ))}
        </div>
      </Panel>
      <Panel title="Backend backups">
        <div className="padded">
          <p>
            Backups are local operator tasks; no backup history is claimed by
            this screen. See the README backup/restore scripts. One-year backup
            retention belongs to backend deployment; this frontend does not
            create or restore backups.
          </p>
        </div>
      </Panel>
    </div>
  );
}
function Recovery() {
  const { data, act, notify } = useApp(),
    [rows, setRows] = useState<{
      people: Person[];
      events: AccessEvent[];
      audits: Audit[];
    }>({ people: [], events: [], audits: [] }),
    [loading, setLoading] = useState(true);
  useEffect(() => {
    service
      .recovery()
      .then(setRows)
      .catch((e) => notify(e.message, true))
      .finally(() => setLoading(false));
  }, [data, notify]);
  return (
    <>
      <div className="callout">
        Restoration retains the original purge deadline. It does not reactivate
        access. Visitor details: 15 + 30 days after access ends; access logs: 90
        + 30 days; audit logs: six calendar months + 30 days.
      </div>
      {loading ? (
        <Empty>Loading recovery records…</Empty>
      ) : (
        Object.entries(rows).map(([family, list]) => (
          <Panel key={family} title={`Soft-deleted ${family}`}>
            <div className="record-list">
              {list.map((row) => (
                <div className="record" key={row.id}>
                  <strong>{row.id}</strong>
                  <Badge value="SOFT_DELETED" />
                  <p>
                    Deleted: <Time value={row.deletedAt} />
                    <br />
                    Original permanent purge: <Time value={row.purgeAt} />
                  </p>
                  <Action
                    onClick={() =>
                      act({
                        type: "restore",
                        family: family as "people" | "events" | "audits",
                        id: row.id,
                      })
                    }
                  >
                    Restore record
                  </Action>
                </div>
              ))}
              {!list.length && <Empty>No recoverable records.</Empty>}
            </div>
          </Panel>
        ))
      )}
    </>
  );
}
export function Notifications() {
  const { data, act } = useApp();
  return (
    <>
      <Heading
        title="Notifications"
        description="Security alerts and updates appropriate to your account."
      />
      <Panel title="In-app alerts">
        <div className="record-list">
          {data.notifications.map((n) => (
            <div className="notification record" key={n.id}>
              <Badge value={n.severity} />
              <div>
                <h2>{n.title}</h2>
                <p>{n.detail}</p>
                <small>
                  <Time value={n.at} /> · Simulated
                </small>
              </div>
              {n.read ? (
                <span className="muted">Read</span>
              ) : (
                <Action onClick={() => act({ type: "notice-read", id: n.id })}>
                  Mark read
                </Action>
              )}
            </div>
          ))}
          {!data.notifications.length && (
            <Empty>No notifications for your account.</Empty>
          )}
        </div>
      </Panel>
    </>
  );
}
export function DemoTools() {
  const { data, act, setIdentity } = useApp();
  const [controls, setControls] = useState(data.controls);
  useEffect(() => setControls(data.controls), [data.controls]);
  if (!import.meta.env.DEV || service.mode !== "mock") return null;
  return (
    <details className="demo-tools">
      <summary>Demo simulation controls</summary>
      <div className="padded">
        <p>
          Development only · all records are synthetic · current clock{" "}
          <Time value={data.clock} />
        </p>
        <div className="demo-switches">
          {(
            [
              "offline",
              "failDisable",
              "delayActivation",
              "unlockTimeout",
            ] as const
          ).map((k) => (
            <label key={k}>
              <input
                type="checkbox"
                checked={controls[k]}
                onChange={(e) => {
                  const updated = { ...controls, [k]: e.target.checked };
                  setControls(updated);
                  void act({ type: "demo-controls", controls: updated }).catch(
                    () => setControls(data.controls),
                  );
                }}
              />
              {
                {
                  offline: "Controller offline",
                  failDisable: "Fail credential disable",
                  delayActivation: "Delay activation / updates",
                  unlockTimeout: "Unlock timeout",
                }[k]
              }
            </label>
          ))}
        </div>
        <div className="button-row">
          {(
            [
              "ENTRY",
              "EXIT",
              "UNKNOWN",
              "REVOKED",
              "FORCED_OPEN",
              "HELD_OPEN",
              "READER_FAULT",
            ] as const
          ).map((event) => (
            <Action
              key={event}
              onClick={() => act({ type: "demo-event", event })}
            >
              Simulate {event.toLowerCase().replaceAll("_", " ")}
            </Action>
          ))}
        </div>
        <FormFields
          fields={[
            {
              name: "clock",
              label: "Advance demo clock · Lagos",
              type: "datetime-local",
              value: new Date(Date.parse(data.clock) + 3600000)
                .toISOString()
                .slice(0, 16),
            },
          ]}
          submit="Advance clock and apply retention"
          onSubmit={(d) =>
            act({ type: "demo-clock", clock: text(d, "clock") + ":00+01:00" })
          }
        />
        <Action
          className="danger"
          onClick={async () => {
            await service.resetDemo();
            setIdentity(null);
          }}
        >
          Reset all demo records and sign out
        </Action>
      </div>
    </details>
  );
}

function BackendSimulation() {
  const { data, act } = useApp();
  const [controls, setControls] = useState(data.controls);
  useEffect(() => setControls(data.controls), [data.controls]);
  return (
    <Panel title="Simulated controller failures">
      <div className="padded">
        <p>
          Controller integration is simulated. These controls require server
          Super Admin authorization and explicit configuration.
        </p>
        <div className="demo-switches">
          {(
            [
              "offline",
              "failDisable",
              "delayActivation",
              "unlockTimeout",
            ] as const
          ).map((key) => (
            <label key={key}>
              <input
                type="checkbox"
                checked={controls[key]}
                onChange={(e) => {
                  const updated = { ...controls, [key]: e.target.checked };
                  setControls(updated);
                  void act({ type: "demo-controls", controls: updated }).catch(
                    () => setControls(data.controls),
                  );
                }}
              />
              {
                {
                  offline: "Controller offline",
                  failDisable: "Fail credential disable",
                  delayActivation: "Delay activation / updates",
                  unlockTimeout: "Unlock timeout",
                }[key]
              }
            </label>
          ))}
        </div>
        <div className="button-row">
          {(
            [
              "ENTRY",
              "EXIT",
              "UNKNOWN",
              "REVOKED",
              "FORCED_OPEN",
              "HELD_OPEN",
              "READER_FAULT",
            ] as const
          ).map((event) => (
            <Action
              key={event}
              onClick={() => act({ type: "demo-event", event })}
            >
              Simulate {event.toLowerCase().replaceAll("_", " ")}
            </Action>
          ))}
        </div>
      </div>
    </Panel>
  );
}
