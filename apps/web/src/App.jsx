import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  ArrowsLeftRight,
  Bell,
  CheckCircle,
  Clock,
  DiscordLogo,
  GearSix,
  GlobeSimple,
  House,
  LockKey,
  PaperPlaneTilt,
  Plus,
  ShieldCheck,
  Ticket,
  User,
  WarningCircle,
  WifiHigh,
} from "@phosphor-icons/react";
import {
  DEMO_MODE,
  buildWatchCreatePayloads,
  cancelWatch,
  createWatch,
  deleteWatch,
  fetchStations,
  fetchTimetables,
  fetchWatches,
  filterTimetables,
  pauseWatch as pauseWatchRequest,
  refreshSeatStatus,
  mapTimetable,
  startWatch,
  updateWatch,
} from "./api.js";
import { ApiError } from "./api/client";
import { logout } from "./api/auth";
import {
  connectBrowserPush,
  createNotificationChannel,
  disconnectBrowserPush,
  fetchNotificationChannels,
  readBrowserPushState,
  testNotificationChannel,
  updateNotificationChannel,
} from "./api/notifications";
import { subscribeToEvents } from "./api/events";
import { AuthGate } from "./features/auth/AuthGate";
import { useAuthState } from "./features/auth/useAuthState";
import { AppNotificationCenter } from "./features/app/AppNotificationCenter";
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
import { recoverRefreshedRegistrationTrain } from "./features/new-wait/registrationEvidenceRecovery";
import {
  SeatRegistrationCancelButton,
  SeatRegistrationStatus,
  TrainRegistrationBadge,
} from "./features/new-wait/RegistrationStateVisuals";
import { seatRegistrationKey, useInstantWatchRegistration } from "./features/new-wait/useInstantWatchRegistration";
import { resolvedSeatRegistration } from "./features/new-wait/watchRegistrationHydration";
import { SystemStatusDashboard } from "./features/settings/SystemStatusDashboard";
import { TimetableRefreshSettings } from "./features/settings/TimetableRefreshSettings";
import { ProviderAccountSettings } from "./features/settings/ProviderAccountSettings";
import {
  createInitialNewWaitForm,
  selectNewWaitWeekday,
  seoulDateInput,
  setNewWaitTravelDate,
  swapNewWaitStations,
  toggleNewWaitProvider,
} from "./features/new-wait/newWaitForm";
import { createLiveDataReloadCoordinator } from "./features/app/liveDataReloadCoordinator";
import { createReservationPolicyMutationGuard } from "./features/home/reservationPolicyMutationGuard";
import {
  detectSeatAvailabilityLostTransitions,
  detectSeatFoundTransitions,
  detectWatchActionTransitions,
  reconcileWatchSnapshots,
} from "./features/app/watchSnapshots";
import {
  buildAvailabilityLostToast,
  buildSeatFoundToast,
  buildWatchActionToast,
} from "./features/app/reservationToast";
import { buildLiveReservationNotice } from "./features/app/liveReservationNotice";
import { useAppNotifications } from "./features/app/useAppNotifications";
import { Brand } from "./shared/ui/Brand";
import { StatusPill } from "./shared/ui/StatusPill";
import { delayUntilRefreshRotationEnds } from "./shared/lib/refreshIndicator";
import { seatObservationReasonMeta } from "./domain/seatDiagnostics";
import { hasObservedSeatEvidence } from "./domain/seatEvidence";
import { OfficialHandoff } from "./features/official-handoff/OfficialHandoff";
import { isExpiredWatchCreateConflict } from "./domain/apiErrors";
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

function currentRailAccountStatus(provider, accounts, loaded) {
  if (!loaded || !["KORAIL", "SRT"].includes(provider)) return null;
  const account = accounts.find((item) => item.provider === provider);
  return account?.configured && account.enabled ? account.lastAuthStatus : "not_checked";
}

export function isActiveWatch(watch) {
  return activeWatchStatuses.has(watch.status);
}

const notificationOptions = [
  { id: "web_push", label: "OS 알림", helper: "Windows·Android·iOS 시스템 알림", icon: Bell },
  { id: "telegram", label: "텔레그램", helper: "Bot API로 즉시 전송", icon: PaperPlaneTilt },
  { id: "discord_webhook", label: "디스코드", helper: "Webhook 채널 알림", icon: DiscordLogo },
];

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

function PageHeader({ title, helper, action }) {
  return (
    <header className="page-header">
      <div>
        <h1>{title}</h1>
        {helper && <p>{helper}</p>}
      </div>
      {action}
    </header>
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

function fareLabel(train) {
  if (!Number.isFinite(train.adult_fare)) return "운임 확인 필요";
  return `성인 ${new Intl.NumberFormat("ko-KR").format(train.adult_fare)}원`;
}

function displayTrainName(value) {
  return String(value ?? "")
    .replace(/^0+(?=\d+$)/, "")
    .replace(/(\s)0+(?=\d+$)/, "$1");
}

function timetableRetrievedLabel(value) {
  if (!value) return "시간표 업데이트 시각 미제공";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "시간표 업데이트 시각 미제공";
  return `시간표 업데이트 ${new Intl.DateTimeFormat("ko-KR", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Seoul" }).format(date)}`;
}

function publicTimetableErrorMessage(result) {
  return result?.httpStatus === 503 || String(result?.message ?? "").includes("응답하지 않습니다")
    ? "공식 시간표 제공자가 응답하지 않습니다. 잠시 후 다시 시도해 주세요."
    : "공식 시간표를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.";
}

const seatClassNames = { standard: "일반실", first: "특실", any: "좌석 무관" };
const directlyReservableSeatStatuses = new Set(["available", "limited", "standing_plus_seat"]);

function seatStatusMeta(status, notObservedReason = null) {
  if (status === "unavailable") return { label: "예매 불가", helper: "현재 이 좌석 등급은 예매할 수 없습니다." };
  if (status === "available") return { label: "예매 가능", helper: "공식 예매 화면에서 최종 좌석을 확인하세요." };
  if (status === "limited") return { label: "매진 임박", helper: "잔여 좌석이 빠르게 바뀔 수 있습니다." };
  if (status === "standing_plus_seat") return { label: "입석+좌석", helper: "일부 구간은 입석으로 배정될 수 있으니 공식 예매 화면에서 확인하세요." };
  if (status === "sold_out") return { label: "매진", helper: "취소표나 공식 예약대기 가능 여부를 확인하세요." };
  if (status === "waitlist_available") return { label: "예약대기 가능", helper: "공식 예약대기 접수 여부를 확인하세요." };
  if (status === "stale") return { label: "확인 필요", helper: "표시 시각이 오래되어 다시 확인해야 합니다." };
  if (status === "error") return { label: "확인 필요", helper: "좌석 조회가 지연되어 공식 확인이 필요합니다." };
  if (status === "not_enough_seats") return { label: "예매 불가", helper: "선택 인원을 함께 배정할 좌석이 부족합니다." };
  if (status === "departed") return { label: "예매 불가", helper: "이미 출발한 열차입니다." };
  if (status === "out_of_service") return { label: "예매 불가", helper: "현재 운행하지 않는 열차입니다." };
  if (status === "reservation_completed") return { label: "예매 불가", helper: "예약내역에서 결제기한을 확인하세요." };
  if (status === "not_offered") return { label: "미운영", helper: "이 열차에는 해당 좌석 등급이 없습니다." };
  if (notObservedReason) return seatObservationReasonMeta(notObservedReason);
  return { label: "확인 필요", helper: "좌석 상태가 관측되지 않았습니다." };
}

function seatFareLabel(seat) {
  return Number.isFinite(seat.fare) ? `${new Intl.NumberFormat("ko-KR").format(seat.fare)}원` : "운임 확인 필요";
}

function seatSourceLabel(seat) {
  if (seat.provenance?.kind === "mock") return "데모 좌석 상태";
  if (seat.provenance?.kind === "official_provider") {
    return `공식 좌석 관측 · ${timetableRetrievedLabel(seat.provenance.observed_at).replace("시간표 업데이트 ", "")}`;
  }
  if (seat.provenance?.kind === "official_page_browser_companion") {
    return `공식 화면 동기화 · ${timetableRetrievedLabel(seat.provenance.observed_at).replace("시간표 업데이트 ", "")}`;
  }
  if (seat.provenance?.kind === "user_confirmed_official_page") {
    return `공식 페이지에서 직접 확인 · ${timetableRetrievedLabel(seat.provenance.observed_at).replace("시간표 업데이트 ", "")}`;
  }
  return "좌석 정보 미확인";
}

function userConfirmationRemainingMs(seat) {
  const provenance = seat?.provenance;
  if (!["official_page_browser_companion", "user_confirmed_official_page"].includes(provenance?.kind)) return null;
  if (!hasObservedSeatEvidence(seat)) return 0;
  return provenance.client_freshness.ttl_ms
    - (performance.now() - provenance.client_freshness.received_monotonic_ms);
}

async function copyTrainJourney(train) {
  if (!navigator.clipboard?.writeText) return false;
  const journeySummary = `${String(train.departure_at ?? "").slice(0, 10)} / ${train.origin} → ${train.destination} / ${train.name} / ${train.departure} 출발`;
  await navigator.clipboard.writeText(journeySummary);
  return true;
}

export function SeatClassPanel({
  train,
  seat,
  registration,
  onChooseSeat,
  automaticReservationEnabled = false,
}) {
  const [, setFreshnessVersion] = useState(0);
  useEffect(() => {
    const remaining = userConfirmationRemainingMs(seat);
    if (remaining == null || remaining <= 0) return undefined;
    const timer = window.setTimeout(
      () => setFreshnessVersion((value) => value + 1),
      Math.min(remaining + 16, 2_147_483_647),
    );
    return () => window.clearTimeout(timer);
  }, [seat.provenance]);
  const observed = hasObservedSeatEvidence(seat);
  const displayStatus = observed ? seat.status : "unknown";
  const meta = seatStatusMeta(displayStatus, observed ? null : seat.provenance?.reason);
  const selectable = !["not_offered", "departed", "out_of_service", "reservation_completed"].includes(seat.status);
  const actions = Array.isArray(seat.actions) ? seat.actions : [];
  const isDemoSeat = seat.provenance?.kind === "mock";
  const canRegisterDirectReservation = automaticReservationEnabled
    && directlyReservableSeatStatuses.has(seat.status);
  const canAddToWatch = observed
    && selectable
    && (isDemoSeat || canRegisterDirectReservation || ["sold_out", "waitlist_available"].includes(seat.status))
    && actions.some((action) => action.kind === "add_to_watch");
  const officialActionKind = seat.status === "waitlist_available"
    ? "official_waitlist"
    : ["available", "limited", "standing_plus_seat"].includes(seat.status)
      ? "official_check"
      : null;
  const officialAction = officialActionKind
    ? actions.find((action) => action.kind === officialActionKind && typeof action.url === "string")
    : null;
  const officialActionLabel = officialActionKind === "official_waitlist"
    ? "공식 예약대기"
    : "공식 예매";
  const officialActionClassName = officialActionKind === "official_waitlist"
    ? "button button-primary compact seat-action-official-waitlist"
    : "button button-primary compact seat-action-booking";
  const showOfficialAction = officialAction
    && !(canRegisterDirectReservation && canAddToWatch);
  const registrationStatus = registration?.status ?? "idle";
  const registered = registrationStatus === "active";
  const registering = registrationStatus === "pending";
  const cancelling = registrationStatus === "cancelling";
  const watchLabel = !observed
    ? `${seatClassNames[seat.seat_class]} 관심 열차에 추가`
    : canRegisterDirectReservation
      ? `${seatClassNames[seat.seat_class]} 자동 예매`
      : seat.status === "waitlist_available"
      ? `${seatClassNames[seat.seat_class]} 예약대기`
      : seat.status === "sold_out"
        ? `${seatClassNames[seat.seat_class]} 취소표 대기`
        : `${seatClassNames[seat.seat_class]}로 대기`;
  const hasActiveRegistration = registered || cancelling;
  return (
    <section
      className={`seat-class-panel seat-status-${displayStatus} ${hasActiveRegistration ? "is-selected is-registered" : ""}`}
      aria-label={`${train.name} ${seatClassNames[seat.seat_class]}`}
      data-registration-state={registrationStatus}
    >
      <div className="seat-class-heading">
        <strong className="seat-class-name">{seatClassNames[seat.seat_class]}</strong>
        <span className="seat-status-chip">{meta.label}</span>
      </div>
      <p className="seat-class-helper" title={meta.helper}>{meta.helper}</p>
      <div className="seat-class-meta"><span className="seat-fare">{seatFareLabel(seat)}</span><span className="seat-source">{seatSourceLabel(seat)}</span></div>
      {hasActiveRegistration && <SeatRegistrationStatus
        cancelling={cancelling}
        reservationPolicy={registration?.reservationPolicy}
      />}
      <div className="seat-class-actions">
        {showOfficialAction && <OfficialHandoff
          train={train}
          selectedSeatClass={seat.seat_class}
          onCopy={copyTrainJourney}
          triggerLabel={officialActionLabel}
          actionUrl={officialAction.url}
          searchUrl={train.official_search_url}
          triggerClassName={officialActionClassName}
        />}
        {hasActiveRegistration
          ? <SeatRegistrationCancelButton
            seatClassLabel={seatClassNames[seat.seat_class]}
            cancelling={cancelling}
            onCancel={() => onChooseSeat(train.id, seat.seat_class)}
          />
          : canAddToWatch && <button
          type="button"
          aria-pressed="false"
          aria-busy={registering}
          disabled={registering}
          className={`${!observed || !showOfficialAction ? "button button-primary" : "button button-secondary"} compact seat-action-watch seat-action-${displayStatus}`}
          onClick={() => onChooseSeat(train.id, seat.seat_class)}
        >
          {registering
            ? `${seatClassNames[seat.seat_class]} 등록 중…`
            : registrationStatus === "error"
              ? `${seatClassNames[seat.seat_class]} 다시 등록`
              : watchLabel}
        </button>}
      </div>
      {seat.registration_evidence_error && <p className="seat-registration-evidence-error" role="note">{seat.registration_evidence_error}</p>}
      {(registrationStatus === "error" || (registrationStatus === "active" && registration.message)) && <p className="seat-registration-error" role="alert">{registration.message}</p>}
    </section>
  );
}

const TrainResultCard = memo(function TrainResultCard({
  train,
  registrationBySeat,
  onChooseSeat,
  automaticReservationEnabled,
}) {
  const titleId = `train-title-${train.id.replace(/[^a-zA-Z0-9_-]/g, "-")}`;
  const registrationCount = train.seat_classes.filter((seat) => ["active", "cancelling"].includes(registrationBySeat[seat.seat_class]?.status)).length;
  const registered = registrationCount > 0;
  return (
    <article
      className={registered ? "train-result-card is-selected has-active-registration" : "train-result-card"}
      aria-labelledby={titleId}
      data-registration-count={registrationCount}
    >
      <header className="train-result-header">
        <span className={`provider-chip ${train.provider === "SRT" ? "provider-srt" : "provider-korail"}`}>{train.provider}</span>
        <div><strong id={titleId} aria-label={train.name}>{displayTrainName(train.name)}</strong><span>{train.train_type || train.provider} · {train.duration}</span></div>
        <div className="train-result-header-actions">
          {registered && <TrainRegistrationBadge count={registrationCount} />}
        </div>
      </header>
      <div className="train-route-time">
        <div><strong>{train.departure}</strong><span>{train.origin}</span></div>
        <div className="train-duration"><ArrowRight /><span>{train.duration}</span></div>
        <div><strong>{train.arrival}</strong><span>{train.destination}</span></div>
      </div>
      <div className="train-result-meta">
        <span>{fareLabel(train)}</span>
        <span>{train.timetable_source === "mock" ? "데모 시간표" : "공식 시간표"}</span>
        <span>{train.timetable_source === "mock" ? timetableRetrievedLabel(train.timetable_retrieved_at).replace("시간표 업데이트", "데모 기준 시각") : timetableRetrievedLabel(train.timetable_retrieved_at)}</span>
      </div>
      <div className="seat-class-grid">
        {train.seat_classes.map((seat) => <SeatClassPanel
          key={seat.seat_class}
          train={train}
          seat={seat}
          registration={registrationBySeat[seat.seat_class]}
          onChooseSeat={onChooseSeat}
          automaticReservationEnabled={automaticReservationEnabled}
        />)}
      </div>
    </article>
  );
}, (previous, next) => (
  previous.train === next.train
  && previous.onChooseSeat === next.onChooseSeat
  && previous.automaticReservationEnabled === next.automaticReservationEnabled
  && previous.registrationBySeat.standard === next.registrationBySeat.standard
  && previous.registrationBySeat.first === next.registrationBySeat.first
  && previous.registrationBySeat.any === next.registrationBySeat.any
));

export function NewWait({ demo, watches = [], providerAccounts = [], refreshIntervalSeconds = DEFAULT_TIMETABLE_REFRESH_INTERVAL_SECONDS, onComplete, onCancelWatch = async () => undefined, onCancel }) {
  const [step, setStep] = useState(1);
  const [submitError, setSubmitError] = useState("");
  const reservationPolicyManuallySelectedRef = useRef(false);
  const [form, setForm] = useState(() => createInitialNewWaitForm({
    demo,
    providerAccounts,
    demoOriginNodeId: demo ? demoNodeId("서울") : null,
    demoDestinationNodeId: demo ? demoNodeId("부산") : null,
    now: new Date(),
  }));
  const { getRegistrationState, register, cancel: cancelRegistration, successCount } = useInstantWatchRegistration();
  const registrationStateForSeat = (train, seatClass) => {
    const local = getRegistrationState(seatRegistrationKey(train.id, seatClass));
    return resolvedSeatRegistration(local, watches, train, seatClass);
  };
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
  const chooseTrainSeatImplementation = async (id, seatClass) => {
    const train = trains.find((item) => item.id === id);
    if (!train || !form.providers.includes(train.provider)) return;
    const key = seatRegistrationKey(train.id, seatClass);
    const currentRegistration = registrationStateForSeat(train, seatClass);
    if (currentRegistration.status === "active") {
      const cancelled = await cancelRegistration(
        key,
        onCancelWatch,
        currentRegistration.watchId,
      );
      if (cancelled) setSubmitError("");
      return;
    }
    if (currentRegistration.status === "pending" || currentRegistration.status === "cancelling") return;
    const seat = train.seat_classes?.find((item) => item.seat_class === seatClass);
    if (!seat?.actions?.some((action) => action.kind === "add_to_watch")) return;
    const selectedTrain = { ...train, selected_seat_class: seatClass };
    const registrationForm = { ...form };
    let registeredTrain = selectedTrain;
    const registered = await register(key, async () => {
      try {
        const created = await onComplete({ form: registrationForm, train: selectedTrain, selectedTrains: [selectedTrain] });
        return created;
      } catch (error) {
        if (!isExpiredWatchCreateConflict(error)) throw error;
        let refreshedTrains;
        try {
          refreshedTrains = await refreshProviderSeatStatus(train.provider, registrationForm);
        } catch {
          const message = "좌석 상태를 다시 확인하지 못해 등록하지 않았습니다. 잠시 후 다시 조회해 주세요.";
          setSubmitError(message);
          throw new Error(message);
        }
        const recovery = recoverRefreshedRegistrationTrain(train, refreshedTrains, seatClass);
        if (!recovery.ok) {
          setSubmitError(recovery.message);
          throw new Error(recovery.message);
        }
        registeredTrain = { ...recovery.train, selected_seat_class: seatClass };
        const created = await onComplete({
          form: registrationForm,
          train: registeredTrain,
          selectedTrains: [registeredTrain],
        });
        return created;
      }
    });
    if (registered) {
      setSubmitError("");
    }
  };
  const chooseTrainSeatRef = useRef(chooseTrainSeatImplementation);
  chooseTrainSeatRef.current = chooseTrainSeatImplementation;
  const chooseTrainSeat = useMemo(
    () => (id, seatClass) => chooseTrainSeatRef.current(id, seatClass),
    [],
  );
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
  const hasActiveRegistration = successCount > 0 || trains.some((train) => (
    train.seat_classes.some((seat) => (
      registrationStateForSeat(train, seat.seat_class).status === "active"
    ))
  ));
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

export function Settings({ channels, demo, browserPushState = { support: "checking", permission: "default", subscribed: false }, providerAccounts = [], providerRuntimeStatuses = [], providerAccountsLoading = false, pendingProviderAccount = null, uiPreferences, savingUiPreferences, onSaveUiPreferences, onSaveChannel, onToggleChannel, onTestChannel, onConnectWebPush, onSaveProviderAccount = async () => undefined, onDeleteProviderAccount = async () => undefined, onSectionChange = () => undefined, onLogout, initialSection = "notifications" }) {
  const [section, setSection] = useState(initialSection);
  const [editingKind, setEditingKind] = useState("");
  const [draft, setDraft] = useState({ name: "", token: "", chatId: "", url: "", authorization: "" });
  const [pendingAction, setPendingAction] = useState("");
  const sections = [
    { id: "rail-accounts", label: "철도 계정", icon: User },
    { id: "notifications", label: "알림 채널", icon: Bell },
    { id: "display", label: "화면 동작", icon: Clock },
    { id: "security", label: "보안", icon: LockKey },
    { id: "system", label: "로그·진행 상태", icon: WifiHigh },
  ];
  const configuredByKind = Object.fromEntries(channels.map((channel) => [channel.kind, channel]));
  const allOptions = [
    ...notificationOptions,
    { id: "generic_webhook", label: "범용 Webhook", helper: "HTTPS JSON endpoint", icon: GlobeSimple },
  ];
  const beginConfigure = async (kind) => {
    if (kind === "web_push") {
      setPendingAction("connect:web_push");
      try {
        await onConnectWebPush();
      } finally {
        setPendingAction("");
      }
      return;
    }
    const existing = configuredByKind[kind];
    setDraft({ name: existing?.name ?? "", token: "", chatId: "", url: "", authorization: "" });
    setEditingKind(kind);
  };
  const saveDraft = async () => {
    const config = editingKind === "telegram"
      ? { bot_token: draft.token, chat_id: draft.chatId }
      : editingKind === "generic_webhook"
        ? { url: draft.url, ...(draft.authorization ? { authorization: draft.authorization } : {}) }
        : { url: draft.url };
    setPendingAction(`save:${editingKind}`);
    try {
      await onSaveChannel(editingKind, draft.name || allOptions.find((item) => item.id === editingKind)?.label, config);
      setEditingKind("");
    } catch {
      // App owns user-facing API errors through the global toast.
    } finally {
      setPendingAction("");
    }
  };
  const toggleOption = async (kind, channel, nextEnabled) => {
    if (!channel) {
      await beginConfigure(kind);
      return;
    }
    setPendingAction(`toggle:${kind}`);
    try {
      await onToggleChannel(channel, nextEnabled);
    } finally {
      setPendingAction("");
    }
  };
  const testOption = async (kind, channel) => {
    setPendingAction(`test:${kind}`);
    try {
      await onTestChannel(channel);
    } finally {
      setPendingAction("");
    }
  };
  const selectSection = (nextSection) => {
    setSection(nextSection);
    onSectionChange(nextSection);
  };

  return (
    <div className="page settings-page">
      <PageHeader title="설정" helper="개인 서비스 연결과 운영 상태를 관리합니다." />
      <div className="settings-layout">
        <nav className="settings-nav" aria-label="설정 메뉴">{sections.map(({ id, label, icon: Icon }) => <button key={id} type="button" className={section === id ? "is-active" : ""} onClick={() => selectSection(id)}><Icon size={21} />{label}<ArrowRight size={17} /></button>)}</nav>
        <section className="settings-panel">
          {section === "rail-accounts" && <><div className="panel-heading"><h2>철도 계정</h2><p>새 좌석 가용성 에피소드마다 결제 직전까지 자동 예매할 계정을 연결합니다. 자동 결제는 하지 않습니다.</p></div><ProviderAccountSettings accounts={providerAccounts} runtimeStatuses={providerRuntimeStatuses} loading={providerAccountsLoading} pendingProvider={pendingProviderAccount} onSave={onSaveProviderAccount} onDelete={onDeleteProviderAccount} /></>}
          {section === "notifications" && <><div className="panel-heading"><h2>알림 채널</h2><p>여러 채널을 함께 켜 중요한 알림 누락을 줄입니다.</p></div><div className="settings-list">{allOptions.map(({ id, label, helper, icon: Icon }) => {
            const channel = configuredByKind[id];
            const isPending = pendingAction.endsWith(`:${id}`);
            const isWebPush = id === "web_push";
            const webPushReady = browserPushState.support === "supported"
              && browserPushState.permission === "granted"
              && browserPushState.subscribed;
            const checked = isWebPush
              ? Boolean(channel?.enabled && (browserPushState.support === "checking" || webPushReady))
              : Boolean(channel?.enabled);
            const webPushDetail = browserPushState.support === "unsupported"
              ? "이 브라우저는 OS 알림을 지원하지 않음"
              : browserPushState.support === "insecure"
                ? "HTTPS 또는 localhost 접속 필요"
                : browserPushState.permission === "denied"
                  ? "브라우저 사이트 설정에서 알림 권한이 차단됨"
                  : channel?.enabled && browserPushState.subscribed
                    ? "이 기기의 OS 알림 사용 중"
                    : channel?.enabled
                      ? "이 기기 구독이 없어 다시 연결 필요"
                      : channel
                        ? "이 기기의 OS 알림 꺼짐"
                        : helper;
            const detail = isPending
              ? "처리 중…"
              : isWebPush
                ? webPushDetail
                : channel
                  ? `${channel.name} · ${channel.enabled ? "사용 중" : "꺼짐"}`
                  : helper;
            return <div key={id} className="setting-row" aria-busy={isPending || undefined}><Icon size={25} /><div><strong>{label}</strong><span>{detail}</span>{isWebPush && <small className="setting-row-note">허용하면 브라우저를 닫아도 운영체제 알림 영역으로 전달됩니다. iOS는 홈 화면에 설치한 PWA에서 지원됩니다.</small>}</div>{channel ? <button type="button" className="button button-ghost compact" disabled={isPending || (isWebPush && !webPushReady)} aria-label={`${label} 시험 알림 보내기`} onClick={() => testOption(id, channel)}>시험</button> : <button type="button" className="button button-ghost compact" disabled={isPending} aria-label={`${label} 연결 설정 열기`} onClick={() => beginConfigure(id)}>{isPending ? "연결 중…" : "설정"}</button>}<label className="switch"><input type="checkbox" disabled={isPending || (isWebPush && ["unsupported", "insecure"].includes(browserPushState.support))} aria-label={`${label} ${checked ? "끄기" : "켜기"}`} checked={checked} onChange={(event) => toggleOption(id, channel, event.target.checked)} /><span /></label></div>;
          })}</div>{editingKind && <div className="channel-editor"><h3>{allOptions.find((item) => item.id === editingKind)?.label} 연결</h3><Field label="표시 이름"><input name="railwait-notification-name" autoComplete="off" value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} placeholder="내 알림" /></Field>{editingKind === "telegram" ? <div className="form-grid"><Field label="Bot token"><input type="password" name="railwait-telegram-bot-token" value={draft.token} onChange={(event) => setDraft({ ...draft, token: event.target.value })} autoComplete="new-password" data-lpignore="true" /></Field><Field label="Chat ID"><input name="railwait-telegram-chat-id" autoComplete="off" value={draft.chatId} onChange={(event) => setDraft({ ...draft, chatId: event.target.value })} /></Field></div> : <><Field label="HTTPS URL"><input type="url" name={`railwait-${editingKind}-url`} autoComplete="off" value={draft.url} onChange={(event) => setDraft({ ...draft, url: event.target.value })} placeholder="https://" /></Field>{editingKind === "generic_webhook" && <Field label="Authorization (선택)"><input type="password" name="railwait-webhook-authorization" value={draft.authorization} onChange={(event) => setDraft({ ...draft, authorization: event.target.value })} autoComplete="new-password" data-lpignore="true" /></Field>}</>}<div className="editor-actions"><button type="button" className="button button-ghost" disabled={pendingAction.startsWith("save:")} onClick={() => setEditingKind("")}>취소</button><button type="button" className="button button-primary" disabled={pendingAction.startsWith("save:")} onClick={saveDraft}>{pendingAction.startsWith("save:") ? "저장 중…" : "저장"}</button></div></div>}</>}
          {section === "display" && <><div className="panel-heading"><h2>화면 동작</h2><p>화면 표시와 백엔드 좌석 관측 간격을 관리합니다.</p></div><TimetableRefreshSettings preferences={uiPreferences} saving={savingUiPreferences} onSave={onSaveUiPreferences} /></>}
          {section === "security" && <><div className="panel-heading"><h2>보안</h2><p>관리자 한 명만 사용하며 공개 가입 기능은 없습니다.</p></div><div className="security-card"><ShieldCheck size={34} weight="fill" /><div><strong>관리자 ID·비밀번호 로그인 활성화</strong><span>비밀번호는 Argon2id 단방향 해시로 저장됩니다.</span></div><StatusPill status="watching">보호됨</StatusPill></div><div className="security-card"><GlobeSimple size={34} /><div><strong>접속 경로</strong><span>Tailscale 우선 · 공개 도메인 선택 지원</span></div></div><button type="button" className="button button-outline logout-button" onClick={onLogout}>이 기기에서 로그아웃</button></>}
          {section === "system" && <SystemStatusDashboard demo={demo} />}
        </section>
      </div>
    </div>
  );
}

export function App() {
  const [activeView, setActiveView] = useState("home");
  const [settingsInitialSection, setSettingsInitialSection] = useState("notifications");
  const [settingsActiveSection, setSettingsActiveSection] = useState("notifications");
  const { auth, markAuthenticated, markUnauthenticated, retryAuthStatus } = useAuthState();
  const [watches, setWatches] = useState(DEMO_MODE ? initialWatches : []);
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
  const [reservationPolicyUpdatingIds, setReservationPolicyUpdatingIds] = useState(() => new Set());
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
  const [watchRefreshState, setWatchRefreshState] = useState({
    isRefreshing: false,
    lastRefreshedAt: null,
  });
  const watchesRef = useRef(watches);
  const pendingLiveReservationEventsRef = useRef([]);
  const watchReloadCoordinatorRef = useRef(null);
  const reservationPolicyMutationGuardRef = useRef(null);
  if (reservationPolicyMutationGuardRef.current === null) {
    reservationPolicyMutationGuardRef.current = createReservationPolicyMutationGuard();
  }
  const watchRefreshAnimationRef = useRef({
    generation: 0,
    startedAt: 0,
    stopTimerId: null,
  });

  const commitWatches = (updater) => {
    setWatches((current) => {
      const next = typeof updater === "function" ? updater(current) : updater;
      watchesRef.current = next;
      return next;
    });
  };

  const refreshProviderRuntimeStatuses = useCallback(async () => {
    if (auth.demo) {
      setProviderRuntimeStatuses(demoProviderRuntimeStatuses);
      return;
    }
    const statuses = await fetchProviderRuntimeStatuses();
    setProviderRuntimeStatuses(statuses);
  }, [auth.demo]);

  const reloadWatches = async () => {
    if (auth.demo) return;
    const reservationPolicyMutationSnapshot = reservationPolicyMutationGuardRef.current.snapshot();
    const refreshAnimation = watchRefreshAnimationRef.current;
    refreshAnimation.generation += 1;
    const refreshGeneration = refreshAnimation.generation;
    refreshAnimation.startedAt = performance.now();
    if (refreshAnimation.stopTimerId !== null) {
      window.clearTimeout(refreshAnimation.stopTimerId);
      refreshAnimation.stopTimerId = null;
    }
    setWatchRefreshState((current) => ({ ...current, isRefreshing: true }));
    try {
      const watchItems = await fetchWatches();
      if (!reservationPolicyMutationGuardRef.current.isCurrent(
        reservationPolicyMutationSnapshot,
      )) {
        // A policy PATCH crossed this GET. Its older snapshot must not overwrite
        // the newer ticket-level choice; the mutation schedules a fresh reload.
        return;
      }
      const previous = watchesRef.current;
      const transitions = detectSeatFoundTransitions(previous, watchItems);
      const availabilityLosses = detectSeatAvailabilityLostTransitions(previous, watchItems);
      const actionTransitions = detectWatchActionTransitions(previous, watchItems);
      const pendingLiveEvents = pendingLiveReservationEventsRef.current;
      pendingLiveReservationEventsRef.current = [];
      const liveReservationNotices = pendingLiveEvents.flatMap((event) => {
        const notice = buildLiveReservationNotice(event, watchItems);
        return notice ? [notice] : [];
      });
      const reconciled = reconcileWatchSnapshots(previous, watchItems);
      watchesRef.current = reconciled;
      setWatches(reconciled);
      setWatchRefreshState((current) => ({ ...current, lastRefreshedAt: new Date() }));
      const providerAuthTransitions = actionTransitions.filter((item) => (
        item.status === "auth_required" || item.status === "authentication_recovered"
      ));
      if (providerAuthTransitions.length > 0 && !auth.demo) {
        setProviderAccountsLoaded(false);
        void fetchProviderAccounts().then((items) => {
          setProviderAccounts(items);
          setProviderAccountsLoaded(true);
          return refreshProviderRuntimeStatuses();
        }).catch(() => {
          // Keep the activity row neutral until a no-store account read succeeds.
        });
      }
      const liveNoticeSubjects = new Set(
        liveReservationNotices.map((notice) => notice.subjectKey),
      );
      const lifecycleNotices = actionTransitions
        .filter((item) => !liveNoticeSubjects.has(`watch:${item.id}`))
        .map(buildWatchActionToast);
      pushNotifications([
        ...transitions.map(buildSeatFoundToast),
        ...lifecycleNotices,
        ...availabilityLosses.map(buildAvailabilityLostToast),
        ...liveReservationNotices,
      ]);
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        markUnauthenticated();
      }
      throw error;
    } finally {
      const delay = delayUntilRefreshRotationEnds(
        refreshAnimation.startedAt,
        performance.now(),
      );
      refreshAnimation.stopTimerId = window.setTimeout(() => {
        if (watchRefreshAnimationRef.current.generation !== refreshGeneration) return;
        watchRefreshAnimationRef.current.stopTimerId = null;
        setWatchRefreshState((current) => ({ ...current, isRefreshing: false }));
      }, delay);
    }
  };

  const requestWatchesRefresh = () => {
    watchReloadCoordinatorRef.current?.request();
  };

  useEffect(() => {
    if (!auth.authenticated || auth.demo) return undefined;
    const coordinator = createLiveDataReloadCoordinator(reloadWatches, 50, {
      pollIntervalMs: uiPreferences.timetableRefreshIntervalSeconds * 1_000,
    });
    watchReloadCoordinatorRef.current = coordinator;
    const unsubscribe = subscribeToEvents(
      (event) => {
        const liveNotice = buildLiveReservationNotice(event, watchesRef.current);
        if (liveNotice) {
          pushNotifications([liveNotice]);
        } else if ([
          "watch.reservation_attempted",
          "watch.reservation_result",
          "watch.reservation_result_requires_manual_check",
          "watch.payment_hold_ended_monitoring_resumed",
          "watch.payment_hold_ended_one_off_expired",
        ].includes(event?.event_type)) {
          pendingLiveReservationEventsRef.current.push(event);
        }
        coordinator.request();
      },
      () => {},
    );
    coordinator.start();
    return () => {
      if (watchReloadCoordinatorRef.current === coordinator) {
        watchReloadCoordinatorRef.current = null;
      }
      coordinator.dispose();
      unsubscribe();
    };
  }, [auth.authenticated, auth.demo, uiPreferences.timetableRefreshIntervalSeconds, refreshProviderRuntimeStatuses]);

  useEffect(() => () => {
    const refreshAnimation = watchRefreshAnimationRef.current;
    refreshAnimation.generation += 1;
    if (refreshAnimation.stopTimerId !== null) {
      window.clearTimeout(refreshAnimation.stopTimerId);
      refreshAnimation.stopTimerId = null;
    }
  }, []);

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

  const pauseWatch = async (id) => {
    try {
      if (auth.demo) {
        commitWatches((items) => items.map((watch) => watch.id === id ? { ...watch, status: "paused", statusLabel: "일시정지" } : watch));
      } else {
        const updated = await pauseWatchRequest(id);
        commitWatches((items) => items.map((watch) => watch.id === id ? updated : watch));
      }
      setToast("대기를 일시정지했습니다.");
    } catch (error) {
      setToast(error.message);
    }
  };

  const resumeWatch = async (id) => {
    try {
      if (auth.demo) {
        commitWatches((items) => items.map((watch) => watch.id === id ? { ...watch, status: "watching", statusLabel: "감시 중" } : watch));
      } else {
        const updated = await startWatch(id);
        commitWatches((items) => items.map((watch) => watch.id === id ? updated : watch));
      }
      setToast("대기를 재개했습니다.");
    } catch (error) {
      setToast(error.message);
    }
  };

  const cancelWatchItem = async (id) => {
    try {
      let updated;
      if (auth.demo) {
        updated = watches.find((watch) => watch.id === id);
        updated = updated ? { ...updated, status: "expired", statusLabel: "만료" } : { id, status: "expired", statusLabel: "만료" };
        commitWatches((items) => items.map((watch) => watch.id === id ? updated : watch));
      } else {
        updated = await cancelWatch(id);
        commitWatches((items) => items.map((watch) => watch.id === id ? updated : watch));
      }
      setToast("대기를 취소했습니다.");
      return updated;
    } catch (error) {
      setToast(error.message);
      throw error;
    }
  };

  const changeWatchReservationPolicy = async (id, reservationPolicy) => {
    reservationPolicyMutationGuardRef.current.begin();
    setReservationPolicyUpdatingIds((items) => new Set(items).add(id));
    try {
      let updated;
      if (auth.demo) {
        updated = watches.find((watch) => watch.id === id);
        updated = updated ? { ...updated, reservationPolicy } : null;
      } else {
        updated = await updateWatch(id, { reservation_policy: reservationPolicy });
      }
      if (updated) {
        commitWatches((items) => items.map((watch) => watch.id === id ? updated : watch));
      }
      setToast(reservationPolicy === "reserve_once_before_payment"
        ? "좌석 재발견마다 자동 예매하도록 변경했습니다. 같은 좌석 가용성 에피소드에서는 중복 요청하지 않으며 결제는 직접 진행합니다."
        : "자동 예매를 끄고 좌석 감시와 알림만 유지합니다.");
    } catch (error) {
      setToast(error instanceof Error ? error.message : "대기 실행 방식을 변경하지 못했습니다.");
    } finally {
      reservationPolicyMutationGuardRef.current.end();
      requestWatchesRefresh();
      setReservationPolicyUpdatingIds((items) => {
        const next = new Set(items);
        next.delete(id);
        return next;
      });
    }
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

  const deleteWatchRecord = async (id) => {
    try {
      if (!auth.demo) await deleteWatch(id);
      commitWatches((items) => items.filter((watch) => watch.id !== id));
      setToast("대기 기록을 삭제했습니다.");
    } catch (error) {
      setToast(error.message);
    }
  };

  const completeWizard = async ({ form, selectedTrains }) => {
    let createdWatches;
    if (auth.demo) {
      createdWatches = selectedTrains.map((item, index) => ({
        id: `watch-${Date.now()}-${item.id}-${item.selected_seat_class || "any"}-${index}`,
        provider: item.provider,
        train: item.name,
        route: `${form.origin} → ${form.destination}`,
        origin: form.origin,
        destination: form.destination,
        departure: item.departure,
        arrival: item.arrival,
        date: dateLabel(form.date),
        status: "watching",
        statusLabel: "감시 중",
        seat_class: item.selected_seat_class || "any",
        seatClass: item.selected_seat_class || "any",
        seatClassLabel: seatClassNames[item.selected_seat_class || "any"],
        seatEvidenceLabel: `${seatClassNames[item.selected_seat_class || "any"]} · 데모 좌석 상태`,
        official_booking_url: item.official_booking_url,
        candidates: [{
          train_number: item.train_number ?? item.name,
          departure_at: item.departure_at,
          seat_class: item.selected_seat_class || "any",
          priority: 1,
        }],
      }));
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

  const saveChannel = async (kind, name, config) => {
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
        setChannels((items) => [{ id: "demo-web-push", kind: "web_push", name: "이 브라우저", enabled: true }, ...items.filter((item) => item.kind !== "web_push")]);
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
        {activeView === "settings" && <Settings channels={channels} demo={auth.demo} browserPushState={browserPushState} providerAccounts={providerAccounts} providerRuntimeStatuses={providerRuntimeStatuses} providerAccountsLoading={providerAccountsLoading} pendingProviderAccount={pendingProviderAccount} uiPreferences={uiPreferences} savingUiPreferences={savingUiPreferences} onSaveUiPreferences={saveUiPreferences} onSaveChannel={saveChannel} onToggleChannel={toggleChannel} onTestChannel={testChannel} onConnectWebPush={connectWebPushChannel} onSaveProviderAccount={saveRailProviderAccount} onDeleteProviderAccount={removeRailProviderAccount} onSectionChange={setSettingsActiveSection} onLogout={signOut} initialSection={settingsInitialSection} />}
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
