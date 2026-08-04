import { useCallback, useEffect, useRef, useState } from "react";
import { fetchSeatStatusSources } from "../../api/seatStatusSources";
import type { SeatStatusSource } from "../../api/seatStatusSourcesContract";
import { demoSeatStatusSources } from "../../fixtures/seatStatusSources";

export type SeatStatusSourcesState =
  | { phase: "loading"; data: null; error: null }
  | { phase: "ready"; data: SeatStatusSource[]; error: null }
  | { phase: "refreshing"; data: SeatStatusSource[]; error: null }
  | { phase: "error"; data: SeatStatusSource[] | null; error: string };

export type SeatStatusSourcesLoader = (signal?: AbortSignal) => Promise<SeatStatusSource[]>;

interface UseSeatStatusSourcesOptions {
  demo: boolean;
  enabled: boolean;
  loader?: SeatStatusSourcesLoader;
}

interface UseSeatStatusSourcesResult {
  state: SeatStatusSourcesState;
  refresh: () => void;
}

export function useSeatStatusSources({
  demo,
  enabled,
  loader = fetchSeatStatusSources,
}: UseSeatStatusSourcesOptions): UseSeatStatusSourcesResult {
  const [state, setState] = useState<SeatStatusSourcesState>(() => demo
    ? { phase: "ready", data: demoSeatStatusSources, error: null }
    : { phase: "loading", data: null, error: null });
  const requestId = useRef(0);
  const controller = useRef<AbortController | null>(null);

  const refresh = useCallback(() => {
    if (demo) {
      setState({ phase: "ready", data: demoSeatStatusSources, error: null });
      return;
    }
    if (!enabled) {
      controller.current?.abort();
      setState({ phase: "ready", data: [], error: null });
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
      const message = error instanceof Error ? error.message : "좌석 조회 제공원 상태를 불러오지 못했습니다.";
      setState((current) => ({ phase: "error", data: current.data, error: message }));
    });
  }, [demo, enabled, loader]);

  useEffect(() => {
    refresh();
    return () => controller.current?.abort();
  }, [refresh]);

  return { state, refresh };
}
