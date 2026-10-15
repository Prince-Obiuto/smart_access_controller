import { useState } from "react";
import { useApp } from "../context";
import { service } from "../services";
import type {
  ControllerPlanRead,
  ManagementPreview,
  PlanKind,
  PlanRecord,
} from "../services/accessControlService";
import { Action, Badge, Panel, Time } from "../components/ui";
import { PreviewChange } from "./ControllerAccess";

const weekdays = [
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
  "Sunday",
];
const time = (s: number) =>
  `${String(Math.floor(s / 3600)).padStart(2, "0")}:${String(Math.floor(s / 60) % 60).padStart(2, "0")}`;
const seconds = (v: string) =>
  Number(v.slice(0, 2)) * 3600 + Number(v.slice(3, 5)) * 60;
const toDate = (v?: number[]) =>
  v?.map((n, i) => String(n).padStart(i ? 2 : 4, "0")).join("-") || "";

export function ControllerSchedules() {
  const { data, refresh } = useApp();
  const [kind, setKind] = useState<PlanKind>("TEMPLATE"),
    [slot, setSlot] = useState(3);
  const [read, setRead] = useState<ControllerPlanRead | null>(null),
    [draft, setDraft] = useState<PlanRecord | null>(null);
  const [preview, setPreview] = useState<ManagementPreview | null>(null),
    [error, setError] = useState("");
  const [history, setHistory] = useState<ManagementPreview[]>([]);
  const [labels, setLabels] = useState<number[]>([0, 1, 2, 3, 4, 5, 6]),
    [source, setSource] = useState("");
  if (data.identity.role !== "SUPER_ADMIN")
    return (
      <Panel title="Controller schedules">
        <p className="padded">
          Only Super Admin can edit physical controller schedules. Local
          schedule drafts do not apply controller changes.
        </p>
      </Panel>
    );
  const run = async (work: () => Promise<unknown>) => {
    try {
      setError("");
      await work();
    } catch (e) {
      setError((e as Error).message);
      throw e;
    }
  };
  const load = async () =>
    run(async () => {
      const result = await service.readControllerPlan(kind, slot);
      setRead(result);
      const record = structuredClone(result.record);
      if (record.days && result.weekdayOrder) {
        const days = record.days;
        record.days = weekdays.map(
          (_, day) => days[result.weekdayOrder!.indexOf(day)],
        );
      }
      setDraft(record);
      setHistory((await service.controllerAccess()).management || []);
    });
  const update = (patch: Partial<PlanRecord>) =>
    setDraft({ ...draft!, ...patch });
  const intervals = (segments: number[][], change: (s: number[][]) => void) =>
    segments.map((s, i) => (
      <div className="controller-interval" key={i}>
        <label>
          <input
            aria-label={`Enable interval ${i + 1}`}
            type="checkbox"
            checked={s[0] === 1}
            onChange={(e) =>
              change(
                segments.map((v, n) =>
                  n === i
                    ? [
                        Number(e.target.checked),
                        v[1],
                        v[2] > v[1] ? v[2] : Math.min(v[1] + 3600, 86400),
                        ...v.slice(3),
                      ]
                    : v,
                ),
              )
            }
          />{" "}
          Interval {i + 1}
        </label>
        <input
          aria-label={`Interval ${i + 1} start`}
          type="time"
          value={time(s[1])}
          onChange={(e) =>
            change(
              segments.map((v, n) =>
                n === i ? [v[0], seconds(e.target.value), ...v.slice(2)] : v,
              ),
            )
          }
        />
        <input
          aria-label={`Interval ${i + 1} end`}
          type="time"
          disabled={s[2] === 86400}
          value={time(s[2] === 86400 ? 0 : s[2])}
          onChange={(e) =>
            change(
              segments.map((v, n) =>
                n === i
                  ? [v[0], v[1], seconds(e.target.value), ...v.slice(3)]
                  : v,
              ),
            )
          }
        />
        <label>
          <input
            type="checkbox"
            checked={s[2] === 86400}
            onChange={(e) =>
              change(
                segments.map((v, n) =>
                  n === i
                    ? [
                        v[0],
                        v[1],
                        e.target.checked ? 86400 : 86340,
                        ...v.slice(3),
                      ]
                    : v,
                ),
              )
            }
          />{" "}
          End of day
        </label>
      </div>
    ));
  const desired = (): Record<string, unknown> => {
    const d = draft!;
    const base = { enabled: d.enabled };
    if (kind === "WEEK")
      return {
        ...base,
        days: d.days!.map((day) => day.map((s) => s.slice(0, 3))),
      };
    if (kind === "HOLIDAY")
      return {
        ...base,
        start: d.start,
        end: d.end,
        segments: d.segments!.map((s) => s.slice(0, 3)),
      };
    if (kind === "GROUP") return { ...base, plans: d.plans };
    return { ...base, week: d.week, groups: d.groups };
  };
  return (
    <>
      <Panel title="Live controller schedules">
        <div className="padded">
          <p>
            Read a specific existing slot, edit its card-access configuration,
            then preview the affected cards. No controller change is sent until
            the reviewed operation is confirmed. Existing names and reserved
            fields are preserved.
          </p>
          <label>
            Plan type{" "}
            <select
              value={kind}
              onChange={(e) => {
                setKind(e.target.value as PlanKind);
                setRead(null);
                setDraft(null);
              }}
            >
              {["TEMPLATE", "WEEK", "GROUP", "HOLIDAY"].map((k) => (
                <option key={k}>{k}</option>
              ))}
            </select>
          </label>
          <label>
            Slot number{" "}
            <input
              type="number"
              min={1}
              max={65535}
              value={slot}
              onChange={(e) => {
                setSlot(Number(e.target.value));
                setRead(null);
                setDraft(null);
              }}
            />
          </label>
          <Action onClick={load}>Refresh from controller</Action>
          {error && (
            <p className="form-error" role="alert">
              {error}
            </p>
          )}
          <p className="muted">
            Total template capacity remains unknown. This interface does not
            scan for unused slots or infer capacity from an SDK array limit.
            Door 2 shared-plan changes are blocked.
          </p>
          {read && draft && (
            <>
              <p>
                {read.kind} {read.slot} · {read.record.name} ·{" "}
                <Time value={read.readAt} />
              </p>
              <label>
                <input
                  type="checkbox"
                  checked={draft.enabled === 1}
                  onChange={(e) =>
                    update({ enabled: Number(e.target.checked) })
                  }
                />{" "}
                Plan enabled
              </label>
              {kind === "WEEK" && (
                <>
                  <p>
                    {read.weekdayOrder
                      ? "Weekday order established by independently labelled controller evidence."
                      : "Weekday order remains unverified. Below are native indices, not named weekdays. Only a uniform seven-day change can be applied until independent evidence is recorded."}
                  </p>
                  {draft.days!.map((day, d) => (
                    <details key={d}>
                      <summary>
                        {read.weekdayOrder ? weekdays[d] : `Native index ${d}`}
                      </summary>
                      {intervals(day, (s) =>
                        update({
                          days: draft.days!.map((v, n) => (n === d ? s : v)),
                        }),
                      )}
                    </details>
                  ))}
                  <Action
                    onClick={() =>
                      update({
                        days: Array.from({ length: 7 }, () =>
                          structuredClone(draft.days![0]),
                        ),
                      })
                    }
                  >
                    Use first day’s intervals for all seven days
                  </Action>
                  <Action
                    onClick={() =>
                      update({
                        days: Array.from({ length: 7 }, () => [
                          [1, 0, 86400, 0, 0],
                          ...Array.from({ length: 7 }, () => [0, 0, 0, 0, 0]),
                        ]),
                      })
                    }
                  >
                    Prepare all-day access on all seven days
                  </Action>
                  {!read.weekdayOrder && (
                    <details>
                      <summary>Record independent weekday evidence</summary>
                      <p>
                        Use a freshly retrieved iVMS view from this controller
                        with seven distinct daily patterns. Match every native
                        index to its independently labelled weekday. Uniform or
                        partly repeated patterns cannot prove all seven indices.
                        This records evidence only and sends no schedule write.
                      </p>
                      {labels.map((v, i) => (
                        <label key={i}>
                          Native index {i}
                          <select
                            value={v}
                            onChange={(e) =>
                              setLabels(
                                labels.map((n, j) =>
                                  j === i ? Number(e.target.value) : n,
                                ),
                              )
                            }
                          >
                            {weekdays.map((day, d) => (
                              <option key={day} value={d}>
                                {day}
                              </option>
                            ))}
                          </select>
                        </label>
                      ))}
                      <label>
                        Controller-retrieved iVMS view reference{" "}
                        <input
                          value={source}
                          onChange={(e) => setSource(e.target.value)}
                        />
                      </label>
                      <Action
                        disabled={
                          new Set(labels).size !== 7 ||
                          source.trim().length < 10
                        }
                        onClick={() =>
                          run(async () => {
                            const labelled = weekdays.map((_, d) =>
                              read.record.days![labels.indexOf(d)].map((s) =>
                                s.slice(0, 3),
                              ),
                            );
                            await service.attestControllerWeekdays(
                              read.slot,
                              labelled,
                              source,
                            );
                            await load();
                          })
                        }
                      >
                        Confirm independent labels and record evidence
                      </Action>
                    </details>
                  )}
                </>
              )}
              {kind === "HOLIDAY" && (
                <>
                  <label>
                    First date{" "}
                    <input
                      type="date"
                      value={toDate(draft.start)}
                      onChange={(e) =>
                        update({ start: e.target.value.split("-").map(Number) })
                      }
                    />
                  </label>
                  <label>
                    Last date{" "}
                    <input
                      type="date"
                      value={toDate(draft.end)}
                      onChange={(e) =>
                        update({ end: e.target.value.split("-").map(Number) })
                      }
                    />
                  </label>
                  {intervals(draft.segments!, (s) => update({ segments: s }))}
                </>
              )}
              {kind === "TEMPLATE" && (
                <label>
                  Linked week{" "}
                  <input
                    type="number"
                    min={1}
                    max={64}
                    value={draft.week}
                    onChange={(e) => update({ week: Number(e.target.value) })}
                  />
                </label>
              )}
              {(kind === "TEMPLATE" || kind === "GROUP") && (
                <fieldset>
                  <legend>
                    {kind === "TEMPLATE"
                      ? "Holiday groups (0 = unused)"
                      : "Holiday plans (0 = unused)"}
                  </legend>
                  {(kind === "TEMPLATE" ? draft.groups! : draft.plans!).map(
                    (n, i) => (
                      <label key={i}>
                        Reference {i + 1}
                        <input
                          type="number"
                          min={0}
                          max={65535}
                          value={n}
                          onChange={(e) => {
                            const key =
                              kind === "TEMPLATE" ? "groups" : "plans";
                            update({
                              [key]: draft[key]!.map((v, j) =>
                                j === i ? Number(e.target.value) : v,
                              ),
                            });
                          }}
                        />
                      </label>
                    ),
                  )}
                </fieldset>
              )}
              <Action
                className="primary"
                onClick={() =>
                  run(async () =>
                    setPreview(
                      await service.previewControllerPlan(
                        read.kind,
                        read.slot,
                        read.revision,
                        desired(),
                      ),
                    ),
                  )
                }
              >
                Preview schedule change and affected cards
              </Action>
              <details>
                <summary>Complete baseline</summary>
                <pre className="controller-json">
                  {JSON.stringify(read.record, null, 2)}
                </pre>
              </details>
              {history
                .filter(
                  (p) =>
                    p.kind === read.kind &&
                    p.slot === read.slot &&
                    p.status === "CONFIRMED",
                )
                .map((p) => (
                  <div key={p.id} className="callout">
                    <p>
                      Saved operation {p.id} · <Badge value={p.status} />
                    </p>
                    <Action
                      onClick={() =>
                        run(async () =>
                          setPreview(
                            await service.previewControllerPlan(
                              read.kind,
                              read.slot,
                              read.revision,
                              {},
                              p.id,
                            ),
                          ),
                        )
                      }
                    >
                      Preview exact preimage restoration
                    </Action>
                  </div>
                ))}
            </>
          )}
        </div>
      </Panel>
      {preview && (
        <PreviewChange
          key={preview.id}
          preview={preview}
          onClose={() => setPreview(null)}
          onResult={async (p) => {
            setPreview(p);
            await refresh();
          }}
        />
      )}
    </>
  );
}
