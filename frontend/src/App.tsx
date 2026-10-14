import { useCallback, useEffect, useRef, useState } from "react";
import {
  BrowserRouter,
  Link,
  NavLink,
  Navigate,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from "react-router";
import { service } from "./services";
import { ServiceError } from "./services/accessControlService";
import type { Command, Identity, Snapshot } from "./models";
import { can, landing, type Capability } from "./permissions";
import { Context } from "./context";
import { Auth } from "./pages/Auth";
import {
  PasswordRecovery,
  ContactAdministrator,
} from "./pages/PasswordRecovery";
import { Dashboard } from "./pages/Dashboard";
import { MyAccess, Profile, Requests } from "./pages/Access";
import { People, Visitors } from "./pages/PeopleVisitors";
import { CardsActivity } from "./pages/Records";
import {
  Administration,
  Notifications,
  DemoTools,
} from "./pages/Administration";
import { Icon, pretty, BrandMark } from "./components/ui";
const modules: {
  path: string;
  label: string;
  icon: string;
  permission?: Capability;
}[] = [
  {
    path: "/dashboard",
    label: "Dashboard",
    icon: "dashboard",
    permission: "dashboard",
  },
  { path: "/my-access", label: "My Access", icon: "card" },
  {
    path: "/requests",
    label: "Card applications",
    icon: "clock",
    permission: "review",
  },
  { path: "/people", label: "People", icon: "people", permission: "people" },
  {
    path: "/cards-activity",
    label: "Cards & activity",
    icon: "card",
    permission: "logs",
  },
  {
    path: "/visitors",
    label: "Visitors",
    icon: "people",
    permission: "visitors",
  },
  {
    path: "/administration",
    label: "Administration",
    icon: "settings",
    permission: "logs",
  },
  { path: "/notifications", label: "Notifications", icon: "bell" },
  { path: "/profile", label: "My profile", icon: "people" },
];
export default function App() {
  return (
    <BrowserRouter>
      <Application />
    </BrowserRouter>
  );
}
function Application() {
  const [identity, setIdentity] = useState<Identity | null>(null),
    [data, setData] = useState<Snapshot | null>(null),
    [initial, setInitial] = useState(true),
    [error, setError] = useState(""),
    [toast, setToast] = useState<{ message: string; error: boolean } | null>(
      null,
    ),
    [drawer, setDrawer] = useState(false),
    [demoIdentities, setDemoIdentities] = useState<Identity[]>([]);
  const location = useLocation(),
    navigate = useNavigate(),
    drawerRef = useRef<HTMLDialogElement>(null);
  const workspaceEpoch = useRef(0);
  const pendingRefresh = useRef<{
    epoch: number;
    promise: Promise<void>;
    again: boolean;
  } | null>(null);
  const notify = useCallback(
    (message: string, error = false) => setToast({ message, error }),
    [],
  );
  const refresh = useCallback(() => {
    const epoch = workspaceEpoch.current;
    // Poll/visibility notifications can overlap. Share one request per account
    // generation, and never apply a response after an account/role change.
    if (pendingRefresh.current?.epoch === epoch) {
      pendingRefresh.current.again = true;
      return pendingRefresh.current.promise;
    }
    const pending = { epoch, promise: Promise.resolve(), again: false };
    pendingRefresh.current = pending;
    pending.promise = (async () => {
      do {
        pending.again = false;
        try {
          const s = await service.snapshot();
          if (epoch !== workspaceEpoch.current) return;
          setData(s);
          setIdentity((previous) =>
            previous &&
            (previous.id !== s.identity.id ||
              previous.personId !== s.identity.personId ||
              previous.role !== s.identity.role ||
              previous.name !== s.identity.name ||
              previous.email !== s.identity.email)
              ? s.identity
              : previous,
          );
          setError("");
        } catch (e) {
          if (epoch !== workspaceEpoch.current) return;
          if (e instanceof ServiceError && e.code === "UNAUTHENTICATED") {
            setData(null);
            setIdentity(null);
            setError((e as Error).message);
            return;
          }
          setError((e as Error).message);
        }
      } while (pending.again && epoch === workspaceEpoch.current);
    })();
    void pending.promise.finally(() => {
      if (pendingRefresh.current === pending) pendingRefresh.current = null;
    });
    return pending.promise;
  }, []);
  useEffect(() => {
    service
      .currentIdentity()
      .then(setIdentity)
      .catch((e) => setError(e.message))
      .finally(() => setInitial(false));
    service
      .demoIdentities()
      .then(setDemoIdentities)
      .catch(() => {});
  }, []);
  const accountId = identity?.id;
  const personId = identity?.personId;
  const accountRole = identity?.role;
  useEffect(() => {
    workspaceEpoch.current++;
    setData(null);
    if (accountId) {
      void service.demoIdentities().then(setDemoIdentities);
      void refresh();
      const unsubscribe = service.subscribe(() => void refresh());
      return () => {
        workspaceEpoch.current++;
        unsubscribe();
      };
    }
  }, [accountId, personId, accountRole, refresh]);
  useEffect(() => {
    setDrawer(false);
    document.title = `${modules.find((m) => m.path === location.pathname)?.label ?? "Welcome"} · IFT E-Library Access Control`;
  }, [location.pathname]);
  useEffect(() => {
    if (!drawer) return;
    const d = drawerRef.current!;
    d.showModal();
    return () => d.close();
  }, [drawer]);
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 9000);
    return () => clearTimeout(t);
  }, [toast]);
  const act = useCallback(
    async (c: Command) => {
      try {
        const result = await service.execute(c);
        notify(result.message, ["FAILED", "UNCERTAIN"].includes(result.result));
        await refresh();
        return result;
      } catch (e) {
        notify((e as Error).message, true);
        throw e;
      }
    },
    [notify, refresh],
  );
  const banner = (
    <div className={`demo-banner ${service.mode === "http" ? "warning" : ""}`}>
      <Icon name="warning" size={15} />
      {service.mode === "mock"
        ? "Demo mode — hardware actions are simulated."
        : data?.controllerMode === "HIKVISION_CONTROLLED"
          ? `Hikvision · ${data.controllerReadStatus === "CONNECTED" ? "event connection active" : "event connection unavailable"} · ${data.controllerWriteMessage}`
          : data?.controllerMode === "SIMULATED"
              ? "Controller integration is simulated."
              : "Controller status available after sign-in."}
    </div>
  );
  if (initial)
    return (
      <div className="boot" role="status">
        Loading IFT E-Library Access Control…
      </div>
    );
  if (
    location.pathname === "/reset-password" ||
    location.pathname === "/forgot-password"
  )
    return (
      <PasswordRecovery
        key={location.key}
        reset={location.pathname === "/reset-password"}
        onReset={() => {
          setIdentity(null);
          setData(null);
        }}
      />
    );
  if (location.pathname === "/contact-admin") return <ContactAdministrator />;
  if (!identity)
    return (
      <>
        {banner}
        <Routes>
          <Route path="/login" element={<Auth onIdentity={setIdentity} />} />
          <Route
            path="/register"
            element={<Auth register onIdentity={setIdentity} />}
          />
          <Route
            path="/register/visitor"
            element={<Auth register visitor onIdentity={setIdentity} />}
          />
          <Route path="*" element={<Navigate to="/login" replace />} />
        </Routes>
        {error && (
          <div className="connection-error" role="alert">
            {error}
            <button
              onClick={() => {
                setError("");
                void service
                  .currentIdentity()
                  .then(setIdentity)
                  .catch((e) => setError(e.message));
              }}
            >
              Retry connection
            </button>
          </div>
        )}
      </>
    );
  const navigation = (
    <>
      <Link className="brand" to={landing(identity.role)}>
        <span className="brand-icon">
          <BrandMark />
        </span>
        <span>
          IFT E-Library<small>ACCESS CONTROL</small>
        </span>
      </Link>
      <span className="nav-label">WORKSPACE</span>
      <nav aria-label="Main navigation">
        {modules
          .filter((m) => !m.permission || can(identity.role, m.permission))
          .map((m) => (
            <NavLink key={m.path} to={m.path}>
              <Icon name={m.icon} />
              <span>{m.label}</span>
              {m.path === "/notifications" &&
                !!data?.notifications.filter((n) => !n.read).length && (
                  <span className="nav-count">
                    {data.notifications.filter((n) => !n.read).length}
                  </span>
                )}
            </NavLink>
          ))}
      </nav>
      <div className="sidebar-bottom">
        <div className="network-note">
          <span className="status-dot" />
          <div>
            Campus local network
            <small>
              {service.mode === "mock"
                ? "Offline-capable demo"
                : "Local server session"}
            </small>
          </div>
        </div>
        <button
          className="logout"
          onClick={async () => {
            await service.logout();
            setIdentity(null);
            navigate("/login");
          }}
        >
          <Icon name="logout" /> Sign out
        </button>
      </div>
    </>
  );
  const guard = (cap: Capability, element: React.ReactNode) =>
    can(identity.role, cap) ? (
      element
    ) : (
      <div className="forbidden">
        <Icon name="shield" size={40} />
        <h1>Access forbidden</h1>
        <p>Your {pretty(identity.role)} role does not permit this page.</p>
        <Link to={landing(identity.role)}>Return to your workspace</Link>
      </div>
    );
  return (
    <>
      {banner}
      <a className="skip-link" href="#main-content">
        Skip to main content
      </a>
      <div className="app-shell">
        <aside className="sidebar">{navigation}</aside>
        {drawer && (
          <dialog
            className="mobile-drawer"
            ref={drawerRef}
            onCancel={() => setDrawer(false)}
            aria-label="Navigation menu"
          >
            <button
              className="drawer-close icon-button"
              onClick={() => setDrawer(false)}
              aria-label="Close navigation"
            >
              <Icon name="close" />
            </button>
            {navigation}
          </dialog>
        )}
        <div className="workspace">
          <header className="topbar">
            <div>
              <button
                className="mobile-menu icon-button"
                aria-label="Open navigation"
                onClick={() => setDrawer(true)}
              >
                <Icon name="menu" />
              </button>
              <span>IFT E-Library Access Control</span>
            </div>
            <div className="topbar-right">
              <span className="timezone">Africa/Lagos</span>
              <Link
                className="icon-button"
                to="/notifications"
                aria-label="View notifications"
              >
                <Icon name="bell" />
              </Link>
              <span className="avatar">
                {identity.name
                  .split(" ")
                  .map((n) => n[0])
                  .slice(0, 2)
                  .join("")}
              </span>
              <div className="identity-label">
                <strong>{identity.name}</strong>
                <small>{pretty(identity.role)}</small>
              </div>
            </div>
          </header>
          {import.meta.env.DEV && demoIdentities.length > 0 && (
            <div className="identity-switch">
              <label>
                Development identity{" "}
                <select
                  value={identity.id}
                  onChange={async (e) => {
                    try {
                      const i = await service.selectDemo(e.target.value);
                      setIdentity(i);
                      navigate(landing(i.role));
                    } catch (e) {
                      notify((e as Error).message, true);
                    }
                  }}
                >
                  {demoIdentities.map((i) => (
                    <option key={i.id} value={i.id}>
                      {i.name} ·{" "}
                      {i.personId === "visitor" ? "VISITOR / USER" : i.role}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          )}
          <main className="main-content" id="main-content" tabIndex={-1}>
            {error && data && (
              <div role="alert" className="callout warning">
                <h2>Unable to refresh workspace</h2>
                <p>{error} Showing the last loaded workspace.</p>
                <button onClick={() => void refresh()}>Retry</button>
              </div>
            )}
            {error && !data ? (
              <div role="alert" className="callout warning">
                <h2>Unable to load workspace</h2>
                <p>{error}</p>
                <button onClick={() => void refresh()}>Retry</button>
              </div>
            ) : !data ? (
              <div className="empty" role="status">
                Loading your workspace…
              </div>
            ) : (
              <Context.Provider
                value={{ data, refresh, act, setIdentity, notify }}
              >
                <Routes>
                  <Route
                    path="/dashboard"
                    element={guard("dashboard", <Dashboard />)}
                  />
                  <Route path="/my-access" element={<MyAccess />} />
                  <Route path="/profile" element={<Profile />} />
                  <Route
                    path="/requests"
                    element={guard("review", <Requests />)}
                  />
                  <Route path="/people" element={guard("people", <People />)} />
                  <Route
                    path="/cards-activity"
                    element={guard("logs", <CardsActivity />)}
                  />
                  <Route
                    path="/visitors"
                    element={guard("visitors", <Visitors />)}
                  />
                  <Route
                    path="/administration"
                    element={guard("logs", <Administration />)}
                  />
                  <Route path="/notifications" element={<Notifications />} />
                  <Route
                    path="/"
                    element={<Navigate to={landing(identity.role)} replace />}
                  />
                  <Route
                    path="/login"
                    element={<Navigate to={landing(identity.role)} replace />}
                  />
                  <Route
                    path="*"
                    element={
                      <div className="empty">
                        <h1>Page not found</h1>
                        <Link to={landing(identity.role)}>
                          Return to workspace
                        </Link>
                      </div>
                    }
                  />
                </Routes>
                <DemoTools />
              </Context.Provider>
            )}
          </main>
          <footer className="footer">
            <span>
              IFT E-Library · Federal University of Technology, Owerri
            </span>
            <span>Local access. Clear accountability.</span>
          </footer>
        </div>
      </div>
      {toast && (
        <div
          className={`toast ${toast.error ? "error" : ""}`}
          role={toast.error ? "alert" : "status"}
        >
          <Icon name={toast.error ? "warning" : "check"} />
          <span>{toast.message}</span>
          <button aria-label="Dismiss message" onClick={() => setToast(null)}>
            <Icon name="close" size={16} />
          </button>
        </div>
      )}
    </>
  );
}
