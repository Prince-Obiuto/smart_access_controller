import { useState } from "react";
import { useApp } from "../context";
import { service } from "../services";
import type { AccessControlService } from "../services/accessControlService";
import { Action, Modal } from "./ui";

export function RemoteUnlock() {
  const { data, notify } = useApp();
  const [open, setOpen] = useState(false);
  const [info, setInfo] = useState<Awaited<
    ReturnType<AccessControlService["unlockStatus"]>
  > | null>(null);
  const [error, setError] = useState("");
  const [configure, setConfigure] = useState(false);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const read = async () => {
    setInfo(null);
    setError("");
    setMessage("");
    try {
      setInfo(await service.unlockStatus());
    } catch (e) {
      setError((e as Error).message);
    }
  };
  return (
    <>
      <Action
        className="primary full"
        onClick={async () => {
          setOpen(true);
          setMessage("");
          setConfigure(false);
          await read();
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
          {!info && !error && !message && (
            <p role="status">Reading Door 1 configuration…</p>
          )}
          {info && (
            <>
              <p>
                Door 1’s configured release is{" "}
                <strong>{info.configuredSeconds} seconds</strong>. Required
                timed release: <strong>5 seconds</strong>.
              </p>
              <p>
                The physical door state is unknown. A controller acknowledgement
                does not prove the door opened.
              </p>
              <p role="status">
                Controller communication:{" "}
                <strong>{info.controllerStatus || "UNKNOWN"}</strong>
              </p>
              {info.uncertainOperationId && (
                <div className="callout warning">
                  <p>
                    The previous command was acknowledged by the controller,
                    but its physical result was not observed by the
                    application.
                  </p>
                  <p>
                    Observe Door 1 release and its return to the secured state,
                    then confirm that observation here. Do not press unlock
                    again.
                  </p>
                  <Action
                    onClick={async () => {
                      if (busy) return;
                      setBusy(true);
                      setError("");
                      try {
                        const result = await service.confirmPhysicalUnlock();
                        setMessage(result.message);
                        notify(result.message, false);
                        await read();
                      } catch (e) {
                        setError((e as Error).message);
                      } finally {
                        setBusy(false);
                      }
                    }}
                    disabled={busy}
                  >
                    {busy
                      ? "Saving observation…"
                      : "Confirm Door 1 released and relocked"}
                  </Action>
                </div>
              )}
              {configure ? (
                <>
                  <p>
                    Change Door 1’s normal release duration from{" "}
                    {info.configuredSeconds} to 5 seconds? This applies to Door
                    1’s normal timed releases.
                  </p>
                  <Action
                    className="primary"
                    onClick={async () => {
                      if (busy) return;
                      setBusy(true);
                      setError("");
                      try {
                        const result = await service.configureUnlockDuration();
                        setMessage(result.message);
                        notify(result.message, result.status !== "CONFIRMED");
                        setConfigure(false);
                        await read();
                      } catch (e) {
                        setError((e as Error).message);
                      } finally {
                        setBusy(false);
                      }
                    }}
                    disabled={busy}
                  >
                    {busy ? "Waiting for controller…" : "Confirm duration change"}
                  </Action>
                  <button onClick={() => setConfigure(false)}>
                    Cancel change
                  </button>
                </>
              ) : (
                <>
                  {!info.ready && (
                    <p role="status">
                      The configured duration must match five seconds before
                      remote unlock is available.
                    </p>
                  )}
                  {info.ready && (
                    <Action
                      className="primary"
                      onClick={async () => {
                        if (busy) return;
                        setBusy(true);
                        setError("");
                        try {
                          const result = await service.remoteUnlock();
                          setMessage(result.message);
                          notify(result.message, result.status === "FAILED");
                          setInfo((current) =>
                            current
                              ? {
                                  ...current,
                                  sensor: result.physicalState,
                                }
                              : current,
                          );
                        } catch (e) {
                          setError((e as Error).message);
                        } finally {
                          setBusy(false);
                        }
                      }}
                      disabled={busy}
                    >
                      {busy
                        ? "Waiting for controller…"
                        : "Unlock Door 1 once for 5 seconds"}
                    </Action>
                  )}
                  {!info.ready && data.identity.role === "SUPER_ADMIN" && (
                    <button onClick={() => setConfigure(true)}>
                      Set release duration to 5 seconds
                    </button>
                  )}
                </>
              )}
            </>
          )}
          {message && <p role="status">{message}</p>}
        </Modal>
      )}
    </>
  );
}
