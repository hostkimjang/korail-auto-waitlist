import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type Dispatch,
  type SetStateAction,
} from "react";

import type { RailProvider } from "../../api/providerAccounts";
import type { StationCatalogOrigin } from "../../api/stations";
import type { StationComboboxStation } from "./StationCombobox";
import type { NewWaitForm } from "./newWaitForm";

const STATION_CATALOG_ERROR =
  "공식 역 목록이 일시적으로 응답하지 않습니다. 잠시 후 다시 시도해 주세요.";

export interface StationCatalogStation extends StationComboboxStation {
  nodeId: string;
}

export interface StationCatalogMetadata {
  source: string;
  catalogOrigin: StationCatalogOrigin;
  retrievedAt: string;
}

export interface StationCatalogResult {
  stations: StationCatalogStation[];
  catalogs: StationCatalogMetadata[];
}

export type StationCatalogState = {
  status: "idle" | "loading" | "ready" | "error";
  providerKey: string;
  stations: StationCatalogStation[];
  source: "" | "mock" | "official" | "bundled_snapshot";
  /** 내장 스냅샷을 받은 경우 그 원래 수집 시각(가장 오래된 값), 아니면 빈 문자열. */
  bundledSnapshotRetrievedAt: string;
  error: string;
};

export interface UseStationCatalogOptions {
  demo: boolean;
  providers: ReadonlyArray<RailProvider>;
  loadStations: (providers: RailProvider[]) => Promise<StationCatalogResult>;
  loadDemoStations: (providers: RailProvider[]) => StationCatalogStation[];
  setForm: Dispatch<SetStateAction<NewWaitForm>>;
}

export interface UseStationCatalogResult {
  state: StationCatalogState;
  providerKey: string;
  ready: boolean;
  stations: StationCatalogStation[];
  hasStation: (name: string, nodeId: string | null) => boolean;
  retry: () => void;
}

function providerKey(providers: ReadonlyArray<RailProvider>): string {
  return [...providers].sort().join(",");
}

function catalogSource(
  catalogs: ReadonlyArray<StationCatalogMetadata>,
): Exclude<StationCatalogState["source"], ""> {
  if (catalogs.every((catalog) => catalog.source === "mock")) return "mock";
  return catalogs.some((catalog) => catalog.catalogOrigin === "bundled_snapshot")
    ? "bundled_snapshot"
    : "official";
}

function oldestBundledSnapshotRetrievedAt(
  catalogs: ReadonlyArray<StationCatalogMetadata>,
): string {
  let oldest: { value: string; time: number } | null = null;
  for (const catalog of catalogs) {
    if (catalog.catalogOrigin !== "bundled_snapshot") continue;
    const time = new Date(catalog.retrievedAt).getTime();
    if (Number.isNaN(time)) continue;
    if (oldest === null || time < oldest.time) oldest = { value: catalog.retrievedAt, time };
  }
  return oldest?.value ?? "";
}

function providersFromKey(key: string): RailProvider[] {
  return key
    .split(",")
    .filter((provider): provider is RailProvider => provider === "KORAIL" || provider === "SRT");
}

function stationExists(
  stations: ReadonlyArray<StationCatalogStation>,
  name: string,
  nodeId: string | null,
): boolean {
  return stations.some((station) => station.name === name && station.nodeId === nodeId);
}

export function reconcileNewWaitStations(
  form: NewWaitForm,
  stations: ReadonlyArray<StationCatalogStation>,
): NewWaitForm {
  const originValid = stationExists(stations, form.origin, form.origin_node_id);
  const destinationValid = stationExists(
    stations,
    form.destination,
    form.destination_node_id,
  );
  return {
    ...form,
    origin: originValid ? form.origin : "",
    origin_node_id: originValid ? form.origin_node_id : null,
    destination: destinationValid ? form.destination : "",
    destination_node_id: destinationValid ? form.destination_node_id : null,
  };
}

function initialState(
  demo: boolean,
  key: string,
  loadDemoStations: UseStationCatalogOptions["loadDemoStations"],
): StationCatalogState {
  if (!key) {
    return {
      status: "idle",
      providerKey: "",
      stations: [],
      source: "",
      bundledSnapshotRetrievedAt: "",
      error: "",
    };
  }
  if (demo) {
    return {
      status: "ready",
      providerKey: key,
      stations: loadDemoStations(providersFromKey(key)),
      source: "mock",
      bundledSnapshotRetrievedAt: "",
      error: "",
    };
  }
  return {
    status: "loading",
    providerKey: key,
    stations: [],
    source: "",
    bundledSnapshotRetrievedAt: "",
    error: "",
  };
}

export function useStationCatalog({
  demo,
  providers,
  loadStations,
  loadDemoStations,
  setForm,
}: UseStationCatalogOptions): UseStationCatalogResult {
  const key = providerKey(providers);
  const [reloadVersion, setReloadVersion] = useState(0);
  const [state, setState] = useState<StationCatalogState>(() => (
    initialState(demo, key, loadDemoStations)
  ));

  useEffect(() => {
    let active = true;
    const selectedProviders = providersFromKey(key);

    if (selectedProviders.length === 0) {
      queueMicrotask(() => {
        if (!active) return;
        setState({
          status: "idle",
          providerKey: "",
          stations: [],
          source: "",
          bundledSnapshotRetrievedAt: "",
          error: "",
        });
        setForm((form) => reconcileNewWaitStations(form, []));
      });
      return () => {
        active = false;
      };
    }

    if (demo) {
      const stations = loadDemoStations(selectedProviders);
      queueMicrotask(() => {
        if (!active) return;
        setState({
          status: "ready",
          providerKey: key,
          stations,
          source: "mock",
          bundledSnapshotRetrievedAt: "",
          error: "",
        });
        setForm((form) => reconcileNewWaitStations(form, stations));
      });
      return () => {
        active = false;
      };
    }

    queueMicrotask(() => {
      if (!active) return;
      setState({
        status: "loading",
        providerKey: key,
        stations: [],
        source: "",
        bundledSnapshotRetrievedAt: "",
        error: "",
      });
    });
    void Promise.resolve()
      .then(() => loadStations(selectedProviders))
      .then(({ stations, catalogs }) => {
        if (!active) return;
        setState({
          status: "ready",
          providerKey: key,
          stations,
          source: catalogSource(catalogs),
          bundledSnapshotRetrievedAt: oldestBundledSnapshotRetrievedAt(catalogs),
          error: "",
        });
        setForm((form) => reconcileNewWaitStations(form, stations));
      })
      .catch(() => {
        if (!active) return;
        setState({
          status: "error",
          providerKey: key,
          stations: [],
          source: "",
          bundledSnapshotRetrievedAt: "",
          error: STATION_CATALOG_ERROR,
        });
        setForm((form) => reconcileNewWaitStations(form, []));
      });

    return () => {
      active = false;
    };
  }, [demo, key, loadDemoStations, loadStations, reloadVersion, setForm]);

  const ready = state.status === "ready" && state.providerKey === key;
  const stations = useMemo(() => ready ? state.stations : [], [ready, state.stations]);
  const hasStation = useCallback(
    (name: string, nodeId: string | null) => stationExists(stations, name, nodeId),
    [stations],
  );
  const retry = useCallback(() => setReloadVersion((value) => value + 1), []);

  return { state, providerKey: key, ready, stations, hasStation, retry };
}
