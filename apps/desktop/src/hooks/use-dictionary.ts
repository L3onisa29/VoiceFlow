import { useCallback, useEffect, useRef, useState } from "react";
import { dictionaryApi, type Dictionary } from "@/lib/dictionary";

export function useDictionary() {
  const [data, setData] = useState<Dictionary | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pending = useRef(false);
  const mounted = useRef(false);
  const refresh = useCallback(async () => {
    if (pending.current) return;
    pending.current = true;
    setLoading(true);
    setError(null);
    try {
      const snapshot = await dictionaryApi.read();
      if (mounted.current) setData(snapshot);
    } catch (error) {
      if (mounted.current) setError(String(error));
    } finally {
      pending.current = false;
      if (mounted.current) setLoading(false);
    }
  }, []);
  useEffect(() => {
    mounted.current = true;
    void refresh();
    return () => {
      mounted.current = false;
    };
  }, [refresh]);

  const mutate = async (operation: () => Promise<Dictionary>) => {
    if (pending.current) return false;
    pending.current = true;
    setBusy(true);
    setError(null);
    try {
      const snapshot = await operation();
      if (mounted.current) setData(snapshot);
      return true;
    } catch (error) {
      if (mounted.current) setError(String(error));
      return false;
    } finally {
      pending.current = false;
      if (mounted.current) setBusy(false);
    }
  };
  return {
    data,
    loading,
    busy,
    error,
    refresh,
    mutate,
    clearError: () => setError(null),
  };
}
