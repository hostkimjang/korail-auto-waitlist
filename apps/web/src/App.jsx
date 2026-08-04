import { useCallback, useEffect, useRef, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  ArrowsLeftRight,
  Bell,
  CheckCircle,
  Clock,
  GearSix,
  House,
  Plus,
  ShieldCheck,
  Ticket,
  User,
  WarningCircle,
} from "@phosphor-icons/react";
import { fetchStations } from "./api/stations";
import {
  fetchTimetables,
  filterTimetables,
  mapTimetable,
  refreshSeatStatus,
} from "./api/timetables";
import { ApiError } from "./api/client";
import { logout } from "./api/auth";
import {
  buildWatchCreatePayloads,
  cancelWatch,
  createWatch,
  deleteWatch,
  fetchWatches,
  pauseWatch as pauseWatchRequest,
  startWatch,
  updateWatch,
} from "./api/watches";
import {
  connectBrowserPush,
  createNotificationChannel,
  disconnectBrowserPush,
  fetchNotificationChannels,
  readBrowserPushState,
  testNotificationChannel,
  updateNotificationChannel,
} from "./api/notifications";
import { AuthGate } from "./features/auth/AuthGate";
import { useAuthState } from "./features/auth/useAuthState";
import { AppNotificationCenter } from "./features/app/AppNotificationCenter";
import { useWatchCollection } from "./features/app/useWatchCollection";
import { useWatchMutations } from "./features/app/useWatchMutations";
import { ActiveWatchList } from "./features/home/ActiveWatchList";
import { PaymentRequiredSection } from "./features/home/PaymentRequiredSection";
import { ReservationList } from "./features/reservations/ReservationList";
import { ReservationSummary } from "./features/reservations/ReservationSummary";
import { CalendarPicker } from "./features/new-wait/CalendarPicker";
import { ReservationPolicyControl } from "./features/new-wait/ReservationPolicyControl";
import {
  canReserveWithAuthenticatedAccounts,
  defaultReservationPolicy,
} from "./features/new-wait/reservationPolicy";
import { ServerSeatStatusPanel } from "./features/new-wait/ServerSeatStatusPanel";
import { summarizeServerSeatStatus } from "./features/new-wait/serverSeatStatusSummary";
import { StepThreeDateSelector } from "./features/new-wait/StepThreeDateSelector";
import { StepThreeTimeRange } from "./features/new-wait/StepThreeTimeRange";
import { StepThreeRefreshControl } from "./features/new-wait/StepThreeRefreshControl";
import { StationCombobox } from "./features/new-wait/StationCombobox";
import { useStationCatalog } from "./features/new-wait/useStationCatalog";
import { useTimetableSearch } from "./features/new-wait/useTimetableSearch";
import {
  TrainResultCard,
  copyTrainJourney,
  seatClassNames,
} from "./features/new-wait/TrainResultCard";
import { useSeatWatchRegistration } from "./features/new-wait/useSeatWatchRegistration";
import { SettingsPage } from "./features/settings/SettingsPage";
import {
  createInitialNewWaitForm,
  selectNewWaitWeekday,
  seoulDateInput,
  setNewWaitTravelDate,
  swapNewWaitStations,
  toggleNewWaitProvider,
} from "./features/new-wait/newWaitForm";
import { useAppNotifications } from "./features/app/useAppNotifications";
import { Brand } from "./shared/ui/Brand";
import { PageHeader } from "./shared/ui/PageHeader";
import { StatusPill } from "./shared/ui/StatusPill";
import { hasObservedSeatEvidence } from "./domain/seatEvidence";
import { DEMO_MODE } from "./shared/lib/runtimeConfig";
import { OfficialHandoff } from "./features/official-handoff/OfficialHandoff";
import {
  DEFAULT_SEAT_OBSERVATION_INTERVAL_SECONDS,
  DEFAULT_TIMETABLE_REFRESH_INTERVAL_SECONDS,
  fetchUiPreferences,
  updateUiPreferences,
} from "./api/uiPreferences";
import { fetchCachedTimetableSnapshot } from "./api/timetableSnapshots";
import {
  deleteProviderAccount,
  fetchProviderAccounts,
  saveProviderAccount,
} from "./api/providerAccounts";
import { fetchProviderRuntimeStatuses } from "./api/providerRuntime";
import {
  createDemoWatch,
  demoNodeId,
  demoPaymentWatch,
  demoProviderAccounts,
  demoProviderRuntimeStatuses,
  demoStations,
  demoTimetablesForForm,
  initialWatches,
} from "./fixtures/demoData";

const navItems = [
  { id: "home", label: "홈", icon: House },
  { id: "new", label: "새 대기", icon: Plus },
  { id: "reservations", label: "내 예약", icon: Ticket },
  { id: "settings", label: "설정", icon: GearSix },
];

const activeWatchStatuses = new Set(["draft", "scheduled", "watching", "official_waitlist", "seat_found", "reserving", "paused", "cooldown", "auth_required"]);
const initialWatchCollection = DEMO_MODE ? initialWatches : [];

function currentRailAccountStatus(provider, accounts, loaded) {
  if (!loaded || !["KORAIL", "SRT"].includes(provider)) return null;
  const account = accounts.find((item) => item.provider === provider);
  return account?.configured && account.enabled ? account.lastAuthStatus : "not_checked";
}

export function isActiveWatch(watch) {
  return activeWatchStatuses.has(watch.status);
}

const providers = [
  { id: "KORAIL", name: "KTX · KORAIL", helper: "KTX 시간표" },
  { id: "SRT", name: "SRT", helper: "SRT 시간표" },
];

const timePresets = [
  { label: "새벽", start: "05:00", end: "09:00" },
  { label: "오전", start: "09:00", end: "12:00" },
  { label: "오후", start: "12:00", end: "18:00" },
  { label: "저녁", start: "18:00", end: "23:00" },
];

function parseDateInput(value) {
  const [year, month, day] = value.split("-").map(Number);
  return new Date(year, month - 1, day);
}

function dateLabel(value) {
  return new Intl.DateTimeFormat("ko-KR", { month: "long", day: "numeric", weekday: "short" }).format(parseDateInput(value));
}

function TimeRangePicker({ start, end, onChange }) {
  const toIndex = (time) => Number(time.slice(0, 2)) * 2 + Number(time.slice(3)) / 30;
  const toTime = (index) => `${String(Math.floor(index / 2)).padStart(2, "0")}:${index % 2 ? "30" : "00"}`;
  const startIndex = toIndex(start);
  const endIndex = toIndex(end);
  return (
    <div className="journey-field time-range-field">
      <span className="journey-label"><Clock size={18} />출발 시간 범위</span>
      <div className="time-range-card">
        <div className="time-values"><strong>{start}</strong><span>부터</span><strong>{end}</strong><span>까지</span></div>
        <div className="range-sliders">
          <input aria-label="출발 시작 시간" aria-valuetext={`${start}부터`} type="range" min="0" max="46" step="1" value={startIndex} onChange={(event) => onChange(toTime(Math.min(Number(event.target.value), endIndex - 1)), end)} />
          <input aria-label="출발 종료 시간" aria-valuetext={`${end}까지`} type="range" min="1" max="47" step="1" value={endIndex} onChange={(event) => onChange(start, toTime(Math.max(Number(event.target.value), startIndex + 1)))} />
        </div>
        <div className="time-preset-chips" aria-label="시간대 빠른 선택">
          {timePresets.map((preset) => <button key={preset.label} type="button" aria-pressed={start === preset.start && end === preset.end} onClick={() => onChange(preset.start, preset.end)}><strong>{preset.label}</strong><span>{preset.start}–{preset.end}</span></button>)}
        </div>
      </div>
    </div>
  );
}

function Sidebar({ activeView, onNavigate }) {
  return (
    <aside className="sidebar">
      <Brand />
      <nav aria-label="주 메뉴" className="side-nav">
        {navItems.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            type="button"
            className={activeView === id ? "nav-item is-active" : "nav-item"}
            onClick={() => onNavigate(id)}
          >
            <Icon size={24} weight={activeView === id ? "fill" : "regular"} />
            <span>{label}</span>
          </button>
        ))}
      </nav>
      <div className="private-badge"><ShieldCheck size={19} weight="fill" /><span>Tailscale 보호됨</span></div>
    </aside>
  );
}

function BottomNav({ activeView, onNavigate }) {
  return (
    <nav className="bottom-nav" aria-label="모바일 주 메뉴">
      {navItems.map(({ id, label, icon: Icon }) => (
        <button
          key={id}
          type="button"
          className={activeView === id ? "bottom-item is-active" : "bottom-item"}
          onClick={() => onNavigate(id)}
        >
          <Icon size={24} weight={activeView === id ? "fill" : "regular"} />
          <span>{label}</span>
        </button>
      ))}
    </nav>
  );
}

export function PaymentHero({ watch, onOfficialPayment }) {
  return <PaymentRequiredSection watches={[watch]} onOpenPayment={() => onOfficialPayment()} />;
}

function WatchManagementHero({ onNavigate }) {
  return (
    <section className="watch-management-hero">
      <div><StatusPill status="watching">관심 열차 관리</StatusPill><h2>공식 예약대기와 예매를<br />한곳에서 관리하세요.</h2><p>선택한 열차와 좌석 등급을 한곳에서 확인하세요.</p></div>
      <button className="button button-primary" type="button" onClick={() => onNavigate("new")}><Plus size={21} />새 대기 만들기</button>
    </section>
  );
}

function activeWatchHandoffTrain(watch) {
  const [routeOrigin = "", routeDestination = ""] = String(watch.route ?? "").split(" → ");
  const origin = watch.origin || routeOrigin;
  const destination = watch.destination || routeDestination;
  const travelDate = watch.travelDate || "";
  return {
    id: `active-watch-${watch.id}`,
    provider: watch.provider,
    origin,
    destination,
    name: watch.train,
    date: watch.date,
    departure_at: travelDate ? `${travelDate}T${watch.departure}:00+09:00` : "",
    departure: watch.departure,
    arrival: watch.arrival,
    seat_classes: [],
  };
}

export function Home({
  watches,
  paymentWatch = null,
  paymentWatches = paymentWatch ? [paymentWatch] : [],
  watchRefreshState = { isRefreshing: false, lastRefreshedAt: null },
  onRefreshWatches,
  onNavigate,
  onPause,
  onResume,
  onCancel,
  onChangeReservationPolicy,
  reservationPolicyUpdatingIds,
  onToast,
}) {
  const today = new Intl.DateTimeFormat("ko-KR", { year: "numeric", month: "long", day: "numeric", weekday: "short" }).format(new Date());
  const openOfficialPayment = (watch) => {
    if (!watch?.official_booking_url) {
      onToast("공식 예매 주소를 확인할 수 없습니다.");
      return;
    }
    onToast("공식 결제 화면을 새 창에서 엽니다.");
    window.open(watch.official_booking_url, "_blank", "noopener,noreferrer");
  };

  return (
    <div className="page home-page">
      <PageHeader title="지금 할 일" helper={today} />
      <PaymentRequiredSection
        watches={paymentWatches}
        onOpenPayment={openOfficialPayment}
        emptyState={<WatchManagementHero onNavigate={onNavigate} />}
      />
      <ActiveWatchList
        watches={watches}
        isRefreshing={watchRefreshState.isRefreshing}
        lastRefreshedAt={watchRefreshState.lastRefreshedAt}
        onRefresh={onRefreshWatches}
        onCreate={() => onNavigate("new")}
        onViewAll={() => onNavigate("reservations")}
        onPause={onPause}
        onResume={onResume}
        onCancel={onCancel}
        onChangeReservationPolicy={onChangeReservationPolicy}
        reservationPolicyUpdatingIds={reservationPolicyUpdatingIds}
        onOpenRailAccounts={() => onNavigate("settings", "rail-accounts")}
        renderSeatFoundAction={(watch) => (
          <OfficialHandoff
            train={activeWatchHandoffTrain(watch)}
            selectedSeatClass={watch.seatClass}
            onCopy={copyTrainJourney}
            triggerLabel="예매"
            actionUrl={watch.officialBookingUrl}
            seatFoundObservation={watch.seatFoundObservation}
            triggerClassName="button button-primary compact watch-booking-button"
          />
        )}
      />
    </div>
  );
}

export { WatchRow } from "./features/home/ActiveWatchList";
export { OfficialHandoff, hasObservedSeatEvidence };

function Stepper({ step }) {
  return (
    <ol className="stepper" aria-label={`새 대기 만들기 ${step}단계`}>
      {["여정", "조건", "열차 등록"].map((label, index) => {
        const value = index + 1;
        return <li key={label} className={value === step ? "is-current" : value < step ? "is-complete" : ""}><span>{value < step ? <CheckCircle weight="fill" /> : value}</span><em>{label}</em></li>;
      })}
    </ol>
  );
}

function Field({ label, icon: Icon, children }) {
  return <label className="field"><span>{Icon && <Icon size={18} />}{label}</span>{children}</label>;
}

function publicTimetableErrorMessage(result) {
  return result?.httpStatus === 503 || String(result?.message ?? "").includes("응답하지 않습니다")
    ? "공식 시간표 제공자가 응답하지 않습니다. 잠시 후 다시 시도해 주세요."
    : "공식 시간표를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.";
}

export function NewWait({ demo, watches = [], providerAccounts = [], refreshIntervalSeconds = DEFAULT_TIMETABLE_REFRESH_INTERVAL_SECONDS, onComplete, onCancelWatch = async () => undefined, onCancel }) {
  const [step, setStep] = useState(1);
  const reservationPolicyManuallySelectedRef = useRef(false);
  const [form, setForm] = useState(() => createInitialNewWaitForm({
    demo,
    providerAccounts,
    demoOriginNodeId: demo ? demoNodeId("서울") : null,
    demoDestinationNodeId: demo ? demoNodeId("부산") : null,
    now: new Date(),
  }));
  const {
    trains,
    state: timetableState,
    retryProvider: retryTimetableProvider,
    refreshProviderSeatStatus,
    retrySeatStatusProviders,
    refreshAll: refreshAllTimetables,
    synchronizeCached: synchronizeCachedTimetables,
  } = useTimetableSearch({
    active: step === 3,
    demo,
    form,
    loadTimetables: fetchTimetables,
    loadSeatStatus: refreshSeatStatus,
    loadCachedSnapshot: fetchCachedTimetableSnapshot,
    loadDemoTimetables: demoTimetablesForForm,
    filterTimetables,
    mapTimetable,
  });
  const {
    state: stationState,
    providerKey: stationProviderKey,
    ready: stationCatalogReady,
    stations: selectableStations,
    hasStation: hasSelectedStation,
    retry: retryStationCatalog,
  } = useStationCatalog({
    demo,
    providers: form.providers,
    loadStations: fetchStations,
    loadDemoStations: demoStations,
    setForm,
  });

  useEffect(() => {
    const reservationPolicy = demo
      ? "notify_only"
      : defaultReservationPolicy(form.providers, providerAccounts);
    setForm((value) => {
      const selectedPolicyUnavailable = value.reservationPolicy === "reserve_once_before_payment"
        && reservationPolicy === "notify_only";
      if (reservationPolicyManuallySelectedRef.current && !selectedPolicyUnavailable) {
        return value;
      }
      return value.reservationPolicy === reservationPolicy
        ? value
        : { ...value, reservationPolicy };
    });
  }, [demo, providerAccounts, stationProviderKey]);

  const swapStations = () => setForm(swapNewWaitStations);
  const setTravelDate = (date) => setForm((value) => setNewWaitTravelDate(value, date));
  const selectWeekday = (weekday) => setForm((value) => (
    selectNewWaitWeekday(value, weekday, seoulDateInput(new Date()))
  ));
  const toggleProvider = (provider) => setForm((value) => toggleNewWaitProvider(value, provider, {
    demo,
    providerAccounts,
    reservationPolicyManuallySelected: reservationPolicyManuallySelectedRef.current,
  }));
  const {
    registrationStateForSeat,
    chooseTrainSeat,
    hasActiveRegistration,
    submitError,
  } = useSeatWatchRegistration({
    form,
    trains,
    watches,
    onComplete,
    onCancelWatch,
    refreshProviderSeatStatus,
  });
  const originStationError = stationCatalogReady && !hasSelectedStation(form.origin, form.origin_node_id)
    ? "출발역을 제공된 역 목록에서 선택해 주세요."
    : "";
  const destinationStationError = stationCatalogReady && !hasSelectedStation(form.destination, form.destination_node_id)
    ? "도착역을 제공된 역 목록에서 선택해 주세요."
    : "";
  const stepOneErrors = [
    form.providers.length === 0 ? "KTX(KORAIL) 또는 SRT 운영사를 1개 이상 선택해 주세요." : "",
    form.origin_node_id && form.origin_node_id === form.destination_node_id ? "출발역과 도착역은 달라야 합니다." : "",
    form.date < seoulDateInput(new Date()) ? "오늘 이후 날짜를 선택해 주세요." : "",
    form.time >= form.timeEnd ? "출발 종료 시간은 시작 시간보다 늦어야 합니다." : "",
    form.selectedWeekdays.length !== 1 ? "출발 요일을 하나 선택해 주세요." : "",
  ].filter(Boolean);
  const stationNotice = stationCatalogReady
    ? `${stationState.source === "mock" ? "데모" : "공식"} 역 목록 ${selectableStations.length}개를 불러왔습니다. 이 목록은 운영사별 운행 여부를 증명하지 않으며, 선택 날짜의 시간표 결과에서 실제 운행 열차를 확인합니다.`
    : "";
  const canContinue = step === 1
    ? stationCatalogReady
      && !originStationError
      && !destinationStationError
      && stepOneErrors.length === 0
    : true;
  const visibleProviderCounts = trains.reduce((counts, train) => ({ ...counts, [train.provider]: (counts[train.provider] || 0) + 1 }), {});
  const serverSeatStatusSummary = summarizeServerSeatStatus(
    trains,
    form.providers,
    timetableState.providerResults,
    timetableState.loadingProviders,
  );

  return (
    <div className="page wizard-page">
      <PageHeader title="새 대기 만들기" helper="여정을 고른 뒤 열차와 좌석 등급을 바로 등록합니다." />
      <Stepper step={step} />
      <section className="wizard-panel">
        {step === 1 && (
          <div className="wizard-content journey-step">
            <div className="wizard-heading"><span>1</span><div><h2>어디로 떠나세요?</h2><p>역별 실제 운행 열차는 선택 날짜의 시간표에서 확인합니다.</p></div></div>
            <fieldset className="provider-section">
              <legend>운영사 <span>복수 선택 가능</span></legend>
              <div className="provider-cards">
                {providers.map((provider) => {
                  const checked = form.providers.includes(provider.id);
                  return <button key={provider.id} type="button" role="checkbox" aria-checked={checked} className={`provider-card provider-card-${provider.id.toLowerCase()} ${checked ? "is-selected" : ""}`} onClick={() => toggleProvider(provider.id)}><span className="provider-card-mark">{provider.id === "KORAIL" ? "KTX" : "SRT"}</span><span><strong>{provider.name}</strong><em>{provider.helper}</em></span><span className="provider-check">{checked && <CheckCircle weight="fill" size={23} />}</span></button>;
                })}
              </div>
            </fieldset>
            <div className="route-fields">
              <StationCombobox label="출발역" value={form.origin} selectedNodeId={form.origin_node_id} stations={selectableStations} loading={stationState.status === "loading"} disabled={!stationCatalogReady} error={originStationError} onChange={(station) => setForm((value) => ({ ...value, origin: station.name, origin_node_id: station.nodeId }))} />
              <div className="route-swap-slot">
                <button className="swap-button" type="button" disabled={!stationCatalogReady || !form.origin_node_id || !form.destination_node_id} aria-label="출발역과 도착역 바꾸기" onClick={swapStations}><ArrowsLeftRight size={23} /></button>
              </div>
              <StationCombobox label="도착역" value={form.destination} selectedNodeId={form.destination_node_id} stations={selectableStations} loading={stationState.status === "loading"} disabled={!stationCatalogReady} error={destinationStationError} onChange={(station) => setForm((value) => ({ ...value, destination: station.name, destination_node_id: station.nodeId }))} />
            </div>
            {stationState.status === "loading" && <div className="station-catalog-state" role="status"><Clock size={19} /><span>선택한 운영사의 역 목록을 불러오고 있습니다.</span></div>}
            {stationState.status === "error" && <div className="form-error station-catalog-error" role="alert"><WarningCircle weight="fill" /><span><strong>역 목록을 불러오지 못했습니다.</strong> {stationState.error}</span><button type="button" className="button button-outline compact" onClick={retryStationCatalog}>다시 불러오기</button></div>}
            {stationNotice && <div className="station-notice" role="note"><WarningCircle size={19} weight="fill" /><span>{stationNotice}</span></div>}
            <div className="journey-schedule-grid">
              <CalendarPicker value={form.date} onChange={setTravelDate} />
              <TimeRangePicker start={form.time} end={form.timeEnd} onChange={(time, timeEnd) => setForm((value) => ({ ...value, time, timeEnd }))} />
            </div>
            <fieldset className="weekday-section"><legend>출발 요일 빠른 선택 <span>선택하면 가장 가까운 해당 요일로 날짜가 이동합니다</span></legend><div className="weekday-chips">{["월", "화", "수", "목", "금", "토", "일"].map((weekday) => <button key={weekday} type="button" aria-pressed={form.selectedWeekdays[0] === weekday} onClick={() => selectWeekday(weekday)}>{weekday}</button>)}</div></fieldset>
            {stepOneErrors.length > 0 && <div className="form-error journey-error" role="alert"><WarningCircle weight="fill" /><span>{stepOneErrors[0]}</span></div>}
          </div>
        )}
        {step === 2 && (
          <div className="wizard-content">
            <div className="wizard-heading"><span>2</span><div><h2>어떤 좌석을 찾을까요?</h2><p>지나치게 넓은 조건은 요청 수를 늘릴 수 있어요.</p></div></div>
            <div className="form-grid">
              <Field label="인원" icon={User}><select value={form.passengers} onChange={(event) => setForm({ ...form, passengers: event.target.value })}><option value="1">성인 1명</option><option value="2">성인 2명</option></select></Field>
              <Field label="좌석" icon={Ticket}><select value={form.seat} onChange={(event) => setForm({ ...form, seat: event.target.value })}><option>일반실</option><option>특실</option><option>상관없음</option></select></Field>
            </div>
            <ReservationPolicyControl
              value={form.reservationPolicy}
              selectedProviders={form.providers}
              accounts={providerAccounts}
              onChange={(reservationPolicy) => {
                reservationPolicyManuallySelectedRef.current = true;
                setForm((value) => ({ ...value, reservationPolicy }));
              }}
            />
          </div>
        )}
        {step === 3 && (
          <div className="wizard-content">
            <div className="wizard-heading"><span>3</span><div><h2>공식 시간표에서 관심 열차를 고르세요</h2><p>일반실·특실을 각각 누르면 해당 대기가 즉시 등록됩니다. 여러 열차를 계속 추가할 수 있어요.</p></div></div>
            <div className="step-three-result-tools">
              <div className="step-three-live-toolbar" aria-label="열차 정보 자동 동기화">
                <div><strong>열차 정보 자동 동기화</strong><span>서버 snapshot · {refreshIntervalSeconds}초</span></div>
                <StepThreeRefreshControl
                  intervalSeconds={refreshIntervalSeconds}
                  enabled={timetableState.loadingProviders.length === 0 && trains.length > 0}
                  onManualRefresh={refreshAllTimetables}
                  onAutomaticRefresh={synchronizeCachedTimetables}
                />
              </div>
              <StepThreeDateSelector
                value={form.date}
                appliedDateLabel={dateLabel(form.date)}
                busy={timetableState.loadingProviders.length > 0}
                onChange={setTravelDate}
              />
              <StepThreeTimeRange
                appliedStart={form.time}
                appliedEnd={form.timeEnd}
                busy={timetableState.loadingProviders.length > 0}
                onApply={(time, timeEnd) => setForm((value) => ({ ...value, time, timeEnd }))}
              />
              {!demo && (
                <ServerSeatStatusPanel
                  summary={serverSeatStatusSummary}
                  onRetry={retrySeatStatusProviders}
                />
              )}
            </div>
            <div className="train-options">
              {timetableState.loadingProviders.length > 0 && <div className="timetable-state"><Clock size={24} /><span>{timetableState.loadingProviders.join(" · ")} 공식 시간표를 조회하고 있습니다.</span></div>}
              {Object.values(timetableState.providerResults).filter((result) => result.status === "error").map((result) => <div key={result.provider} className="form-error timetable-error" role="alert"><WarningCircle weight="fill" /><span><strong>{result.provider}</strong> {publicTimetableErrorMessage(result)}</span><button type="button" className="button button-outline compact" disabled={timetableState.loadingProviders.includes(result.provider)} onClick={() => retryTimetableProvider(result.provider)}>이 운영사만 다시 조회</button></div>)}
              {timetableState.loadingProviders.length === 0 && trains.length > 0 && <div className="timetable-result-summary" aria-label="시간표 조회 결과 요약"><strong>{form.time}–{form.timeEnd}</strong><span>총 {trains.length}개 열차 · KORAIL {visibleProviderCounts.KORAIL || 0} · SRT {visibleProviderCounts.SRT || 0}</span></div>}
              {timetableState.loadingProviders.length === 0 && trains.length === 0 && Object.values(timetableState.providerResults).some((result) => result.status === "success") && <div className="timetable-state"><Ticket size={24} /><span>선택한 날짜·시간 범위에 맞는 공식 열차가 없습니다.</span></div>}
              {trains.map((train) => {
                const registrationBySeat = Object.fromEntries(train.seat_classes.map((seat) => [
                  seat.seat_class,
                  registrationStateForSeat(train, seat.seat_class),
                ]));
                return <TrainResultCard
                  key={train.id}
                  train={train}
                  registrationBySeat={registrationBySeat}
                  onChooseSeat={chooseTrainSeat}
                  officialHandoffComponent={OfficialHandoff}
                  automaticReservationEnabled={
                    !demo
                    && form.reservationPolicy === "reserve_once_before_payment"
                    && canReserveWithAuthenticatedAccounts([train.provider], providerAccounts)
                  }
                />;
              })}
            </div>
          </div>
        )}
        {submitError && <div className="form-error" role="alert"><WarningCircle weight="fill" />{submitError}</div>}
        {step === 3 && !hasActiveRegistration && <p className="wizard-next-hint" role="status">일반실 또는 특실 버튼을 누르면 대기가 바로 등록되고, 같은 버튼으로 즉시 취소할 수 있습니다.</p>}
        <footer className="wizard-actions">
          <button type="button" className="button button-ghost" onClick={step === 1 ? onCancel : () => setStep(step - 1)}><ArrowLeft />{step === 1 ? "취소" : "이전"}</button>
          {step < 3
            ? <button type="button" className="button button-primary" disabled={!canContinue} onClick={() => setStep(step + 1)}>다음<ArrowRight /></button>
            : <span className="wizard-action-note">등록·취소는 각 좌석 버튼에서 바로 반영됩니다.</span>}
        </footer>
      </section>
    </div>
  );
}

export function Reservations({ watches, onNavigate, onDelete }) {
  return (
    <div className="page">
      <PageHeader title="내 예약" helper="감시부터 결제 완료까지 상태를 구분해 보여드립니다." action={<button className="button button-primary compact" type="button" onClick={() => onNavigate("new")}><Plus />새 대기</button>} />
      <ReservationSummary watches={watches} />
      <ReservationList
        watches={watches}
        onCreate={() => onNavigate("new")}
        onOpenOfficial={(watch) => window.open(watch.official_booking_url, "_blank", "noopener,noreferrer")}
        onDelete={onDelete}
      />
    </div>
  );
}

export { SettingsPage as Settings };

export function App() {
  const [activeView, setActiveView] = useState("home");
  const [settingsInitialSection, setSettingsInitialSection] = useState("notifications");
  const [settingsActiveSection, setSettingsActiveSection] = useState("notifications");
  const { auth, markAuthenticated, markUnauthenticated, retryAuthStatus } = useAuthState();
  const [channels, setChannels] = useState([]);
  const [browserPushState, setBrowserPushState] = useState({
    support: "checking",
    permission: "default",
    subscribed: false,
  });
  const [providerAccounts, setProviderAccounts] = useState(DEMO_MODE ? demoProviderAccounts : []);
  const [providerRuntimeStatuses, setProviderRuntimeStatuses] = useState(
    DEMO_MODE ? demoProviderRuntimeStatuses : [],
  );
  const [providerAccountsLoaded, setProviderAccountsLoaded] = useState(DEMO_MODE);
  const [providerAccountsLoading, setProviderAccountsLoading] = useState(false);
  const [pendingProviderAccount, setPendingProviderAccount] = useState(null);
  const [uiPreferences, setUiPreferences] = useState({
    timetableRefreshIntervalSeconds: DEFAULT_TIMETABLE_REFRESH_INTERVAL_SECONDS,
    seatObservationIntervalSeconds: DEFAULT_SEAT_OBSERVATION_INTERVAL_SECONDS,
    updatedAt: new Date(0).toISOString(),
  });
  const [savingUiPreferences, setSavingUiPreferences] = useState(false);
  const {
    state: notificationState,
    push: setToast,
    pushMany: pushNotifications,
    dismiss: dismissNotification,
    dismissGroup: dismissNotificationGroup,
    dismissTimed: dismissTimedNotifications,
    clear: clearNotifications,
  } = useAppNotifications();
  const refreshProviderRuntimeStatuses = useCallback(async () => {
    if (auth.demo) {
      setProviderRuntimeStatuses(demoProviderRuntimeStatuses);
      return;
    }
    const statuses = await fetchProviderRuntimeStatuses();
    setProviderRuntimeStatuses(statuses);
  }, [auth.demo]);

  const handleProviderAuthenticationTransition = useCallback(() => {
    if (auth.demo) return;
    setProviderAccountsLoaded(false);
    void fetchProviderAccounts().then((items) => {
      setProviderAccounts(items);
      setProviderAccountsLoaded(true);
      return refreshProviderRuntimeStatuses();
    }).catch(() => {
      // Keep the activity row neutral until a no-store account read succeeds.
    });
  }, [auth.demo, refreshProviderRuntimeStatuses]);

  const {
    watches,
    commitWatches,
    refreshState: watchRefreshState,
    requestRefresh: requestWatchesRefresh,
    beginReservationPolicyMutation,
    endReservationPolicyMutation,
  } = useWatchCollection({
    authenticated: auth.authenticated,
    demo: auth.demo,
    initialWatches: initialWatchCollection,
    pollIntervalSeconds: uiPreferences.timetableRefreshIntervalSeconds,
    loadWatches: fetchWatches,
    onAuthenticationExpired: markUnauthenticated,
    onProviderAuthenticationTransition: handleProviderAuthenticationTransition,
    pushNotifications,
  });
  const {
    pauseWatch,
    resumeWatch,
    cancelWatch: cancelWatchItem,
    changeReservationPolicy: changeWatchReservationPolicy,
    deleteWatchRecord,
    reservationPolicyUpdatingIds,
  } = useWatchMutations({
    demo: auth.demo,
    watches,
    commitWatches,
    pushToast: setToast,
    beginReservationPolicyMutation,
    endReservationPolicyMutation,
    requestWatchesRefresh,
    pauseWatchRequest,
    startWatchRequest: startWatch,
    cancelWatchRequest: cancelWatch,
    updateWatchRequest: updateWatch,
    deleteWatchRequest: deleteWatch,
  });

  useEffect(() => {
    if (!auth.authenticated || auth.demo) return undefined;
    let active = true;
    fetchNotificationChannels().then((channelItems) => {
      if (active) setChannels(channelItems);
    }).catch((error) => {
      if (error instanceof ApiError && error.status === 401) markUnauthenticated();
    });
    return () => { active = false; };
  }, [auth.authenticated, auth.demo]);

  useEffect(() => {
    if (!auth.authenticated || auth.demo) return undefined;
    let active = true;
    const refresh = () => {
      readBrowserPushState().then((state) => {
        if (active) setBrowserPushState(state);
      }).catch(() => {
        if (active) setBrowserPushState({ support: "unsupported", permission: "default", subscribed: false });
      });
    };
    refresh();
    window.addEventListener("focus", refresh);
    return () => {
      active = false;
      window.removeEventListener("focus", refresh);
    };
  }, [auth.authenticated, auth.demo]);

  useEffect(() => {
    if (!auth.authenticated) return undefined;
    if (auth.demo) {
      setProviderAccounts(demoProviderAccounts);
      setProviderRuntimeStatuses(demoProviderRuntimeStatuses);
      setProviderAccountsLoaded(true);
      return undefined;
    }
    let active = true;
    setProviderAccountsLoading(true);
    setProviderAccountsLoaded(false);
    fetchProviderAccounts().then((items) => {
      if (active) {
        setProviderAccounts(items);
        setProviderAccountsLoaded(true);
        void refreshProviderRuntimeStatuses().catch(() => {
          // Preserve the latest known runtime state when this supplemental read fails.
        });
      }
    }).catch((error) => {
      if (active) setToast(error instanceof Error ? error.message : "철도 계정 상태를 불러오지 못했습니다.");
    }).finally(() => {
      if (active) setProviderAccountsLoading(false);
    });
    return () => { active = false; };
  }, [auth.authenticated, auth.demo, refreshProviderRuntimeStatuses]);

  useEffect(() => {
    if (
      !auth.authenticated
      || auth.demo
      || activeView !== "settings"
      || settingsActiveSection !== "rail-accounts"
    ) {
      return undefined;
    }
    void refreshProviderRuntimeStatuses().catch(() => {
      // A polling failure must not erase a previously confirmed status or interrupt settings.
    });
    const timer = window.setInterval(() => {
      void refreshProviderRuntimeStatuses().catch(() => {
        // Keep the last successful status visible until the next successful poll.
      });
    }, 15_000);
    return () => window.clearInterval(timer);
  }, [activeView, auth.authenticated, auth.demo, refreshProviderRuntimeStatuses, settingsActiveSection]);

  useEffect(() => {
    if (!auth.authenticated || auth.demo) return undefined;
    let active = true;
    fetchUiPreferences().then((preferences) => {
      if (active) setUiPreferences(preferences);
    }).catch((error) => {
      if (active) setToast(error instanceof Error ? error.message : "화면 갱신 설정을 불러오지 못했습니다.");
    });
    return () => { active = false; };
  }, [auth.authenticated, auth.demo]);

  const navigate = (view, settingsSection) => {
    setActiveView(view);
    if (view === "settings") {
      const nextSettingsSection = settingsSection ?? "notifications";
      setSettingsInitialSection(nextSettingsSection);
      setSettingsActiveSection(nextSettingsSection);
    }
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const saveUiPreferences = async (input) => {
    setSavingUiPreferences(true);
    try {
      const saved = auth.demo
        ? { ...input, updatedAt: new Date().toISOString() }
        : await updateUiPreferences(input);
      setUiPreferences(saved);
      setToast("화면·좌석 관측 간격을 저장했습니다. 활성 작업의 다음 관측부터 적용됩니다.");
      return saved;
    } catch (error) {
      setToast(error instanceof Error ? error.message : "화면·좌석 관측 간격을 저장하지 못했습니다.");
      throw error;
    } finally {
      setSavingUiPreferences(false);
    }
  };

  const saveRailProviderAccount = async (provider, input) => {
    setPendingProviderAccount(provider);
    try {
      const saved = auth.demo
        ? { ...demoProviderAccounts.find((item) => item.provider === provider), maskedLoginId: `${input.loginId.slice(0, 2)}***`, updatedAt: new Date().toISOString() }
        : await saveProviderAccount(provider, input);
      setProviderAccounts((items) => [saved, ...items.filter((item) => item.provider !== provider)]);
      setProviderAccountsLoaded(true);
      void refreshProviderRuntimeStatuses().catch(() => {
        // The saved account remains valid even if its supplemental runtime read is unavailable.
      });
      setToast(`${provider} 철도 계정을 저장했습니다.`);
    } catch (error) {
      setToast(error instanceof Error ? error.message : "철도 계정을 저장하지 못했습니다.");
      throw error;
    } finally {
      setPendingProviderAccount(null);
    }
  };

  const removeRailProviderAccount = async (provider) => {
    setPendingProviderAccount(provider);
    try {
      if (!auth.demo) await deleteProviderAccount(provider);
      setProviderAccounts((items) => items.map((item) => item.provider === provider ? {
        ...item,
        configured: false,
        enabled: false,
        maskedLoginId: null,
        credentialVersion: 0,
        lastAuthStatus: "not_checked",
        lastAuthenticatedAt: null,
        updatedAt: null,
      } : item));
      setProviderAccountsLoaded(true);
      void refreshProviderRuntimeStatuses().catch(() => {
        // Preserve the prior status instead of replacing it with an unverified value.
      });
      setToast(`${provider} 철도 계정 연결을 해제했습니다.`);
    } catch (error) {
      setToast(error instanceof Error ? error.message : "철도 계정 연결을 해제하지 못했습니다.");
      throw error;
    } finally {
      setPendingProviderAccount(null);
    }
  };

  const completeWizard = async ({ form, selectedTrains }) => {
    let createdWatches;
    if (auth.demo) {
      createdWatches = selectedTrains.map((item, index) => {
        const seatClass = item.selected_seat_class || "any";
        const watchId = `watch-${Date.now()}-${item.id}-${seatClass}-${index}`;
        return createDemoWatch({
          id: watchId,
          provider: item.provider,
          train: item.name,
          route: `${form.origin} → ${form.destination}`,
          origin: form.origin,
          destination: form.destination,
          departure: item.departure,
          arrival: item.arrival,
          date: dateLabel(form.date),
          travelDate: form.date,
          status: "watching",
          statusLabel: "감시 중",
          seatClass,
          seatClassLabel: seatClassNames[seatClass],
          seatEvidenceLabel: `${seatClassNames[seatClass]} · 데모 좌석 상태`,
          officialBookingUrl: item.official_booking_url,
          reservationPolicy: form.reservationPolicy,
          candidates: [{
            train_number: item.train_number ?? item.name,
            departure_at: item.departure_at,
            arrival_at: item.arrival_at ?? null,
            seat_class: seatClass,
            priority: 1,
          }],
        });
      });
    } else {
      const channelIds = channels.filter((channel) => form.channels.includes(channel.kind) && channel.enabled).map((channel) => channel.id);
      const payloads = buildWatchCreatePayloads(form, selectedTrains, channelIds);
      const created = await Promise.all(payloads.map((payload) => createWatch(payload)));
      createdWatches = await Promise.all(created.map((watch) => startWatch(watch.id)));
    }
    const createdIds = new Set(createdWatches.map((watch) => watch.id));
    commitWatches((items) => [...createdWatches, ...items.filter((item) => !createdIds.has(item.id))]);
    setToast("대기를 등록했습니다. 열차를 더 추가할 수 있습니다.");
    return createdWatches;
  };

  const saveChannel = async ({ kind, name, config }) => {
    try {
      const existing = channels.find((channel) => channel.kind === kind);
      const saved = existing
        ? await updateNotificationChannel(existing.id, { name, config, enabled: true })
        : await createNotificationChannel({ kind, name, config, enabled: true });
      setChannels((items) => [saved, ...items.filter((item) => item.kind !== kind)]);
      setToast("알림 채널을 연결했습니다.");
    } catch (error) {
      setToast(error.message);
      throw error;
    }
  };

  const toggleChannel = async (channel, nextEnabled = !channel?.enabled) => {
    if (!channel) return;
    try {
      if (auth.demo) setChannels((items) => items.map((item) => item.id === channel.id ? { ...item, enabled: nextEnabled } : item));
      else if (channel.kind === "web_push") {
        if (nextEnabled) {
          const updated = await connectBrowserPush(channel.name, channel.id);
          setChannels((items) => items.map((item) => item.id === channel.id ? updated : item));
        } else {
          const updated = await updateNotificationChannel(channel.id, { enabled: false });
          setChannels((items) => items.map((item) => item.id === channel.id ? updated : item));
          await disconnectBrowserPush();
        }
        setBrowserPushState(await readBrowserPushState());
      } else {
        const updated = await updateNotificationChannel(channel.id, { enabled: nextEnabled });
        setChannels((items) => items.map((item) => item.id === channel.id ? updated : item));
      }
    } catch (error) {
      setToast(error.message);
    }
  };

  const testChannel = async (channel) => {
    try {
      if (channel.kind === "web_push") {
        const state = await readBrowserPushState();
        setBrowserPushState(state);
        if (state.permission !== "granted" || !state.subscribed) {
          throw new ApiError("이 기기의 OS 알림 구독을 먼저 켜 주세요.");
        }
      }
      if (!auth.demo) await testNotificationChannel(channel.id);
      setToast(`${channel.name} 시험 알림을 전송 대기열에 넣었습니다.`);
    } catch (error) {
      setToast(error.message);
    }
  };

  const connectWebPushChannel = async () => {
    try {
      if (auth.demo) {
        const now = new Date().toISOString();
        setChannels((items) => [{ id: "demo-web-push", kind: "web_push", name: "이 브라우저", enabled: true, configured: true, createdAt: now, updatedAt: now }, ...items.filter((item) => item.kind !== "web_push")]);
        setBrowserPushState({ support: "supported", permission: "granted", subscribed: true });
      } else {
        const existing = channels.find((channel) => channel.kind === "web_push");
        const channel = await connectBrowserPush(existing?.name ?? "이 브라우저", existing?.id ?? null);
        setChannels((items) => [channel, ...items.filter((item) => item.kind !== "web_push")]);
        setBrowserPushState(await readBrowserPushState());
      }
      setToast("이 기기의 OS 알림을 연결했습니다.");
    } catch (error) {
      setToast(error.message);
    }
  };

  const signOut = async () => {
    try {
      if (!auth.demo) await logout();
    } finally {
      commitWatches([]);
      setChannels([]);
      setProviderAccounts([]);
      clearNotifications();
      setUiPreferences({
        timetableRefreshIntervalSeconds: DEFAULT_TIMETABLE_REFRESH_INTERVAL_SECONDS,
        seatObservationIntervalSeconds: DEFAULT_SEAT_OBSERVATION_INTERVAL_SECONDS,
        updatedAt: new Date(0).toISOString(),
      });
      markUnauthenticated();
    }
  };

  if (auth.loading) return <main className="auth-page"><div className="loading-state"><img src="/icons/app-icon-512.png" alt="" /><span>안전하게 연결하는 중…</span></div></main>;
  if (!auth.authenticated) return <AuthGate status={auth} onAuthenticated={markAuthenticated} onRetryStatus={retryAuthStatus} />;

  const paymentWatches = watches.filter((watch) => watch.status === "payment_required");
  if (auth.demo && paymentWatches.length === 0) paymentWatches.push(demoPaymentWatch);
  const activeWatches = watches.filter(isActiveWatch).map((watch) => {
    const accountAuthStatus = currentRailAccountStatus(
      watch.provider,
      providerAccounts,
      providerAccountsLoaded,
    );
    return { ...watch, accountAuthStatus };
  });
  const reservationWatches = auth.demo && !watches.some((watch) => watch.id === demoPaymentWatch.id)
    ? [demoPaymentWatch, ...watches]
    : watches;
  return (
    <div className="app-shell">
      <Sidebar activeView={activeView} onNavigate={navigate} />
      <main className="main-content">
        <div className="mobile-header"><Brand /><button type="button" className="icon-button" aria-label="알림"><Bell size={23} /></button></div>
        {activeView === "home" && <Home watches={activeWatches} paymentWatches={paymentWatches} watchRefreshState={watchRefreshState} onRefreshWatches={requestWatchesRefresh} onNavigate={navigate} onPause={pauseWatch} onResume={resumeWatch} onCancel={cancelWatchItem} onChangeReservationPolicy={changeWatchReservationPolicy} reservationPolicyUpdatingIds={reservationPolicyUpdatingIds} onToast={setToast} />}
        {activeView === "new" && <NewWait demo={auth.demo} watches={watches} providerAccounts={providerAccounts} refreshIntervalSeconds={uiPreferences.timetableRefreshIntervalSeconds} onComplete={completeWizard} onCancelWatch={cancelWatchItem} onCancel={() => navigate("home")} />}
        {activeView === "reservations" && <Reservations watches={reservationWatches} onNavigate={navigate} onDelete={deleteWatchRecord} />}
        {activeView === "settings" && <SettingsPage channels={channels} demo={auth.demo} browserPushState={browserPushState} providerAccounts={providerAccounts} providerRuntimeStatuses={providerRuntimeStatuses} providerAccountsLoading={providerAccountsLoading} pendingProviderAccount={pendingProviderAccount} uiPreferences={uiPreferences} savingUiPreferences={savingUiPreferences} onSaveUiPreferences={saveUiPreferences} onSaveChannel={saveChannel} onToggleChannel={toggleChannel} onTestChannel={testChannel} onConnectWebPush={connectWebPushChannel} onSaveProviderAccount={saveRailProviderAccount} onDeleteProviderAccount={removeRailProviderAccount} onSectionChange={setSettingsActiveSection} onLogout={signOut} initialSection={settingsInitialSection} />}
      </main>
      <BottomNav activeView={activeView} onNavigate={navigate} />
      <AppNotificationCenter
        state={notificationState}
        onDismiss={dismissNotification}
        onDismissGroup={dismissNotificationGroup}
        onDismissTimed={dismissTimedNotifications}
      />
    </div>
  );
}
