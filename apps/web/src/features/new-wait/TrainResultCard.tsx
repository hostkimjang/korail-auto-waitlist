import { memo, useEffect, useState, type ComponentType } from "react";
import { ArrowRight } from "@phosphor-icons/react";

import type { Timetable, TimetableSource } from "../../api/timetables";
import type { NormalizedSeatClass } from "../../domain/seatClasses";
import { seatObservationReasonMeta } from "../../domain/seatDiagnostics";
import { hasObservedSeatEvidence } from "../../domain/seatEvidence";
import {
  SeatRegistrationCancelButton,
  SeatRegistrationStatus,
  TrainRegistrationBadge,
} from "./RegistrationStateVisuals";
import type {
  SeatClass,
  WatchRegistrationState,
} from "./useInstantWatchRegistration";

type RegistrationBySeat = Partial<Record<SeatClass, WatchRegistrationState>>;
interface TrainResultHandoffFreshness {
  ttl_ms?: number;
  received_monotonic_ms?: number;
}

interface TrainResultHandoffProvenance {
  kind?: string;
  source?: string;
  observed_at?: string;
  fresh_until?: string;
  client_freshness?: TrainResultHandoffFreshness;
}

interface TrainResultHandoffSeat {
  seat_class: string;
  provenance?: TrainResultHandoffProvenance;
}

export interface TrainResultHandoffTrain {
  id: string;
  provider: string;
  name: string;
  origin: string;
  destination: string;
  departure_at?: string;
  departure?: string;
  arrival?: string;
  date?: string;
  seat_classes?: TrainResultHandoffSeat[];
  official_search_url?: string | null;
}

export interface TrainResultOfficialHandoffProps {
  train: TrainResultHandoffTrain;
  selectedSeatClass?: SeatClass | null;
  onCopy: (train: TrainResultHandoffTrain) => Promise<boolean> | boolean;
  triggerLabel?: string;
  actionUrl?: string | null;
  searchUrl?: string | null;
  triggerClassName?: string;
}

export type TrainResultOfficialHandoffComponent = ComponentType<
  TrainResultOfficialHandoffProps
>;

export interface SeatClassPanelProps {
  train: Timetable;
  seat: NormalizedSeatClass;
  registration?: WatchRegistrationState;
  onChooseSeat: (trainId: string, seatClass: SeatClass) => void | Promise<void>;
  automaticReservationEnabled?: boolean;
  officialHandoffComponent?: TrainResultOfficialHandoffComponent;
}

export interface TrainResultCardProps {
  train: Timetable;
  registrationBySeat: RegistrationBySeat;
  onChooseSeat: SeatClassPanelProps["onChooseSeat"];
  automaticReservationEnabled: boolean;
  officialHandoffComponent: TrainResultOfficialHandoffComponent;
}

interface SeatStatusMeta {
  label: string;
  helper: string;
}

export const seatClassNames: Readonly<Record<SeatClass, string>> = {
  standard: "일반실",
  first: "특실",
  any: "좌석 무관",
};
const directlyReservableSeatStatuses: ReadonlySet<string> = new Set([
  "available",
  "limited",
  "standing_plus_seat",
]);
const emptyProvenance: Readonly<Record<string, unknown>> = {};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function optionalFreshness(value: unknown): TrainResultHandoffFreshness | null {
  if (!isRecord(value)) return null;
  const ttlMs = typeof value.ttl_ms === "number" ? value.ttl_ms : null;
  const receivedMonotonicMs = typeof value.received_monotonic_ms === "number"
    ? value.received_monotonic_ms
    : null;
  return {
    ...(ttlMs !== null ? { ttl_ms: ttlMs } : {}),
    ...(receivedMonotonicMs !== null ? { received_monotonic_ms: receivedMonotonicMs } : {}),
  };
}

function officialHandoffProvenance(
  value: Record<string, unknown>,
): TrainResultHandoffProvenance {
  const freshness = optionalFreshness(value.client_freshness);
  return {
    ...(typeof value.kind === "string" ? { kind: value.kind } : {}),
    ...(typeof value.source === "string" ? { source: value.source } : {}),
    ...(typeof value.observed_at === "string" ? { observed_at: value.observed_at } : {}),
    ...(typeof value.fresh_until === "string" ? { fresh_until: value.fresh_until } : {}),
    ...(freshness !== null ? { client_freshness: freshness } : {}),
  };
}

function evidenceSeat(seat: NormalizedSeatClass): TrainResultHandoffSeat {
  const provenance = isRecord(seat.provenance) ? seat.provenance : emptyProvenance;
  return {
    seat_class: seat.seat_class,
    provenance: officialHandoffProvenance(provenance),
  };
}

function officialHandoffTrain(train: Timetable): TrainResultHandoffTrain {
  return {
    id: train.id,
    provider: train.provider,
    name: train.name,
    origin: train.origin,
    destination: train.destination,
    departure_at: train.departure_at,
    departure: train.departure,
    arrival: train.arrival,
    seat_classes: Array.isArray(train.seat_classes) ? train.seat_classes.map(evidenceSeat) : [],
    official_search_url: train.official_search_url,
  };
}

function fareLabel(train: Timetable): string {
  if (typeof train.adult_fare !== "number" || !Number.isFinite(train.adult_fare)) {
    return "운임 확인 필요";
  }
  return `성인 ${new Intl.NumberFormat("ko-KR").format(train.adult_fare)}원`;
}

function displayTrainName(value: string): string {
  return value
    .replace(/^0+(?=\d+$)/, "")
    .replace(/(\s)0+(?=\d+$)/, "$1");
}

function timetableRetrievedLabel(value: string | null): string {
  if (!value) return "시간표 업데이트 시각 미제공";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "시간표 업데이트 시각 미제공";
  return `시간표 업데이트 ${new Intl.DateTimeFormat("ko-KR", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: "Asia/Seoul",
  }).format(date)}`;
}

function timetableSourceLabel(source: TimetableSource): string {
  if (source === "mock") return "데모 시간표";
  if (source === "unknown") return "시간표 출처 미확인";
  return "공식 시간표";
}

function seatStatusMeta(status: string, notObservedReason: unknown = null): SeatStatusMeta {
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

function seatFareLabel(seat: NormalizedSeatClass): string {
  return typeof seat.fare === "number" && Number.isFinite(seat.fare)
    ? `${new Intl.NumberFormat("ko-KR").format(seat.fare)}원`
    : "운임 확인 필요";
}

function seatSourceLabel(seat: NormalizedSeatClass): string {
  const provenance = isRecord(seat.provenance) ? seat.provenance : emptyProvenance;
  if (provenance.kind === "mock") return "데모 좌석 상태";
  if (provenance.kind === "official_provider") {
    const observedAt = typeof provenance.observed_at === "string"
      ? provenance.observed_at
      : null;
    return `공식 좌석 관측 · ${timetableRetrievedLabel(observedAt).replace("시간표 업데이트 ", "")}`;
  }
  if (provenance.kind === "official_page_browser_companion") {
    const observedAt = typeof provenance.observed_at === "string"
      ? provenance.observed_at
      : null;
    return `공식 화면 동기화 · ${timetableRetrievedLabel(observedAt).replace("시간표 업데이트 ", "")}`;
  }
  if (provenance.kind === "user_confirmed_official_page") {
    const observedAt = typeof provenance.observed_at === "string"
      ? provenance.observed_at
      : null;
    return `공식 페이지에서 직접 확인 · ${timetableRetrievedLabel(observedAt).replace("시간표 업데이트 ", "")}`;
  }
  return "좌석 정보 미확인";
}

function userConfirmationRemainingMs(provenance: Record<string, unknown>): number | null {
  const kind = provenance.kind;
  if (kind !== "official_page_browser_companion" && kind !== "user_confirmed_official_page") {
    return null;
  }
  const normalizedSeat = {
    seat_class: "standard",
    provenance: officialHandoffProvenance(provenance),
  };
  if (!hasObservedSeatEvidence(normalizedSeat)) return 0;
  const freshness = normalizedSeat.provenance?.client_freshness;
  if (
    typeof freshness?.ttl_ms !== "number"
    || typeof freshness.received_monotonic_ms !== "number"
  ) return 0;
  return freshness.ttl_ms - (performance.now() - freshness.received_monotonic_ms);
}

export async function copyTrainJourney(train: TrainResultHandoffTrain): Promise<boolean> {
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
  officialHandoffComponent: OfficialHandoffComponent,
}: SeatClassPanelProps) {
  const [, setFreshnessVersion] = useState(0);
  const provenance = isRecord(seat.provenance) ? seat.provenance : emptyProvenance;
  useEffect(() => {
    const remaining = userConfirmationRemainingMs(provenance);
    if (remaining === null || remaining <= 0) return undefined;
    const timer = window.setTimeout(
      () => setFreshnessVersion((value) => value + 1),
      Math.min(remaining + 16, 2_147_483_647),
    );
    return () => window.clearTimeout(timer);
  }, [provenance]);
  const observed = hasObservedSeatEvidence(evidenceSeat(seat));
  const displayStatus = observed ? seat.status : "unknown";
  const meta = seatStatusMeta(displayStatus, observed ? null : provenance.reason);
  const selectable = ![
    "not_offered",
    "departed",
    "out_of_service",
    "reservation_completed",
  ].includes(seat.status);
  const actions = Array.isArray(seat.actions) ? seat.actions : [];
  const isDemoSeat = provenance.kind === "mock";
  const canRegisterDirectReservation = automaticReservationEnabled
    && directlyReservableSeatStatuses.has(seat.status);
  const canAddToWatch = observed
    && selectable
    && (isDemoSeat
      || canRegisterDirectReservation
      || ["sold_out", "waitlist_available"].includes(seat.status))
    && actions.some((action) => action.kind === "add_to_watch");
  const officialActionKind = seat.status === "waitlist_available"
    ? "official_waitlist"
    : ["available", "limited", "standing_plus_seat"].includes(seat.status)
      ? "official_check"
      : null;
  const officialAction = officialActionKind
    ? actions.find((action) => (
      action.kind === officialActionKind && typeof action.url === "string"
    ))
    : null;
  const officialActionLabel = officialActionKind === "official_waitlist"
    ? "공식 예약대기"
    : "공식 예매";
  const officialActionClassName = officialActionKind === "official_waitlist"
    ? "button button-primary compact seat-action-official-waitlist"
    : "button button-primary compact seat-action-booking";
  const showOfficialAction = officialAction !== null
    && officialAction !== undefined
    && !(canRegisterDirectReservation && canAddToWatch);
  const registrationStatus = registration?.status ?? "idle";
  const registered = registrationStatus === "active";
  const registering = registrationStatus === "pending";
  const cancelling = registrationStatus === "cancelling";
  const registrationPolicy = registration?.status === "active"
    || registration?.status === "cancelling"
    ? registration.reservationPolicy
    : undefined;
  const registrationMessage = registration?.status === "active"
    || registration?.status === "error"
    ? registration.message
    : undefined;
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
  const handoffTrain = officialHandoffTrain(train);
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
      <div className="seat-class-meta">
        <span className="seat-fare">{seatFareLabel(seat)}</span>
        <span className="seat-source">{seatSourceLabel(seat)}</span>
      </div>
      {hasActiveRegistration && <SeatRegistrationStatus
        cancelling={cancelling}
        {...(registrationPolicy ? { reservationPolicy: registrationPolicy } : {})}
      />}
      <div className="seat-class-actions">
        {showOfficialAction && OfficialHandoffComponent && <OfficialHandoffComponent
          train={handoffTrain}
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
      {seat.registration_evidence_error && (
        <p className="seat-registration-evidence-error" role="note">
          {seat.registration_evidence_error}
        </p>
      )}
      {registrationMessage && (
        <p className="seat-registration-error" role="alert">{registrationMessage}</p>
      )}
    </section>
  );
}

export const TrainResultCard = memo(function TrainResultCard({
  train,
  registrationBySeat,
  onChooseSeat,
  automaticReservationEnabled,
  officialHandoffComponent,
}: TrainResultCardProps) {
  const titleId = `train-title-${train.id.replace(/[^a-zA-Z0-9_-]/g, "-")}`;
  const registrationCount = train.seat_classes.filter((seat) => (
    ["active", "cancelling"].includes(registrationBySeat[seat.seat_class]?.status ?? "idle")
  )).length;
  const registered = registrationCount > 0;
  return (
    <article
      className={registered ? "train-result-card is-selected has-active-registration" : "train-result-card"}
      aria-labelledby={titleId}
      data-registration-count={registrationCount}
    >
      <header className="train-result-header">
        <span className={`provider-chip ${train.provider === "SRT" ? "provider-srt" : "provider-korail"}`}>
          {train.provider}
        </span>
        <div>
          <strong id={titleId} aria-label={train.name}>{displayTrainName(train.name)}</strong>
          <span>{train.train_type ?? train.provider} · {train.duration}</span>
        </div>
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
        <span>{timetableSourceLabel(train.timetable_source)}</span>
        <span>
          {train.timetable_source === "mock"
            ? timetableRetrievedLabel(train.timetable_retrieved_at)
              .replace("시간표 업데이트", "데모 기준 시각")
            : timetableRetrievedLabel(train.timetable_retrieved_at)}
        </span>
      </div>
      <div className="seat-class-grid">
        {train.seat_classes.map((seat) => <SeatClassPanel
          key={seat.seat_class}
          train={train}
          seat={seat}
          {...(registrationBySeat[seat.seat_class]
            ? { registration: registrationBySeat[seat.seat_class] }
            : {})}
          onChooseSeat={onChooseSeat}
          automaticReservationEnabled={automaticReservationEnabled}
          officialHandoffComponent={officialHandoffComponent}
        />)}
      </div>
    </article>
  );
}, (previous, next) => (
  previous.train === next.train
  && previous.onChooseSeat === next.onChooseSeat
  && previous.officialHandoffComponent === next.officialHandoffComponent
  && previous.automaticReservationEnabled === next.automaticReservationEnabled
  && previous.registrationBySeat.standard === next.registrationBySeat.standard
  && previous.registrationBySeat.first === next.registrationBySeat.first
  && previous.registrationBySeat.any === next.registrationBySeat.any
));
