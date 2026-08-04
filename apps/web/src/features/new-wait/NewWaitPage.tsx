import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  ArrowLeft,
  ArrowRight,
  ArrowsLeftRight,
  CheckCircle,
  Clock,
  Ticket,
  User,
  WarningCircle,
  type Icon,
} from "@phosphor-icons/react";

import type { ProviderAccount, RailProvider } from "../../api/providerAccounts";
import { fetchStations } from "../../api/stations";
import {
  fetchTimetables,
  filterTimetables,
  mapTimetable,
  refreshSeatStatus,
} from "../../api/timetables";
import { fetchCachedTimetableSnapshot } from "../../api/timetableSnapshots";
import { DEFAULT_TIMETABLE_REFRESH_INTERVAL_SECONDS } from "../../api/uiPreferences";
import type { MappedWatch } from "../../api/watches";
import {
  demoNodeId,
  demoStations,
  demoTimetablesForForm,
} from "../../fixtures/demoData";
import { PageHeader } from "../../shared/ui/PageHeader";
import { CalendarPicker } from "./CalendarPicker";
import { ReservationPolicyControl } from "./ReservationPolicyControl";
import {
  canReserveWithAuthenticatedAccounts,
  defaultReservationPolicy,
} from "./reservationPolicy";
import { ServerSeatStatusPanel } from "./ServerSeatStatusPanel";
import { summarizeServerSeatStatus } from "./serverSeatStatusSummary";
import { StationCombobox } from "./StationCombobox";
import { StepThreeDateSelector } from "./StepThreeDateSelector";
import { StepThreeRefreshControl } from "./StepThreeRefreshControl";
import { StepThreeTimeRange } from "./StepThreeTimeRange";
import {
  TrainResultCard,
  type TrainResultOfficialHandoffComponent,
} from "./TrainResultCard";
import {
  createInitialNewWaitForm,
  formatNewWaitDateLabel,
  selectNewWaitWeekday,
  seoulDateInput,
  setNewWaitTravelDate,
  swapNewWaitStations,
  toggleNewWaitProvider,
} from "./newWaitForm";
import type { NewWaitWeekday } from "./newWaitForm";
import {
  useSeatWatchRegistration,
  type SeatWatchCancellation,
  type SeatWatchRegistrationCompletion,
} from "./useSeatWatchRegistration";
import { useStationCatalog } from "./useStationCatalog";
import {
  useTimetableSearch,
  type TimetableProviderError,
} from "./useTimetableSearch";

interface ProviderOption {
  id: RailProvider;
  name: string;
  helper: string;
}

const providers: readonly ProviderOption[] = [
  { id: "KORAIL", name: "KTX · KORAIL", helper: "KTX 시간표" },
  { id: "SRT", name: "SRT", helper: "SRT 시간표" },
];

const timePresets = [
  { label: "새벽", start: "05:00", end: "09:00" },
  { label: "오전", start: "09:00", end: "12:00" },
  { label: "오후", start: "12:00", end: "18:00" },
  { label: "저녁", start: "18:00", end: "23:00" },
] as const;

interface TimeRangePickerProps {
  start: string;
  end: string;
  onChange: (start: string, end: string) => void;
}

function TimeRangePicker({ start, end, onChange }: TimeRangePickerProps) {
  const toIndex = (time: string): number => (
    Number(time.slice(0, 2)) * 2 + Number(time.slice(3)) / 30
  );
  const toTime = (index: number): string => (
    `${String(Math.floor(index / 2)).padStart(2, "0")}:${index % 2 ? "30" : "00"}`
  );
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

interface StepperProps {
  step: number;
}

function Stepper({ step }: StepperProps) {
  return (
    <ol className="stepper" aria-label={`새 대기 만들기 ${step}단계`}>
      {["여정", "조건", "열차 등록"].map((label, index) => {
        const value = index + 1;
        return <li key={label} className={value === step ? "is-current" : value < step ? "is-complete" : ""}><span>{value < step ? <CheckCircle weight="fill" /> : value}</span><em>{label}</em></li>;
      })}
    </ol>
  );
}

interface FieldProps {
  label: string;
  icon: Icon;
  children: ReactNode;
}

function Field({ label, icon: FieldIcon, children }: FieldProps) {
  return <label className="field"><span><FieldIcon size={18} />{label}</span>{children}</label>;
}

function publicTimetableErrorMessage(result: TimetableProviderError): string {
  return result.httpStatus === 503 || result.message.includes("응답하지 않습니다")
    ? "공식 시간표 제공자가 응답하지 않습니다. 잠시 후 다시 시도해 주세요."
    : "공식 시간표를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.";
}

export interface NewWaitPageProps {
  demo: boolean;
  watches?: readonly MappedWatch[];
  providerAccounts?: readonly ProviderAccount[];
  refreshIntervalSeconds?: number;
  onComplete: SeatWatchRegistrationCompletion;
  onCancelWatch?: SeatWatchCancellation;
  onCancel: () => void;
  officialHandoffComponent: TrainResultOfficialHandoffComponent;
}

export function NewWaitPage({
  demo,
  watches = [],
  providerAccounts = [],
  refreshIntervalSeconds = DEFAULT_TIMETABLE_REFRESH_INTERVAL_SECONDS,
  onComplete,
  onCancelWatch = async () => undefined,
  onCancel,
  officialHandoffComponent,
}: NewWaitPageProps) {
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
  }, [demo, form.providers, providerAccounts]);

  const swapStations = (): void => setForm(swapNewWaitStations);
  const setTravelDate = (date: string): void => setForm((value) => (
    setNewWaitTravelDate(value, date)
  ));
  const selectWeekday = (weekday: NewWaitWeekday): void => setForm((value) => (
    selectNewWaitWeekday(value, weekday, seoulDateInput(new Date()))
  ));
  const toggleProvider = (provider: RailProvider): void => setForm((value) => (
    toggleNewWaitProvider(value, provider, {
      demo,
      providerAccounts,
      reservationPolicyManuallySelected: reservationPolicyManuallySelectedRef.current,
    })
  ));
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
  const originStationError = stationCatalogReady
    && !hasSelectedStation(form.origin, form.origin_node_id)
    ? "출발역을 제공된 역 목록에서 선택해 주세요."
    : "";
  const destinationStationError = stationCatalogReady
    && !hasSelectedStation(form.destination, form.destination_node_id)
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
  const visibleProviderCounts = trains.reduce<Partial<Record<RailProvider, number>>>(
    (counts, train) => ({
      ...counts,
      [train.provider]: (counts[train.provider] ?? 0) + 1,
    }),
    {},
  );
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
            <fieldset className="weekday-section"><legend>출발 요일 빠른 선택 <span>선택하면 가장 가까운 해당 요일로 날짜가 이동합니다</span></legend><div className="weekday-chips">{(["월", "화", "수", "목", "금", "토", "일"] satisfies NewWaitWeekday[]).map((weekday) => <button key={weekday} type="button" aria-pressed={form.selectedWeekdays[0] === weekday} onClick={() => selectWeekday(weekday)}>{weekday}</button>)}</div></fieldset>
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
                appliedDateLabel={formatNewWaitDateLabel(form.date)}
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
                  onRetry={(providerValues) => retrySeatStatusProviders(providerValues.flatMap(
                    (provider): RailProvider[] => (
                      provider === "KORAIL" || provider === "SRT" ? [provider] : []
                    ),
                  ))}
                />
              )}
            </div>
            <div className="train-options">
              {timetableState.loadingProviders.length > 0 && <div className="timetable-state"><Clock size={24} /><span>{timetableState.loadingProviders.join(" · ")} 공식 시간표를 조회하고 있습니다.</span></div>}
              {Object.values(timetableState.providerResults).filter((result): result is TimetableProviderError => result.status === "error").map((result) => <div key={result.provider} className="form-error timetable-error" role="alert"><WarningCircle weight="fill" /><span><strong>{result.provider}</strong> {publicTimetableErrorMessage(result)}</span><button type="button" className="button button-outline compact" disabled={timetableState.loadingProviders.includes(result.provider)} onClick={() => retryTimetableProvider(result.provider)}>이 운영사만 다시 조회</button></div>)}
              {timetableState.loadingProviders.length === 0 && trains.length > 0 && <div className="timetable-result-summary" aria-label="시간표 조회 결과 요약"><strong>{form.time}–{form.timeEnd}</strong><span>총 {trains.length}개 열차 · KORAIL {visibleProviderCounts.KORAIL ?? 0} · SRT {visibleProviderCounts.SRT ?? 0}</span></div>}
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
                  officialHandoffComponent={officialHandoffComponent}
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
