import { ApiError, request } from "./client";
import type { RailProvider } from "./providerAccounts";

type StationCatalogSource = "TAGO" | "mock";
type StationCatalogScope = "intercity_station_guide_intersection" | "mock";
type ProviderMembership = "not_verified_by_source" | "mock";

export interface StationCatalogStation {
  name: string;
  nodeId: string;
  cityCode: string;
  cityName: string;
}

export interface StationCatalog {
  provider: RailProvider;
  source: StationCatalogSource;
  catalogScope: StationCatalogScope;
  providerMembership: ProviderMembership;
  note: string;
  retrievedAt: string;
  stations: StationCatalogStation[];
}

export interface MergedStation extends StationCatalogStation {
  catalogProviders: RailProvider[];
  sources: StationCatalogSource[];
  providerMembershipVerified: false;
}

export interface StationCatalogResult {
  stations: MergedStation[];
  catalogs: StationCatalog[];
  providerMembershipVerified: boolean;
}

const SUPPORTED_PROVIDERS: ReadonlySet<string> = new Set(["KORAIL", "SRT"]);
const ALLOWED_CATALOG_TUPLES: ReadonlySet<string> = new Set([
  "TAGO|intercity_station_guide_intersection|not_verified_by_source",
  "mock|mock|mock",
]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function normalizedStationCatalog(payload: unknown, requestedProvider: RailProvider): StationCatalog {
  if (!isRecord(payload)) {
    throw new ApiError(`${requestedProvider} 역 목록 응답 형식을 확인할 수 없습니다.`);
  }
  const provider = String(payload.provider ?? "").toUpperCase();
  const source = String(payload.source ?? "").trim();
  const catalogScope = String(payload.catalog_scope ?? "").trim();
  const providerMembership = String(payload.provider_membership ?? "").trim();
  const note = String(payload.note ?? "").trim();
  const retrievedAt = String(payload.retrieved_at ?? "");
  const catalogTuple = `${source}|${catalogScope}|${providerMembership}`;
  if (
    provider !== requestedProvider
    || !ALLOWED_CATALOG_TUPLES.has(catalogTuple)
    || (Object.hasOwn(payload, "note") && !note)
    || !retrievedAt
    || Number.isNaN(new Date(retrievedAt).getTime())
    || !Array.isArray(payload.stations)
    || payload.stations.length === 0
  ) {
    throw new ApiError(`${requestedProvider} 역 목록 응답 형식을 확인할 수 없습니다.`);
  }

  const stations = payload.stations.map((value): StationCatalogStation => {
    if (!isRecord(value)) {
      throw new ApiError(`${requestedProvider} 역 목록에 불완전한 항목이 있습니다.`);
    }
    const name = String(value.name ?? "").trim();
    const nodeId = String(value.node_id ?? "").trim();
    const cityCode = String(value.city_code ?? "").trim();
    const cityName = String(value.city_name ?? "").trim();
    if (!name || !nodeId || !cityCode || !cityName) {
      throw new ApiError(`${requestedProvider} 역 목록에 불완전한 항목이 있습니다.`);
    }
    return { name, nodeId, cityCode, cityName };
  });

  return {
    provider: requestedProvider,
    source: source as StationCatalogSource,
    catalogScope: catalogScope as StationCatalogScope,
    providerMembership: providerMembership as ProviderMembership,
    note,
    retrievedAt,
    stations,
  };
}

export function mergeStationCatalogs(catalogs: readonly StationCatalog[]): MergedStation[] {
  const merged = new Map<string, MergedStation>();
  for (const catalog of catalogs) {
    for (const station of catalog.stations) {
      const existing = merged.get(station.nodeId);
      if (existing) {
        if (existing.name !== station.name || existing.cityCode !== station.cityCode) {
          throw new ApiError(`역 식별자 ${station.nodeId}의 정보가 응답마다 다릅니다.`);
        }
        if (!existing.catalogProviders.includes(catalog.provider)) {
          existing.catalogProviders.push(catalog.provider);
        }
        if (!existing.sources.includes(catalog.source)) existing.sources.push(catalog.source);
        continue;
      }
      merged.set(station.nodeId, {
        ...station,
        catalogProviders: [catalog.provider],
        sources: [catalog.source],
        providerMembershipVerified: false,
      });
    }
  }
  return [...merged.values()].sort((left, right) => left.name.localeCompare(right.name, "ko-KR"));
}

function selectedProviders(providerValues: RailProvider | readonly RailProvider[]): RailProvider[] {
  const values = Array.isArray(providerValues) ? providerValues : [providerValues];
  const providers = [...new Set(values.map((value) => String(value).toUpperCase()))];
  if (!providers.length || providers.some((provider) => !SUPPORTED_PROVIDERS.has(provider))) {
    throw new ApiError("역 목록을 받을 KORAIL 또는 SRT 운영사를 선택해 주세요.");
  }
  return providers as RailProvider[];
}

export async function fetchStations(
  providerValues: RailProvider | readonly RailProvider[],
): Promise<StationCatalogResult> {
  const selected = selectedProviders(providerValues);
  const providers = selected.includes("SRT")
    ? [...new Set<RailProvider>(["KORAIL", ...selected])]
    : selected;

  // 교차 운행 구간을 놓치지 않도록 카탈로그를 합치며, 일부 실패도 내장 목록으로 숨기지 않는다.
  const catalogs = await Promise.all(providers.map(async (provider) => {
    const payload = await request(`/stations?${new URLSearchParams({
      provider: provider.toLowerCase(),
    })}`);
    return normalizedStationCatalog(payload, provider);
  }));
  return {
    stations: mergeStationCatalogs(catalogs),
    catalogs,
    providerMembershipVerified: catalogs.every((catalog) => catalog.providerMembership === "mock"),
  };
}
