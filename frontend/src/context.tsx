import { createContext, useContext } from "react";
import type { Snapshot, Command, OperationResult, Identity } from "./models";
export interface AppContext {
  data: Snapshot;
  refresh: () => Promise<void>;
  act: (c: Command) => Promise<OperationResult>;
  setIdentity: (i: Identity | null) => void;
  notify: (message: string, error?: boolean) => void;
}
export const Context = createContext<AppContext | null>(null);
export function useApp() {
  const c = useContext(Context);
  if (!c) throw new Error("Missing app context");
  return c;
}
