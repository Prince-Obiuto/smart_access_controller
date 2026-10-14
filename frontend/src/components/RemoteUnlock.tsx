import { useRef, useState } from "react";
import { useApp } from "../context";
import { service } from "../services";
import type {
  AccessControlService,
  UnlockOperation,
} from "../services/accessControlService";
import { Action, Badge, Modal } from "./ui";

export function RemoteUnlock() {
  const { data, notify } = useApp();
  const [open, setOpen] = useState(false);
  const [info, setInfo] = useState<Awaited<
    ReturnType<AccessControlService["unlockStatus"]>
  > | null>(null);
  const [last, setLast] = useState<UnlockOperation | null>(null);
  const [error, setError] = useState("");
  const [configure, setConfigure] = useState(false);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [reading, setReading] = useState(false);
  const inFlight = useRef(false);
  const read = async (operationId?: string) => {
    setReading(true);
    try {
      const current = await service.unlockStatus(operationId);
      setInfo(current);
      if (current.operation) setLast(current.operation);
    } catch (e) {
      setError((e as Error).message);
      setInfo((current) =>
        current
          ? { ...current, ready: false, controllerStatus: "FAILED" }
          : current,
      );
    } finally {
      setReading(false);
    }
  };
  const submit = async (
    work: () => Promise<UnlockOperation>,
    unlock = true,
  ) => {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError("");
    try {
      const result = await work();
      if (unlock) setLast(result);
      setMessage(result.message);
      notify(result.message, result.status === "FAILED");
      setConfigure(false);
      await read(unlock ? result.operationId : last?.operationId);
    } catch (e) {
      setError((e as Error).message);
      await read(last?.operationId);
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  };
  const unresolved = [...(info?.unresolvedOperations ?? [])];
  if (
    last &&
    ["PENDING", "UNCERTAIN"].includes(last.status) &&
    !unresolved.some((o) => o.operationId === last.operationId)
  )
    unresolved.push(last);
  const blocked = busy || reading || unresolved.length > 0 || !info?.ready;
  return (
    <>
      <Action
        className="primary full"
        onClick={async () => {
          setOpen(true);
          setConfigure(false);
          setError("");
          await read(last?.operationId);
        }}
      >
        Remote unlock
      </Action>
      {open && (
        <Modal
          title={
            configure ? "Set Door 1 release duration" : "Confirm remote unlock"
          }
          onClose={() => setOpen(false)}
        >
          {error && (
            <p role="alert" className="form-error">
              {error}
            </p>
          )}
          {reading && <p role="status">Reading Door 1 status…</p>}
          {info && (
            <>
              <p>
                Door 1’s configured release:{" "}
                <strong>
                  {info.configuredSeconds === null
                    ? "Unavailable"
                    : `${info.configuredSeconds} seconds`}
                </strong>
                . Required timed release: <strong>5 seconds</strong>.
              </p>
              <p>
                Controller communication:{" "}
                <strong>{info.controllerStatus}</strong>. Door sensor:{" "}
                <strong>{info.sensor}</strong>.
              </p>
              {info.controllerError && (
                <p role="alert">
                  Controller read failed: {info.controllerError}.
                </p>
              )}
              {last && (
                <div className="callout">
                  <p style={{ overflowWrap: "anywhere" }}>
                    Submitted operation: <strong>{last.operationId}</strong> ·{" "}
                    <Badge value={last.status} />
                  </p>
                  <p>
                    SDK acknowledgement:{" "}
                    {last.sdkStatus ??
                      (last.sdkAcknowledged === true
                        ? "ACKNOWLEDGED"
                        : "UNKNOWN")}
                    .
                  </p>
                  <p>
                    Transport: {last.transportStatus ?? "UNKNOWN"}. Physical
                    result: {last.physicalState}.
                  </p>
                </div>
              )}
              {unresolved.length > 0 && (
                <p role="status">
                  Another unlock is blocked until every unresolved Door 1
                  operation is observed or reconciled.
                </p>
              )}
              {unresolved.map((o) => (
                <div
                  className="callout warning"
                  key={o.operationId}
                  role="group"
                  aria-label={`Unlock operation ${o.operationId}`}
                >
                  <p style={{ overflowWrap: "anywhere" }}>
                    Operation: <strong>{o.operationId}</strong> ·{" "}
                    <Badge value={o.status} />
                  </p>
                  <p>
                    SDK acknowledgement:{" "}
                    {o.sdkStatus ??
                      (o.sdkAcknowledged === true ? "ACKNOWLEDGED" : "UNKNOWN")}
                    . Transport: {o.transportStatus ?? "UNKNOWN"}.
                  </p>
                  <p>{o.message}</p>
                  <p>
                    Confirm only the release and relocking you observed for this
                    operation. If you missed its release, check that Door 1 is
                    secured now and reconcile without claiming the past release.
                  </p>
                  {o.status === "UNCERTAIN" && o.sdkStatus !== "SIMULATED" && (
                    <Action
                      disabled={busy || reading}
                      onClick={() =>
                        submit(() =>
                          service.confirmPhysicalUnlock(o.operationId),
                        )
                      }
                    >
                      Confirm this release and relocking
                    </Action>
                  )}
                  <Action
                    disabled={busy || reading}
                    onClick={() =>
                      submit(() =>
                        service.reconcileUnlock(
                          o.operationId,
                          "Operator checked Door 1 is secured now; original release was not observed.",
                        ),
                      )
                    }
                  >
                    Door is secured now; original release unobserved
                  </Action>
                </div>
              ))}
              {configure ? (
                <>
                  <p>
                    Change Door 1’s normal release duration from{" "}
                    {info.configuredSeconds} to 5 seconds?
                  </p>
                  <Action
                    className="primary"
                    disabled={busy || reading}
                    onClick={() =>
                      submit(() => service.configureUnlockDuration(), false)
                    }
                  >
                    Confirm duration change
                  </Action>
                  <button disabled={busy} onClick={() => setConfigure(false)}>
                    Cancel change
                  </button>
                </>
              ) : (
                <>
                  {info.configuredSeconds !== 5 && (
                    <p role="status">
                      The configured duration must match five seconds before
                      remote unlock is available.
                    </p>
                  )}
                  {info.configuredSeconds === 5 && (
                    <Action
                      className="primary"
                      disabled={blocked}
                      onClick={() => submit(() => service.remoteUnlock())}
                    >
                      Unlock Door 1 once for 5 seconds
                    </Action>
                  )}
                  {info.configuredSeconds !== null &&
                    info.configuredSeconds !== 5 &&
                    info.controllerStatus === "CONNECTED" &&
                    data.identity.role === "SUPER_ADMIN" && (
                      <button
                        disabled={busy || reading}
                        onClick={() => setConfigure(true)}
                      >
                        Set release duration to 5 seconds
                      </button>
                    )}
                </>
              )}
              <Action
                disabled={busy || reading}
                onClick={() => read(last?.operationId)}
              >
                Refresh unlock status
              </Action>
            </>
          )}
          {message && <p role="status">{message}</p>}
        </Modal>
      )}
    </>
  );
}
