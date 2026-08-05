import type {
  MappedWatch,
  MappedWatchCandidate,
} from "../api/watches";
import type { ProviderAccount } from "../api/providerAccounts";
import type { ProviderRuntimeStatus } from "../api/providerRuntime";
import { mapTimetable, type Timetable } from "../api/timetables";
import type { ReservationPolicy } from "../domain/reservationPolicy";
import type { WatchProvider, WatchSeatClass, WatchStatus } from "../domain/watch";

type DemoRailProvider = "KORAIL" | "SRT";
type DemoSeatClassId = "standard" | "first";
type DemoSeatStatus =
  | "available"
  | "limited"
  | "sold_out"
  | "standing_plus_seat"
  | "waitlist_available";

interface DemoTimetableForm {
  providers: DemoRailProvider[];
  origin: string;
  destination: string;
  date: string;
  time: string;
  timeEnd: string;
}

interface DemoSeatClass {
  seat_class: DemoSeatClassId;
  status: DemoSeatStatus;
  fare: number;
  fare_currency: "KRW";
  provenance: {
    kind: "mock";
    source: string;
    observed_at: string;
    reason: null;
  };
  actions: Array<{
    kind: "official_check" | "official_waitlist" | "add_to_watch";
    url: string | null;
  }>;
}

interface DemoTimetable {
  id: string;
  provider: DemoRailProvider;
  train_number: string;
  train_type: string;
  name: string;
  origin: string;
  destination: string;
  departure_at: string;
  arrival_at: string;
  departure: string;
  arrival: string;
  duration: string;
  adult_fare: number;
  fare_currency: "KRW";
  timetable_source: "mock";
  timetable_retrieved_at: string;
  availability: {
    status: DemoSeatStatus;
    source: "mock";
    observed_at: string;
  };
  seat_classes: DemoSeatClass[];
  official_booking_url: string;
}

interface DemoStation {
  name: string;
  nodeId: string;
  cityName: string;
  cityCode: string;
  catalogProviders: DemoRailProvider[];
  sources: ["mock"];
  providerMembershipVerified: false;
}

interface DemoWatchCandidateInput extends Omit<MappedWatchCandidate, "id"> {
  id?: string;
}

export interface DemoWatchInput {
  id: string;
  provider: WatchProvider;
  train: string;
  route: string;
  origin: string;
  destination: string;
  departure: string;
  arrival: string;
  date: string;
  travelDate: string;
  status: WatchStatus;
  statusLabel: string;
  seatClass: WatchSeatClass;
  seatClassLabel: string;
  seatEvidenceLabel: string;
  officialBookingUrl?: string | null;
  reservationPolicy?: ReservationPolicy;
  candidates?: ReadonlyArray<DemoWatchCandidateInput>;
}

export function createDemoWatch(input: DemoWatchInput): MappedWatch {
  const reservationPolicy = input.reservationPolicy ?? "notify_only";
  const candidates = (input.candidates ?? []).map((candidate, index): MappedWatchCandidate => ({
    ...candidate,
    id: candidate.id ?? `${input.id}:candidate:${index + 1}`,
  }));
  const reservationCandidateContexts = Object.fromEntries(candidates.map((candidate) => [
    candidate.id,
    {
      train: candidate.train_number,
      seatClassLabel: input.seatClassLabel,
      date: input.date,
      departure: candidate.departure_at.slice(11, 16),
      arrival: candidate.arrival_at?.slice(11, 16) ?? input.arrival,
    },
  ]));

  return {
    id: input.id,
    provider: input.provider,
    status: input.status,
    candidates,
    payment_deadline: null,
    created_at: null,
    updated_at: null,
    official_booking_url: input.officialBookingUrl ?? null,
    reservation_policy: reservationPolicy,
    train: input.train,
    route: input.route,
    departure: input.departure,
    arrival: input.arrival,
    date: input.date,
    statusLabel: input.statusLabel,
    seatClass: input.seatClass,
    seatClassLabel: input.seatClassLabel,
    seatEvidenceLabel: input.seatEvidenceLabel,
    registrationEvidenceLabel: input.seatEvidenceLabel,
    activityLabel: input.seatEvidenceLabel,
    lastCheckedAt: null,
    lastCheckedLabel: "최근 확인 기록 없음",
    origin: input.origin,
    destination: input.destination,
    travelDate: input.travelDate,
    officialBookingUrl: input.officialBookingUrl ?? null,
    operational: null,
    latestReservationAttempt: null,
    seatFoundObservation: null,
    reservationCandidateContexts,
    reservationPolicy,
    seatObservationMode: "balanced",
    focusedObservationIntervalSeconds: 25,
    nextCheckAt: null,
  };
}

const demoProviders: DemoRailProvider[] = ["KORAIL", "SRT"];

export const demoProviderAccounts: ProviderAccount[] = demoProviders.map((provider) => ({
  provider,
  configured: true,
  enabled: true,
  loginMethod: null,
  maskedLoginId: "de***",
  credentialVersion: 1,
  lastAuthStatus: "authenticated",
  lastAuthenticatedAt: new Date().toISOString(),
  updatedAt: new Date().toISOString(),
}));

export const demoProviderRuntimeStatuses: ProviderRuntimeStatus[] = demoProviders.map((provider) => ({
  provider,
  state: "cold",
  credentialGeneration: null,
  createdAgeSeconds: null,
  lastVerifiedAgeSeconds: null,
  lastUsedAgeSeconds: null,
  localReuseRemainingSeconds: null,
  locallyReusable: false,
  prewarmOutcome: null,
}));

export const initialWatches: MappedWatch[] = [
  createDemoWatch({
    id: "watch-ktx-483",
    provider: "KORAIL",
    train: "KTX 483",
    route: "용산 → 광주송정",
    origin: "용산",
    destination: "광주송정",
    departure: "15:20",
    arrival: "17:46",
    date: "7월 31일 (금)",
    travelDate: "2026-07-31",
    status: "watching",
    statusLabel: "감시 중",
    seatClass: "standard",
    seatClassLabel: "일반실",
    seatEvidenceLabel: "일반실 · 예매 가능 · 데모 관측 14:32",
  }),
];

export interface DemoPaymentWatch {
  id: string;
  provider: "SRT";
  train: string;
  route: string;
  origin: string;
  destination: string;
  departure: string;
  arrival: string;
  date: string;
  status: "payment_required";
  statusLabel: string;
  official_booking_url: string;
  payment_deadline: null;
}

export const demoPaymentWatch: DemoPaymentWatch = {
  id: "demo-payment-srt-327",
  provider: "SRT",
  train: "SRT 327",
  route: "수서 → 부산",
  origin: "수서",
  destination: "부산",
  departure: "10:42",
  arrival: "13:14",
  date: "7월 31일 (금)",
  status: "payment_required",
  statusLabel: "결제 필요",
  official_booking_url: "https://etk.srail.kr",
  payment_deadline: null,
};

function demoSeatClass(
  seatClass: DemoSeatClassId,
  status: DemoSeatStatus,
  fare: number,
  officialUrl: string,
): DemoSeatClass {
  return {
    seat_class: seatClass,
    status,
    fare,
    fare_currency: "KRW",
    provenance: {
      kind: "mock",
      source: "정식 앱 UX 벤치마크 데모",
      observed_at: "2026-07-29T05:32:10Z",
      reason: null,
    },
    actions: [
      { kind: status === "waitlist_available" ? "official_waitlist" : "official_check", url: officialUrl },
      { kind: "add_to_watch", url: null },
    ],
  };
}

const demoAvailableTrains: DemoTimetable[] = [
  { id: "KORAIL:KTX 033:2026-07-31T13:18:00+09:00", provider: "KORAIL", train_number: "KTX 033", train_type: "KTX", name: "KTX 033", origin: "서울", destination: "부산", departure_at: "2026-07-31T13:18:00+09:00", arrival_at: "2026-07-31T15:59:00+09:00", departure: "13:18", arrival: "15:59", duration: "2시간 41분", adult_fare: 59800, fare_currency: "KRW", timetable_source: "mock", timetable_retrieved_at: "2026-07-29T05:32:10Z", availability: { status: "available", source: "mock", observed_at: "2026-07-29T05:32:10Z" }, seat_classes: [demoSeatClass("standard", "available", 59800, "https://www.korail.com/ticket/search"), demoSeatClass("first", "available", 83700, "https://www.korail.com/ticket/search")], official_booking_url: "https://www.korail.com/ticket/search" },
  { id: "KORAIL:KTX 085:2026-07-31T14:11:00+09:00", provider: "KORAIL", train_number: "KTX 085", train_type: "KTX", name: "KTX 085", origin: "서울", destination: "부산", departure_at: "2026-07-31T14:11:00+09:00", arrival_at: "2026-07-31T16:52:00+09:00", departure: "14:11", arrival: "16:52", duration: "2시간 41분", adult_fare: 59800, fare_currency: "KRW", timetable_source: "mock", timetable_retrieved_at: "2026-07-29T05:32:10Z", availability: { status: "limited", source: "mock", observed_at: "2026-07-29T05:32:10Z" }, seat_classes: [demoSeatClass("standard", "limited", 59800, "https://www.korail.com/ticket/search"), demoSeatClass("first", "sold_out", 83200, "https://www.korail.com/ticket/search")], official_booking_url: "https://www.korail.com/ticket/search" },
  { id: "SRT:SRT 327:2026-07-31T14:30:00+09:00", provider: "SRT", train_number: "SRT 327", train_type: "SRT", name: "SRT 327", origin: "수서", destination: "부산", departure_at: "2026-07-31T14:30:00+09:00", arrival_at: "2026-07-31T16:58:00+09:00", departure: "14:30", arrival: "16:58", duration: "2시간 28분", adult_fare: 52600, fare_currency: "KRW", timetable_source: "mock", timetable_retrieved_at: "2026-07-29T05:32:10Z", availability: { status: "standing_plus_seat", source: "mock", observed_at: "2026-07-29T05:32:10Z" }, seat_classes: [demoSeatClass("standard", "standing_plus_seat", 52600, "https://etk.srail.kr/hpg/hra/01/selectScheduleList.do?pageId=TK0101010000"), demoSeatClass("first", "waitlist_available", 76200, "https://etk.srail.kr/hpg/hra/01/selectScheduleList.do?pageId=TK0101010000")], official_booking_url: "https://etk.srail.kr/hpg/hra/01/selectScheduleList.do?pageId=TK0101010000" },
];

function demoTime(minutes: number): string {
  return `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;
}

function demoIsoAt(date: string, minutes: number): string {
  const dayOffset = Math.floor(minutes / (24 * 60));
  const localDate = new Date(`${date}T00:00:00+09:00`);
  localDate.setTime(localDate.getTime() + dayOffset * 24 * 60 * 60 * 1000);
  const seoulDate = new Date(localDate.getTime() + 9 * 60 * 60 * 1000).toISOString().slice(0, 10);
  return `${seoulDate}T${demoTime(minutes % (24 * 60))}:00+09:00`;
}

export function demoTimetablesForForm(
  form: DemoTimetableForm,
  provider: DemoRailProvider | null = null,
): Timetable[] {
  const providersToGenerate = provider ? [provider] : form.providers;
  const startParts = form.time.split(":").map(Number);
  const endParts = form.timeEnd.split(":").map(Number);
  const start = (startParts[0] ?? Number.NaN) * 60 + (startParts[1] ?? Number.NaN);
  const end = (endParts[0] ?? Number.NaN) * 60 + (endParts[1] ?? Number.NaN);

  const rawItems = providersToGenerate.flatMap((providerName) => {
    const templates = demoAvailableTrains.filter((train) => train.provider === providerName);
    const firstTemplate = templates[0];
    if (!firstTemplate) return [];
    return Array.from({ length: Math.floor((end - start) / 40) + 1 }, (_, index): DemoTimetable => {
      const template = templates[index % templates.length] ?? firstTemplate;
      const departureMinutes = start + index * 40;
      const durationMinutes = providerName === "SRT" ? 148 : 161;
      const trainNumber = index < templates.length
        ? template.train_number
        : providerName === "SRT"
          ? `SRT ${String(329 + (index - templates.length) * 2).padStart(3, "0")}`
          : `KTX ${String(101 + index - templates.length).padStart(3, "0")}`;
      const departureAt = demoIsoAt(form.date, departureMinutes);
      const arrivalAt = demoIsoAt(form.date, departureMinutes + durationMinutes);
      return {
        ...template,
        id: `${providerName}:${trainNumber}:${departureAt}`,
        train_number: trainNumber,
        name: trainNumber,
        origin: form.origin,
        destination: form.destination,
        departure_at: departureAt,
        arrival_at: arrivalAt,
        departure: demoTime(departureMinutes),
        arrival: demoTime((departureMinutes + durationMinutes) % (24 * 60)),
        duration: `${Math.floor(durationMinutes / 60)}시간 ${durationMinutes % 60}분`,
        timetable_source: "mock",
      };
    });
  });
  return rawItems.map(mapTimetable);
}

const demoStationsByProvider: Record<
  DemoRailProvider,
  ReadonlyArray<readonly [name: string, cityName: string]>
> = {
  KORAIL: [
    ["서울", "서울"], ["수서", "서울"], ["용산", "서울"], ["영등포", "서울"], ["광명", "경기"], ["수원", "경기"],
    ["천안아산", "충남"], ["오송", "충북"], ["대전", "대전"], ["김천구미", "경북"], ["서대구", "대구"],
    ["동대구", "대구"], ["경주", "경북"], ["울산(통도사)", "울산"], ["부산", "부산"], ["포항", "경북"],
    ["공주", "충남"], ["익산", "전북"], ["전주", "전북"], ["정읍", "전북"], ["광주송정", "광주"],
    ["나주", "전남"], ["목포", "전남"], ["순천", "전남"], ["여수EXPO", "전남"], ["창원중앙", "경남"],
    ["마산", "경남"], ["진주", "경남"], ["강릉", "강원"], ["평창", "강원"], ["안동", "경북"],
  ],
  SRT: [
    ["서울", "서울"], ["수서", "서울"], ["동탄", "경기"], ["평택지제", "경기"], ["천안아산", "충남"], ["오송", "충북"],
    ["대전", "대전"], ["김천구미", "경북"], ["서대구", "대구"], ["동대구", "대구"], ["경주", "경북"],
    ["울산(통도사)", "울산"], ["부산", "부산"], ["포항", "경북"], ["공주", "충남"], ["익산", "전북"],
    ["전주", "전북"], ["정읍", "전북"], ["광주송정", "광주"], ["나주", "전남"], ["목포", "전남"],
    ["순천", "전남"], ["여수EXPO", "전남"], ["창원중앙", "경남"], ["마산", "경남"], ["진주", "경남"],
  ],
};

const demoCityCodes: Record<string, string> = {
  서울: "11", 부산: "26", 대구: "27", 인천: "28", 광주: "29", 대전: "30", 울산: "31",
  경기: "41", 강원: "42", 충북: "43", 충남: "44", 전북: "45", 전남: "46", 경북: "47", 경남: "48",
};

export function demoNodeId(name: string): string {
  const hash = [...name].reduce(
    (value, character) => ((value * 31) + (character.codePointAt(0) ?? 0)) >>> 0,
    17,
  );
  return `MOCK-${hash.toString(16).toUpperCase().padStart(8, "0")}`;
}

export function demoStations(providersToMerge: DemoRailProvider[]): DemoStation[] {
  const merged = new Map<string, DemoStation>();
  for (const provider of providersToMerge) {
    for (const [name, cityName] of demoStationsByProvider[provider]) {
      const nodeId = demoNodeId(name);
      const station = merged.get(nodeId) ?? {
        name,
        nodeId,
        cityName,
        cityCode: demoCityCodes[cityName] ?? "00",
        catalogProviders: [],
        sources: ["mock"],
        providerMembershipVerified: false,
      };
      if (!station.catalogProviders.includes(provider)) station.catalogProviders.push(provider);
      merged.set(nodeId, station);
    }
  }
  return [...merged.values()].sort((left, right) => left.name.localeCompare(right.name, "ko-KR"));
}
