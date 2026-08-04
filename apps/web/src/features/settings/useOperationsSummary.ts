import { useCallback, useEffect, useRef, useState } from "react";
import { fetchOperationsSummary } from "../../api/operationsSummary";
import type { OperationsSummary } from "../../api/operationsSummaryContract";
import { demoOperationsSummary } from "../../fixtures/operationsSummary";

export type OperationsSummaryState =
  | { phase: "loading"; data: null; error: null }
  | { phase: "ready"; data: OperationsSummary; error: null }
  | { phase: "refreshing"; data: OperationsSummary; error: null }
  | { phase: "error"; data: OperationsSummary | null; error: string };

export type OperationsSummaryLoader = (signal?: AbortSignal) => Promise<OperationsSummary>;

interface UseOperationsSummaryOptions {
  demo: boolean;
  loader?: OperationsSummaryLoader;
}

interface UseOperationsSummaryResult {
  state: OperationsSummaryState;
  refresh: () => void;
}

export function useOperationsSummary({
  demo,
  loader = fetchOperationsSummary,
}: UseOperationsSummaryOptions): UseOperationsSummaryResult {
  const [state, setState] = useState<OperationsSummaryState>(() => demo
    ? { phase: "ready", data: demoOperationsSummary, error: null }
    : { phase: "loading", data: null, error: null });
  const requestId = useRef(0);
  const controller = useRef<AbortController | null>(null);

  const refresh = useCallback(() => {
    if (demo) {
      setState({ phase: "ready", data: demoOperationsSummary, error: null });
      return;
    }
    const id = ++requestId.current;
    controller.current?.abort();
    const nextController = new AbortController();
    controller.current = nextController;
    setState((current) => current.data
      ? { phase: "refreshing", data: current.data, error: null }
      : { phase: "loading", data: null, error: null });
    void loader(nextController.signal).then((data) => {
      if (requestId.current === id) setState({ phase: "ready", data, error: null });
    }).catch((error: unknown) => {
      if (nextController.signal.aborted || requestId.current !== id) return;
      const message = error instanceof Error ? error.message : "로그·진행 상태를 불러오지 못했습니다.";
      setState((current) => ({ phase: "error", data: current.data, error: message }));
    });
  }, [demo, loader]);

  useEffect(() => {
    refresh();
    return () => controller.current?.abort();
  }, [refresh]);

  return { state, refresh };
}
