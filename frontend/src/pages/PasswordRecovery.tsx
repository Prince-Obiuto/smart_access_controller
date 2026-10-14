import { useEffect, useState } from "react";
import { Link } from "react-router";
import { service } from "../services";
import { FormFields, text } from "../components/ui";

// Fragment never reaches HTTP/access logs; scrub browser history immediately.
// Retain only in component memory, never localStorage/sessionStorage.
export function PasswordRecovery({
  reset = false,
  onReset,
}: {
  reset?: boolean;
  onReset?: () => void;
}) {
  const [token] = useState(() => {
    const value =
      new URLSearchParams(window.location.hash.slice(1)).get("token") ?? "";
    return value;
  });
  useEffect(() => {
    if (reset) window.history.replaceState(window.history.state, "", window.location.pathname);
  }, [reset]);
  const [options, setOptions] = useState<{
    available: boolean;
    contact: string;
  } | null>(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    service
      .recoveryOptions()
      .then(setOptions)
      .catch(() =>
        setError(
          "Recovery service is unavailable. Contact the library administrator.",
        ),
      );
  }, []);
  return (
    <main className="auth-page">
      <div className="auth-form">
        <div className="auth-box">
          <h1>{reset ? "Reset password" : "Forgot password"}</h1>
          <p>
            {reset
              ? "Choose a new password of 12–128 characters. After resetting, sign in normally."
              : "Enter the email registered to your account."}
          </p>
          {error && (
            <p role="alert" className="form-error">
              {error}
            </p>
          )}
          {message ? (
            <p role="status">{message}</p>
          ) : reset ? (
            token ? (
              <FormFields
                fields={[
                  { name: "password", label: "New password", type: "password" },
                  {
                    name: "confirmation",
                    label: "Confirm new password",
                    type: "password",
                  },
                ]}
                submit="Reset password"
                onSubmit={async (data) => {
                  const password = text(data, "password");
                  const confirmation = text(data, "confirmation");
                  if (
                    password.length < 12 ||
                    password.length > 128 ||
                    password !== confirmation
                  )
                    throw new Error(
                      "Matching passwords of 12–128 characters are required.",
                    );
                  setMessage(
                    (await service.resetPassword(token, password, confirmation))
                      .message,
                  );
                  onReset?.();
                }}
              />
            ) : (
              <p role="alert">
                This recovery link is missing or invalid. Request a new link.
              </p>
            )
          ) : options?.available ? (
            <FormFields
              fields={[
                {
                  name: "email",
                  label: "Registered email address",
                  type: "email",
                },
              ]}
              submit="Request recovery email"
              onSubmit={async (data) =>
                setMessage(
                  (await service.requestPasswordReset(text(data, "email")))
                    .message,
                )
              }
            />
          ) : options ? (
            <p role="status">Email recovery is unavailable.</p>
          ) : (
            !error && <p role="status">Checking recovery availability…</p>
          )}
          {options && (
            <p>
              {options.contact}{" "}
              <Link to="/contact-admin">Contact administrator</Link>
            </p>
          )}
          <p>
            <Link to="/login">Back to sign in</Link>
            {reset && (
              <>
                {" "}
                · <Link to="/forgot-password">Request a new link</Link>
              </>
            )}
          </p>
        </div>
      </div>
    </main>
  );
}

export function ContactAdministrator() {
  const [contact, setContact] = useState(
    "Contact the IFT E-Library administrator in person.",
  );
  useEffect(() => {
    service
      .recoveryOptions()
      .then((o) => setContact(o.contact))
      .catch(() => {});
  }, []);
  return (
    <main className="auth-page">
      <div className="auth-form">
        <div className="auth-box">
          <h1>Contact administrator</h1>
          <p>{contact}</p>
          <p>
            Bring your registration or staff ID, or visitor identity. An
            authorized administrator must verify your identity before initiating
            recovery. Never share your password.
          </p>
          <p>
            <Link to="/login">Back to sign in</Link>
          </p>
        </div>
      </div>
    </main>
  );
}
