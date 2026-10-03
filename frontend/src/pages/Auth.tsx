import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router";
import { service } from "../services";
import type { Identity, Registration } from "../models";
import { landing } from "../permissions";
import {
  FormFields,
  Icon,
  text,
  BrandMark,
  type Field,
} from "../components/ui";
export function Auth({
  register = false,
  visitor = false,
  onIdentity,
}: {
  register?: boolean;
  visitor?: boolean;
  onIdentity: (i: Identity) => void;
}) {
  const [identities, setIdentities] = useState<Identity[]>([]),
    [error, setError] = useState("");
  const nav = useNavigate();
  useEffect(() => {
    service
      .demoIdentities()
      .then(setIdentities)
      .catch((e) => setError(e.message));
  }, []);
  const enter = (i: Identity) => {
    onIdentity(i);
    nav(landing(i.role));
  };
  const fields: Field[] = register
    ? [
        ...(!visitor
          ? [
              {
                name: "type",
                label: "Person type",
                options: [
                  { value: "STUDENT", label: "Student" },
                  { value: "STAFF", label: "Staff" },
                ],
              },
              { name: "reference", label: "Registration number / staff ID" },
            ]
          : []),
        { name: "firstName", label: "First name" },
        { name: "lastName", label: "Last name" },
        {
          name: "email",
          label: visitor ? "Email address" : "University email",
          type: "email",
          hint: visitor
            ? "Personal email is welcome."
            : "Exactly @futo.edu.ng; domain validation does not verify ownership.",
        },
        { name: "phone", label: "Phone", type: "tel" },
        {
          name: "department",
          label: visitor ? "Organization / department" : "Department",
        },
        {
          name: "photo",
          label: "Photo",
          type: "file",
          accept: "image/jpeg,image/png",
        },
        {
          name: "password",
          label: "Password",
          type: "password",
          hint: "At least eight characters; held in memory in this demo.",
        },
      ]
    : [
        { name: "email", label: "Email address", type: "email" },
        { name: "password", label: "Password", type: "password" },
      ];
  return (
    <div className="auth-page">
      <div className="auth-intro">
        <div className="brand">
          <span className="brand-icon">
            <BrandMark />
          </span>
          <span>
            IFT E-Library
            <br />
            <small>ACCESS CONTROL</small>
          </span>
        </div>
        <div>
          <span className="eyebrow">
            FEDERAL UNIVERSITY OF TECHNOLOGY, OWERRI
          </span>
          <h1>
            Your library.
            <br />
            Your access.
          </h1>
          <p>
            Manage your library card, follow your applications and keep your
            access up to date.
          </p>
        </div>
        <div className="auth-foot">
          <Icon name="shield" /> Built for the campus local network
        </div>
      </div>
      <main className="auth-form">
        <div className="auth-box">
          <span className="eyebrow">IFT E-LIBRARY ACCESS CONTROL</span>
          <h1>
            {register
              ? visitor
                ? "Register as a visitor"
                : "Create your account"
              : "Welcome back"}
          </h1>
          <p>
            {register
              ? "Your account is ready immediately. Physical access requires a separate card assignment."
              : "Sign in to manage your library access."}
          </p>
          <FormFields
            fields={fields}
            submit={register ? "Create account" : "Sign in"}
            onSubmit={async (d) => {
              if (register) {
                const photo = d.get("photo") as File;
                enter(
                  await service.register({
                    type: visitor
                      ? "VISITOR"
                      : (text(d, "type") as Registration["type"]),
                    reference: text(d, "reference"),
                    firstName: text(d, "firstName"),
                    lastName: text(d, "lastName"),
                    email: text(d, "email"),
                    phone: text(d, "phone"),
                    department: text(d, "department"),
                    password: text(d, "password"),
                    photo: photo?.size ? photo : undefined,
                  }),
                );
              } else
                enter(
                  await service.login(text(d, "email"), text(d, "password")),
                );
            }}
          />
          {register ? (
            <p>
              Already registered? <Link to="/login">Sign in</Link>
            </p>
          ) : (
            <p>
              New here? <Link to="/register">Student / staff registration</Link>
              <br />
              Visiting the library?{" "}
              <Link to="/register/visitor">Register as a visitor</Link>
            </p>
          )}
          {service.mode === "mock" && (
            <div className="demo-entry">
              <span className="eyebrow">DEVELOPMENT DEMO ENTRY</span>
              <p>
                Explore a role using fictional records. Hardware actions are
                simulated.
              </p>
              {identities.length ? (
                <div className="demo-buttons">
                  {identities.map((i) => (
                    <button
                      key={i.id}
                      onClick={async () => {
                        try {
                          enter(await service.selectDemo(i.id));
                        } catch (e) {
                          setError((e as Error).message);
                        }
                      }}
                    >
                      {i.personId === "visitor"
                        ? "Visitor"
                        : i.role.replaceAll("_", " ")}{" "}
                      <Icon name="arrow" size={15} />
                    </button>
                  ))}
                </div>
              ) : (
                <p>
                  Demo role selection is available only in the development
                  server. Register a synthetic account to explore this
                  production preview.
                </p>
              )}
            </div>
          )}
          {service.mode === "http" && (
            <div className="callout warning">
              HTTP adapter not connected. The future backend must implement the
              documented contract.
            </div>
          )}
          {error && (
            <p role="alert" className="form-error">
              {error}
            </p>
          )}
        </div>
      </main>
    </div>
  );
}
