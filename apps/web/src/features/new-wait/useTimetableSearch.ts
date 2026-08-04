import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import { ApiError } from "../../api/client";
import type { RailProvider } from "../../api/providerAccounts";
import type { NewWaitForm, NewWaitWeekday } from "./newWaitForm";
import { buildTimetableQueryKey } from "./timetableQueryKey";
import { reconcileTrainSnapshots } from "./trainSnapshots";

export interface TimetableTrainSnapshot {
  id: string;
  provider: RailProvider;
  departure_at: string;
}

export interface TimetableProviderSuccess {
  status: "success";
  provider?: RailProvider;
  count?: number;
}

export interface TimetableProviderError {
  status: "error";
  provider: RailProvider;
  message: string;
  httpStatus?: number;
  code?: string;
  retryAfterSeconds?: number | null;
}

export type TimetableProviderResult = TimetableProviderSuccess | TimetableProviderError;
export type TimetableProviderResults = Partial<Record<RailProvider, TimetableProviderResult>>;

export interface TimetableSearchState {
  loadingProviders: RailProvider[];
  providerResults: TimetableProviderResults;
}

export interface TimetableSearchForm {
  provider: RailProvider | "";
  providers: RailProvider[];
  origin: string;
  origin_node_id: string | null;
  destination: string;
  destination_node_id: string | null;
  date: string;
  time: string;
  timeEnd: string;
  selectedWeekdays: NewWaitWeekday[];
  passengers: string;
}

export interface TimetableRequestForm extends TimetableSearchForm {
  timeFrom: string;
  timeTo: string;
}

export interface TimetableSearchResult<TTrain extends TimetableTrainSnapshot> {
  trains: TTrain[];
  providerResults: TimetableProviderResults;
}

interface UseTimetableSearchOptions<TTrain extends TimetableTrainSnapshot> {
  active: boolean;
  demo: boolean;
  form: NewWaitForm;
  loadTimetables: (
    form: TimetableRequestForm,
    providerOverride?: RailProvider,
  ) => Promise<TimetableSearchResult<TTrain>>;
  loadSeatStatus: (form: TimetableRequestForm, provider: RailProvider) => Promise<TTrain[]>;
  loadCachedSnapshot: (
    form: TimetableSearchForm,
    provider: RailProvider,
  ) => Promise<ReadonlyArray<unknown> | null>;
  loadDemoTimetables: (form: TimetableSearchForm, provider?: RailProvider | null) => TTrain[];
  filterTimetables: (form: TimetableRequestForm, items: TTrain[]) => TTrain[];
  mapTimetable: (item: unknown) => TTrain;
}

export interface TimetableSearchController<TTrain extends TimetableTrainSnapshot> {
  trains: TTrain[];
  state: TimetableSearchState;
  retryProvider: (provider: RailProvider) => Promise<void>;
  refreshProviderSeatStatus: (
    provider: RailProvider,
    requestForm?: NewWaitForm,
  ) => Promise<TTrain[]>;
  retrySeatStatusProviders: (providers: RailProvider[]) => Promise<TTrain[][]>;
  refreshAll: () => Promise<void>;
  synchronizeCached: () => Promise<void>;
}

const officialTimetableError = "공식 시간표를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.";
const seatStatusError = "좌석 상태를 다시 확인하지 못했습니다. 잠시 후 다시 시도해 주세요.";

function requestForm(form: TimetableSearchForm): TimetableRequestForm {
  return {
    ...form,
    origin: form.origin.trim(),
    destination: form.destination.trim(),
    timeFrom: form.time,
    timeTo: form.timeEnd,
  };
}

function loadingWithProvider(
  loadingProviders: RailProvider[],
  provider: RailProvider,
): RailProvider[] {
  return loadingProviders.includes(provider)
    ? loadingProviders
    : [...loadingProviders, provider];
}

function providerSuccess(
  provider: RailProvider,
  count?: number,
): TimetableProviderSuccess {
  return count === undefined
    ? { status: "success", provider }
    : { status: "success", provider, count };
}

function providerFailure(provider: RailProvider, message: string): TimetableProviderError {
  return { status: "error", provider, message };
}

function resultsForProviders(
  providers: RailProvider[],
  result: (provider: RailProvider) => TimetableProviderResult,
): TimetableProviderResults {
  return Object.fromEntries(providers.map((provider) => [provider, result(provider)]));
}

function sortedReplacingProvider<TTrain extends TimetableTrainSnapshot>(
  trains: TTrain[],
  provider: RailProvider,
  replacements: TTrain[],
): TTrain[] {
  return [...trains.filter((train) => train.provider !== provider), ...replacements]
    .sort((left, right) => Date.parse(left.departure_at) - Date.parse(right.departure_at));
}

export function useTimetableSearch<TTrain extends TimetableTrainSnapshot>({
  active,
  demo,
  form,
  loadTimetables,
  loadSeatStatus,
  loadCachedSnapshot,
  loadDemoTimetables,
  filterTimetables,
  mapTimetable,
}: UseTimetableSearchOptions<TTrain>): TimetableSearchController<TTrain> {
  const [trains, setTrains] = useState<TTrain[]>([]);
  const [state, setState] = useState<TimetableSearchState>({
    loadingProviders: [],
    providerResults: {},
  });
  const queryForm = useMemo<TimetableSearchForm>(() => ({
    provider: form.provider,
    providers: form.providers,
    origin: form.origin,
    origin_node_id: form.origin_node_id,
    destination: form.destination,
    destination_node_id: form.destination_node_id,
    date: form.date,
    time: form.time,
    timeEnd: form.timeEnd,
    selectedWeekdays: form.selectedWeekdays,
    passengers: form.passengers,
  }), [
    form.date,
    form.destination,
    form.destination_node_id,
    form.origin,
    form.origin_node_id,
    form.passengers,
    form.provider,
    form.providers,
    form.selectedWeekdays,
    form.time,
    form.timeEnd,
  ]);
  const queryKey = buildTimetableQueryKey(queryForm);
  const queryKeyRef = useRef(queryKey);

  useLayoutEffect(() => {
    queryKeyRef.current = queryKey;
  }, [queryKey]);

  useEffect(() => {
    if (!active) return undefined;
    let current = true;
    const requestedQueryKey = queryKey;
    const providers = [...queryForm.providers];
    const requestedForm = requestForm(queryForm);
    // Starting in a microtask keeps effect setup limited to synchronization and
    // gives cleanup a chance to invalidate a request before it reaches an API boundary.
    void Promise.resolve().then(() => {
      if (!current || queryKeyRef.current !== requestedQueryKey) return;
      setState({ loadingProviders: providers, providerResults: {} });

      if (demo) {
        const items = filterTimetables(requestedForm, loadDemoTimetables(queryForm));
        setTrains((previous) => reconcileTrainSnapshots(previous, items));
        setState({
          loadingProviders: [],
          providerResults: resultsForProviders(providers, (provider) => (
            providerSuccess(provider, items.filter((item) => item.provider === provider).length)
          )),
        });
        return;
      }

      setTrains([]);
      void loadTimetables(requestedForm).then((result) => {
        if (!current || queryKeyRef.current !== requestedQueryKey) return;
        setTrains((previous) => reconcileTrainSnapshots(previous, result.trains));
        setState({ loadingProviders: [], providerResults: result.providerResults });
      }).catch((error: unknown) => {
        if (!current || queryKeyRef.current !== requestedQueryKey) return;
        const prefix = error instanceof ApiError && error.status === 503
          ? "공식 시간표 제공자가 응답하지 않습니다."
          : "공식 시간표를 불러오지 못했습니다.";
        setState({
          loadingProviders: [],
          providerResults: resultsForProviders(
            providers,
            (provider) => providerFailure(provider, `${prefix} 잠시 후 다시 시도해 주세요.`),
          ),
        });
      });
    });

    return () => {
      current = false;
    };
  }, [active, demo, filterTimetables, loadDemoTimetables, loadTimetables, queryForm, queryKey]);

  const retryProvider = useCallback(async (provider: RailProvider): Promise<void> => {
    const requestedQueryKey = queryKeyRef.current;
    const requestedForm = requestForm(queryForm);
    setState((value) => ({
      ...value,
      loadingProviders: loadingWithProvider(value.loadingProviders, provider),
    }));

    if (demo) {
      const items = filterTimetables(
        { ...requestedForm, providers: [provider], provider },
        loadDemoTimetables(queryForm, provider),
      );
      if (queryKeyRef.current !== requestedQueryKey) return;
      setTrains((value) => reconcileTrainSnapshots(
        value,
        sortedReplacingProvider(value, provider, items),
      ));
      setState((value) => ({
        loadingProviders: value.loadingProviders.filter((item) => item !== provider),
        providerResults: {
          ...value.providerResults,
          [provider]: providerSuccess(provider, items.length),
        },
      }));
      return;
    }

    try {
      const result = await loadTimetables(requestedForm, provider);
      if (queryKeyRef.current !== requestedQueryKey) return;
      setTrains((value) => reconcileTrainSnapshots(
        value,
        sortedReplacingProvider(value, provider, result.trains),
      ));
      setState((value) => ({
        loadingProviders: value.loadingProviders.filter((item) => item !== provider),
        providerResults: { ...value.providerResults, ...result.providerResults },
      }));
    } catch {
      if (queryKeyRef.current !== requestedQueryKey) return;
      setState((value) => ({
        loadingProviders: value.loadingProviders.filter((item) => item !== provider),
        providerResults: {
          ...value.providerResults,
          [provider]: providerFailure(provider, officialTimetableError),
        },
      }));
    }
  }, [demo, filterTimetables, loadDemoTimetables, loadTimetables, queryForm]);

  const refreshProviderSeatStatus = useCallback(async (
    provider: RailProvider,
    requestFormValue: NewWaitForm = form,
  ): Promise<TTrain[]> => {
    const requestedQueryKey = buildTimetableQueryKey(requestFormValue);
    setState((value) => ({
      ...value,
      loadingProviders: loadingWithProvider(value.loadingProviders, provider),
    }));
    try {
      const items = await loadSeatStatus(requestForm(requestFormValue), provider);
      if (queryKeyRef.current !== requestedQueryKey) {
        throw new Error("조회 조건이 변경되었습니다.");
      }
      setTrains((value) => reconcileTrainSnapshots(
        value,
        sortedReplacingProvider(value, provider, items),
      ));
      setState((value) => ({
        loadingProviders: value.loadingProviders.filter((item) => item !== provider),
        providerResults: {
          ...value.providerResults,
          [provider]: providerSuccess(provider, items.length),
        },
      }));
      return items;
    } catch (error: unknown) {
      if (queryKeyRef.current === requestedQueryKey) {
        setState((value) => ({
          loadingProviders: value.loadingProviders.filter((item) => item !== provider),
          providerResults: {
            ...value.providerResults,
            [provider]: providerFailure(provider, seatStatusError),
          },
        }));
      }
      throw error;
    }
  }, [form, loadSeatStatus]);

  const retrySeatStatusProviders = useCallback(async (
    providers: RailProvider[],
  ): Promise<TTrain[][]> => Promise.all(
    providers.map((provider) => refreshProviderSeatStatus(provider)),
  ), [refreshProviderSeatStatus]);

  const refreshAll = useCallback(async (): Promise<void> => {
    const requestedQueryKey = queryKeyRef.current;
    const requestedForm = requestForm(queryForm);
    const providers = [...queryForm.providers];
    setState((value) => ({ ...value, loadingProviders: providers }));
    try {
      const result = demo
        ? {
          trains: filterTimetables(requestedForm, loadDemoTimetables(queryForm)),
          providerResults: resultsForProviders(providers, providerSuccess),
        }
        : await loadTimetables(requestedForm);
      if (queryKeyRef.current !== requestedQueryKey) return;
      setTrains((previous) => reconcileTrainSnapshots(previous, result.trains));
      setState({ loadingProviders: [], providerResults: result.providerResults });
    } catch (error: unknown) {
      if (queryKeyRef.current !== requestedQueryKey) return;
      setState((value) => ({
        loadingProviders: [],
        providerResults: resultsForProviders(
          providers,
          (provider) => value.providerResults[provider]
            ?? providerFailure(provider, officialTimetableError),
        ),
      }));
      throw error;
    }
  }, [demo, filterTimetables, loadDemoTimetables, loadTimetables, queryForm]);

  const synchronizeCached = useCallback(async (): Promise<void> => {
    if (demo) {
      const items = filterTimetables(requestForm(queryForm), loadDemoTimetables(queryForm));
      setTrains((previous) => reconcileTrainSnapshots(previous, items));
      return;
    }
    const requestedQueryKey = queryKeyRef.current;
    const snapshots = await Promise.all(queryForm.providers.map(async (provider) => ({
      provider,
      items: await loadCachedSnapshot(queryForm, provider),
    })));
    if (queryKeyRef.current !== requestedQueryKey) return;
    setTrains((previous) => {
      const providersWithSnapshots = new Set(
        snapshots.filter(({ items }) => items !== null).map(({ provider }) => provider),
      );
      const incoming = [
        ...previous.filter((train) => !providersWithSnapshots.has(train.provider)),
        ...snapshots.flatMap(({ items }) => items === null
          ? []
          : filterTimetables(
            requestForm(queryForm),
            items.map((item) => mapTimetable(item)),
          )),
      ].sort((left, right) => Date.parse(left.departure_at) - Date.parse(right.departure_at));
      return reconcileTrainSnapshots(previous, incoming);
    });
  }, [demo, filterTimetables, loadCachedSnapshot, loadDemoTimetables, mapTimetable, queryForm]);

  return {
    trains,
    state,
    retryProvider,
    refreshProviderSeatStatus,
    retrySeatStatusProviders,
    refreshAll,
    synchronizeCached,
  };
}
