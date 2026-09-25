import { createContext, useContext, type ReactNode } from "react";
import { useDictation as useDictationSession } from "@/hooks/use-dictation";

const DictationContext = createContext<ReturnType<
  typeof useDictationSession
> | null>(null);

// Lives above the route outlet: changing pages never reconnects the native session.
export function DictationProvider({ children }: { children: ReactNode }) {
  const session = useDictationSession();
  return <DictationContext value={session}>{children}</DictationContext>;
}

export function useDictation() {
  const session = useContext(DictationContext);
  if (!session) throw new Error("DictationProvider missing");
  return session;
}
