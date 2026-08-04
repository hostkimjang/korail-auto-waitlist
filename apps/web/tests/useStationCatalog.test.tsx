import { useState } from "react";
import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { RailProvider } from "../src/api/providerAccounts";
import type { NewWaitForm } from "../src/features/new-wait/newWaitForm";
import {
  reconcileNewWaitStations,
  useStationCatalog,
  type StationCatalogResult,
  type StationCatalogStation,
} from "../src/features/new-wait/useStationCatalog";

const station = (name: string, nodeId: string): StationCatalogStation => ({ name, nodeId });

const initialForm = (overrides: Partial<NewWaitForm> = {}): NewWaitForm => ({
  provider: "KORAIL",
  providers: ["KORAIL"],
  origin: "서울",
  origin_node_id: "SEOUL",
  destination: "부산",
  destination_node_id: "BUSAN",
  date: "2026-08-05",
  time: "12:00",
  timeEnd: "18:00",
  selectedWeekdays: ["수"],
  passengers: "1",
  seat: "일반실",
  channels: ["web_push"],
  reservationPolicy: "notify_only",
  ...overrides,
});

type HarnessProps = {
  demo: boolean;
  providers: RailProvider[];
  loadStations: (providers: RailProvider[]) => Promise<StationCatalogResult>;
  loadDemoStations: (providers: RailProvider[]) => StationCatalogStation[];
};

function useHarness(props: HarnessProps) {
  const [form, setForm] = useState(() => initialForm({ providers: props.providers }));
  const catalog = useStationCatalog({ ...props, setForm });
  return { catalog, form };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

describe("NewWait station catalog hook", () => {
  it("keeps station names and node ids as one atomic selection", () => {
    const reconciled = reconcileNewWaitStations(
      initialForm({ destination_node_id: "WRONG" }),
      [station("서울", "SEOUL"), station("부산", "BUSAN")],
    );

    expect(reconciled).toMatchObject({
      origin: "서울",
      origin_node_id: "SEOUL",
      destination: "",
      destination_node_id: null,
    });
  });

  it("uses the demo catalog without calling the live boundary", async () => {
    const loadStations = vi.fn<HarnessProps["loadStations"]>();
    const loadDemoStations = vi.fn(() => [station("서울", "SEOUL")]);
    const { result } = renderHook(() => useHarness({
      demo: true,
      providers: ["KORAIL"],
      loadStations,
      loadDemoStations,
    }));

    expect(result.current.catalog.state).toMatchObject({
      status: "ready",
      providerKey: "KORAIL",
      source: "mock",
    });
    await waitFor(() => expect(result.current.form.destination_node_id).toBeNull());
    expect(loadStations).not.toHaveBeenCalled();
  });

  it("ignores a stale provider response after the selected provider changes", async () => {
    const korail = deferred<StationCatalogResult>();
    const srt = deferred<StationCatalogResult>();
    const loadStations = vi.fn((providers: RailProvider[]) => (
      providers[0] === "SRT" ? srt.promise : korail.promise
    ));
    const props = {
      demo: false,
      providers: ["KORAIL"] as RailProvider[],
      loadStations,
      loadDemoStations: vi.fn(() => []),
    };
    const { result, rerender } = renderHook(
      (currentProps: HarnessProps) => useHarness(currentProps),
      { initialProps: props },
    );
    await waitFor(() => expect(loadStations).toHaveBeenCalledWith(["KORAIL"]));

    rerender({ ...props, providers: ["SRT"] });
    await waitFor(() => expect(loadStations).toHaveBeenCalledWith(["SRT"]));
    await act(async () => {
      srt.resolve({
        stations: [station("수서", "SUSEO")],
        catalogs: [{ source: "TAGO" }],
      });
    });
    await waitFor(() => expect(result.current.catalog.state).toMatchObject({
      status: "ready",
      providerKey: "SRT",
      source: "official",
    }));

    await act(async () => {
      korail.resolve({
        stations: [station("서울", "SEOUL")],
        catalogs: [{ source: "TAGO" }],
      });
    });
    expect(result.current.catalog.state.providerKey).toBe("SRT");
    expect(result.current.catalog.stations).toEqual([station("수서", "SUSEO")]);
  });

  it("fails closed and retries the same provider catalog", async () => {
    const loadStations = vi
      .fn<HarnessProps["loadStations"]>()
      .mockRejectedValueOnce(new Error("unavailable"))
      .mockResolvedValueOnce({
        stations: [station("서울", "SEOUL"), station("부산", "BUSAN")],
        catalogs: [{ source: "TAGO" }],
      });
    const loadDemoStations = vi.fn(() => []);
    const { result } = renderHook(() => useHarness({
      demo: false,
      providers: ["KORAIL"],
      loadStations,
      loadDemoStations,
    }));

    await waitFor(() => expect(result.current.catalog.state.status).toBe("error"));
    expect(result.current.form).toMatchObject({
      origin: "",
      origin_node_id: null,
      destination: "",
      destination_node_id: null,
    });

    act(() => result.current.catalog.retry());
    await waitFor(() => expect(result.current.catalog.state.status).toBe("ready"));
    expect(loadStations).toHaveBeenCalledTimes(2);
  });

  it("moves to idle and clears both selections when no provider remains", async () => {
    const loadStations = vi.fn<HarnessProps["loadStations"]>();
    const loadDemoStations = vi.fn(() => []);
    const { result } = renderHook(() => useHarness({
      demo: false,
      providers: [],
      loadStations,
      loadDemoStations,
    }));

    expect(result.current.catalog.state.status).toBe("idle");
    await waitFor(() => expect(result.current.form.origin_node_id).toBeNull());
    expect(result.current.form.destination_node_id).toBeNull();
  });
});
