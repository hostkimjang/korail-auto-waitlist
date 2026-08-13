import {
  ArrowClockwise,
  ChartBar,
  Clock,
  Database,
  Info,
  ListBullets,
  Pulse,
  WarningCircle,
} from "@phosphor-icons/react";
import {
  isOperationsSummaryEmpty,
  type OperationsEntry,
  type OperationsRate,
  type OperationsHealthStatus,
  type OperationsServiceState,
  type OperationsSourceFreshness,
  type ProviderCircuit,
} from "../../api/operationsSummaryContract";
import {
  useOperationsSummary,
  type OperationsSummaryLoader,
} from "./useOperationsSummary";
import {
  type SeatStatusSource,
  type SeatStatusSourceCause,
} from "../../api/seatStatusSourcesContract";
import {
  useSeatStatusSources,
  type SeatStatusSourcesLoader,
} from "./useSeatStatusSources";

export interface SystemStatusDashboardProps {
  demo?: boolean;
  loader?: OperationsSummaryLoader;
  seatStatusSourcesLoader?: SeatStatusSourcesLoader;
}

const serviceLabels: Record<string, string> = {
  api: "API",
  database: "데이터베이스",
  worker: "작업 처리",
  scheduler: "일정 실행",
};
const sourceLabels: Record<string, string> = {
  seat_observations: "좌석 관측 집계",
  watch_transition_history: "상태 변경 집계",
  reservation_attempts: "예약 시도 집계",
  notification_delivery: "알림 전달 집계",
  provider_circuits: "운영사 보호 회로",
  station_catalog: "역 목록 갱신",
};
const providerLabels: Record<string, string> = { KORAIL: "KORAIL", SRT: "SRT", MOCK: "모의" };
const timestampBasisLabels: Record<string, string> = {
  observed_at: "관측 시각 기준",
  started_at: "시작 시각 기준",
  created_at: "생성 시각 기준",
  processed_at: "처리 완료 시각 기준",
  updated_at: "갱신 시각 기준",
  retrieved_at: "수집 시각 기준",
};

const statusLabels: Record<OperationsHealthStatus, string> = {
  healthy: "정상",
  fresh: "최신",
  stale: "지연",
  unknown: "확인 불가",
};

const circuitLabels: Record<ProviderCircuit["state"], string> = {
  closed: "정상",
  open: "자동 중단",
  half_open: "복구 확인 중",
  manual_hold: "수동 중단",
  unknown: "확인 불가",
};
const seatStatusSourceLabels: Record<SeatStatusSource["source"], string> = {
  korail_browser: "KORAIL 브라우저 좌석 조회",
  srt_live: "SRT 실시간 좌석 조회",
};
const seatStatusSourceStateLabels: Record<SeatStatusSource["state"], string> = {
  ready: "조회 대기 없음",
  cooldown: "조회 대기 중",
  unknown: "확인 불가",
};
const seatStatusSourceCauseLabels: Record<Exclude<SeatStatusSourceCause, null>, string> = {
  provider_access_restricted: "공식 조회 제한",
  source_unavailable: "일시 불가",
};

const kindLabels: Record<string, string> = {
  notification_delivery: "알림 전달",
  watch_transition: "상태 변경",
  seat_observation: "좌석 조회",
  reservation_attempt: "예약 처리",
  provider_circuit: "운영사 요청 보호",
};
const eventStatusLabels: Record<string, string> = {
  pending: "대기 중",
  sent: "전달됨",
  succeeded: "성공",
  success: "성공",
  draft: "초안",
  scheduled: "대기 등록",
  failed: "실패",
  watching: "감시 중",
  official_waitlist: "공식 예약대기",
  seat_found: "좌석 발견",
  reserving: "예약 진행",
  paused: "일시정지",
  expired: "만료",
  payment_required: "결제 필요",
  reserved: "임시 예약",
  not_available: "좌석 확보 실패",
  provider_blocked: "운영사 제한",
  completed: "완료",
  available: "예약 가능 관측",
  limited: "잔여석 부족 관측",
  standing_plus_seat: "입석+좌석 관측",
  not_enough_seats: "요청 인원 좌석 부족",
  sold_out: "매진 관측",
  waitlist_available: "예약대기 가능 관측",
  reservation_completed: "예약 완료 관측",
  not_offered: "미판매 관측",
  departed: "출발 완료 관측",
  out_of_service: "운행 종료 관측",
  stale: "관측 유효기간 만료",
  unavailable: "매진 관측",
  error: "관측 오류",
  auth_required: "로그인 확인 필요",
  cooldown: "요청 제한",
  closed: "정상",
  open: "자동 중단",
  half_open: "복구 확인 중",
  manual_hold: "수동 중단",
  unknown: "확인 필요",
};
const watchStatusLabels: Record<string, string> = {
  draft: "초안",
  scheduled: "대기 등록",
  watching: "감시 중",
  official_waitlist: "공식 예약대기",
  seat_found: "좌석 발견",
  reserving: "예약 진행",
  payment_required: "결제 필요",
  completed: "완료",
  paused: "일시정지",
  cooldown: "요청 제한",
  auth_required: "로그인 필요",
  expired: "만료",
  failed: "실패",
};
const errorCategoryLabels: Record<string, string> = {
  timeout: "시간 초과",
  schema_mismatch: "응답 형식 오류",
  provider_unavailable: "운영사 연결 불가",
  partial_failure: "일부 처리 실패",
  unknown: "원인 확인 필요",
};
const entryLevelLabels: Record<OperationsEntry["level"], string> = {
  info: "정보",
  warning: "주의",
  error: "오류",
};
const seatClassLabels: Record<NonNullable<OperationsEntry["seatClass"]>, string> = {
  standard: "일반실",
  first: "특실",
  infant: "유아석",
  free: "자유석",
  waitlist: "예약대기",
  any: "좌석 등급 무관",
};
const entryReasonLabels: Record<NonNullable<OperationsEntry["reasonCode"]>, string> = {
  reservation_pending: "예매 처리 결과를 기다리는 중입니다.",
  reservation_payment_required: "좌석을 임시 확보했으며 공식 창에서 결제가 필요합니다.",
  reservation_reserved: "임시 예약이 확인됐으며 결제 완료 상태는 아닙니다.",
  reservation_not_available: "예매 시점에 요청 좌석을 확보하지 못했습니다.",
  reservation_auth_required: "운영사 로그인 확인이 필요해 예매를 진행하지 못했습니다.",
  reservation_provider_blocked: "운영사 요청 제한으로 예매 시도가 중단됐습니다.",
  reservation_failed: "예매 처리가 실패했지만 세부 원인은 기록되지 않았습니다.",
  reservation_unknown: "예매 결과를 확정하지 못해 공식 창 확인이 필요합니다.",
  payment_completed: "공식 내역에서 결제 완료를 확인했습니다.",
  payment_deadline_elapsed_monitoring_resumed: "결제기한 안에 결제가 확인되지 않아 좌석 관측을 다시 시작했습니다.",
  payment_hold_no_longer_present_monitoring_resumed: "공식 미결제 보류가 더 이상 확인되지 않아 좌석 관측을 다시 시작했습니다.",
  payment_deadline_elapsed_one_off_expired: "결제기한이 지나 일회성 관측을 만료 처리했습니다.",
  payment_hold_no_longer_present_one_off_expired: "공식 미결제 보류가 더 이상 확인되지 않아 일회성 관측을 만료 처리했습니다.",
};
const limitationLabels: Record<string, string> = {
  http_and_process_errors_are_not_durably_recorded: "HTTP·프로세스 오류는 이 영속 집계에 포함되지 않습니다.",
  worker_and_scheduler_health_require_durable_heartbeats: "작업 처리·일정 실행은 지속 heartbeat가 없어 현재 정상 여부를 판단할 수 없습니다.",
  recent_entries_are_sanitized_categories_without_identifiers_or_raw_errors: "최근 기록은 내부 ID·노선·원문 오류를 제외하고 열차와 출발 시각 등 필요한 진행 문맥만 표시합니다.",
};

function displayTimestamp(value: string | null): string {
  if (!value) return "확인 불가";
  return new Intl.DateTimeFormat("ko-KR", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    timeZone: "Asia/Seoul",
  }).format(new Date(value));
}

function ageLabel(seconds: number | null, timestamp: string | null): string {
  if (seconds === null) return timestamp ? `${displayTimestamp(timestamp)} 기록` : "기록 시각 확인 불가";
  if (seconds < 60) return `${Math.floor(seconds)}초 전 기록`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}분 전 기록`;
  return `${Math.floor(seconds / 3600)}시간 전 기록`;
}

function statusTone(status: OperationsHealthStatus): string {
  if (status === "healthy" || status === "fresh") return "is-good";
  if (status === "stale") return "is-warning";
  return "is-unknown";
}

function serviceDetail(item: OperationsServiceState): string {
  if ((item.service === "api" || item.service === "database") && item.status === "healthy") return "요청 시점에 확인됨";
  if ((item.service === "worker" || item.service === "scheduler") && item.status === "unknown") {
    return "지속 상태 기록이 없어 확인할 수 없습니다.";
  }
  return item.observedAt ? `${displayTimestamp(item.observedAt)} 확인` : "기록 시각 확인 불가";
}

function ServiceHealthCard({ item }: { item: OperationsServiceState }) {
  const label = serviceLabels[item.service] ?? "기타 서비스";
  return (
    <article className="operations-health-card">
      <div className="operations-card-title">
        <Database size={20} aria-hidden="true" />
        <strong>{label}</strong>
      </div>
      <span className={`operations-status ${statusTone(item.status)}`}>{statusLabels[item.status]}</span>
      <p>{serviceDetail(item)}</p>
    </article>
  );
}

function SourceHealthCard({ item }: { item: OperationsSourceFreshness }) {
  const label = sourceLabels[item.source] ?? "기타 운영 집계";
  return (
    <article className="operations-health-card">
      <div className="operations-card-title">
        <Pulse size={20} aria-hidden="true" />
        <strong>{label}</strong>
      </div>
      <span className={`operations-status ${statusTone(item.status)}`}>{statusLabels[item.status]}</span>
      <p>{ageLabel(item.ageSeconds, item.observedAt)} · {timestampBasisLabels[item.timestampBasis] ?? "시각 기준 확인 불가"}</p>
    </article>
  );
}

function circuitTone(state: ProviderCircuit["state"]): string {
  if (state === "closed") return "is-good";
  if (state === "open" || state === "manual_hold") return "is-danger";
  if (state === "half_open") return "is-warning";
  return "is-unknown";
}

function seatStatusSourceTone(state: SeatStatusSource["state"]): string {
  if (state === "ready") return "is-good";
  if (state === "cooldown") return "is-warning";
  return "is-unknown";
}

function retryAfterLabel(seconds: number | null): string {
  if (seconds === null) return "남은 시간 확인 불가";
  if (seconds < 60) return `남은 ${seconds}초`;
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = seconds % 60;
  return remainingSeconds > 0 ? `남은 ${minutes}분 ${remainingSeconds}초` : `남은 ${minutes}분`;
}

function seatStatusSourceDetail(source: SeatStatusSource): string {
  if (source.state === "ready") return "현재 제한 기록 없음";
  if (source.state === "cooldown") {
    const cause = source.cause === null ? "원인 확인 불가" : seatStatusSourceCauseLabels[source.cause];
    return `${cause} · ${retryAfterLabel(source.retryAfterSeconds)}`;
  }
  return "좌석 조회 제공원 상태를 확인할 수 없습니다.";
}

function safeEventDescription(entry: OperationsEntry): string {
  const kind = kindLabels[entry.kind] ?? "운영 상태 기록";
  const status = eventStatusLabels[entry.status] ?? "상태 확인 필요";
  const provider = entry.provider ? providerLabels[entry.provider] ?? null : null;
  const departureDate = entry.departureAt ? new Date(entry.departureAt) : null;
  const departure = departureDate && !Number.isNaN(departureDate.getTime())
    ? new Intl.DateTimeFormat("ko-KR", {
        month: "long",
        day: "numeric",
        weekday: "short",
        hour: "2-digit",
        minute: "2-digit",
        hourCycle: "h23",
        timeZone: "Asia/Seoul",
      }).format(departureDate)
    : null;
  const context = [
    entry.trainNumber,
    departure ? `출발 ${departure}` : null,
    entry.seatClass ? seatClassLabels[entry.seatClass] : null,
  ].filter((value): value is string => value !== null);
  return [provider, kind, status, ...context]
    .filter((value): value is string => value !== null)
    .join(" · ");
}

function safeEventDetail(entry: OperationsEntry): string {
  const reason = entry.reasonCode ? entryReasonLabels[entry.reasonCode] : null;
  const error = entry.errorCategory
    ? errorCategoryLabels[entry.errorCategory] ?? "오류 분류 확인 필요"
    : null;
  if (reason) return error ? `${reason} · ${error}` : reason;
  return `${entryLevelLabels[entry.level]}${error ? ` · ${error}` : ""}`;
}

function rateLabel(value: OperationsRate): string {
  if (value.denominator === 0) return "기록 없음";
  if (value.rate === null) return "확인 불가";
  return `${(value.rate * 100).toFixed(1)}%`;
}

export function SystemStatusDashboard({
  demo = false,
  loader,
  seatStatusSourcesLoader,
}: SystemStatusDashboardProps) {
  const options = loader ? { demo, loader } : { demo };
  const { state, refresh: refreshOperations } = useOperationsSummary(options);
  const sourceOptions = seatStatusSourcesLoader
    ? { demo, enabled: true, loader: seatStatusSourcesLoader }
    : { demo, enabled: loader === undefined };
  const { state: seatStatusSourcesState, refresh: refreshSeatStatusSources } = useSeatStatusSources(sourceOptions);
  const data = state.data;
  const refresh = () => {
    refreshOperations();
    refreshSeatStatusSources();
  };
  const refreshing = state.phase === "refreshing" || seatStatusSourcesState.phase === "refreshing";
  const loading = state.phase === "loading" || seatStatusSourcesState.phase === "loading";

  return (
    <div className="operations-dashboard">
      <div className="operations-heading">
        <div>
          <h2>로그·진행 상태</h2>
          <p>원문 로그나 개인정보 없이 최근 운영 집계만 확인합니다.</p>
        </div>
        <button
          type="button"
          className="button button-outline compact operations-refresh"
          onClick={refresh}
          disabled={loading || refreshing}
          aria-busy={refreshing}
        >
          <ArrowClockwise size={18} aria-hidden="true" />
          {refreshing ? "새로고침 중…" : "새로고침"}
        </button>
      </div>

      {state.phase === "loading" && (
        <div className="operations-loading" role="status">
          <span className="sr-only">로그·진행 상태를 불러오는 중입니다.</span>
          {[0, 1, 2, 3].map((item) => <div key={item} className="operations-skeleton" />)}
        </div>
      )}

      {state.phase === "error" && !data && (
        <div className="operations-message is-error" role="alert">
          <WarningCircle size={24} aria-hidden="true" />
          <div><strong>운영 상태를 불러오지 못했습니다.</strong><span>{state.error}</span></div>
          <button type="button" className="button button-outline compact" onClick={refresh}>다시 시도</button>
        </div>
      )}

      {state.phase === "error" && data && (
        <div className="operations-message is-warning" role="alert">
          <WarningCircle size={22} aria-hidden="true" />
          <div><strong>새 상태를 받지 못했습니다.</strong><span>아래에는 마지막으로 확인한 집계를 유지합니다.</span></div>
        </div>
      )}

      {data && isOperationsSummaryEmpty(data) && (
        <div className="operations-empty">
          <ListBullets size={30} aria-hidden="true" />
          <strong>표시할 운영 집계가 없습니다.</strong>
          <span>작업이 처리되면 개인정보가 제거된 집계가 이곳에 나타납니다.</span>
        </div>
      )}

      {data && !isOperationsSummaryEmpty(data) && (
        <>
          <div className="operations-meta" aria-label="집계 기준">
            <span><Clock size={17} aria-hidden="true" />생성 {displayTimestamp(data.generatedAt)}</span>
            <span>최근 {data.window.hours === null ? "확인 불가" : `${data.window.hours}시간`} 집계</span>
            {demo && <span className="operations-demo-label">데모 데이터</span>}
          </div>

          {data.isPartial && (
            <div className="operations-message is-info" role="status">
              <Info size={22} aria-hidden="true" />
              <div><strong>일부 상태는 확인할 수 없습니다.</strong><span>지속 heartbeat가 없는 항목은 정상으로 추정하지 않고 확인 불가로 표시합니다.</span></div>
            </div>
          )}

          <section className="operations-section" aria-labelledby="operations-health-title">
            <div className="operations-section-heading">
              <h3 id="operations-health-title">서비스·데이터 상태</h3>
              <span>현재 연결과 가장 최근 집계 기준</span>
            </div>
            <div className="operations-health-grid">
              {data.services.map((item, index) => <ServiceHealthCard key={`service-${item.service}-${index}`} item={item} />)}
              {data.sourceFreshness.map((item, index) => <SourceHealthCard key={`source-${item.source}-${index}`} item={item} />)}
              {data.services.length === 0 && data.sourceFreshness.length === 0 && <p className="operations-inline-empty">상태 정보 없음</p>}
            </div>
          </section>

          <section className="operations-section" aria-labelledby="operations-counts-title">
            <div className="operations-section-heading">
              <h3 id="operations-counts-title">처리량</h3>
              <span>선택한 집계 시간창</span>
            </div>
            <div className="operations-count-grid">
              <div><span>좌석 관측</span><strong>{data.windowCounts.seatObservations ?? "—"}</strong><small>오류 {data.windowCounts.seatObservationErrors ?? "—"}</small></div>
              <div><span>상태 변경</span><strong>{data.windowCounts.watchTransitions ?? "—"}</strong><small>실패 전이 {data.windowCounts.watchFailureTransitions ?? "—"}</small></div>
              <div><span>예약 시도</span><strong>{data.windowCounts.reservationAttempts ?? "—"}</strong><small>실패 {data.windowCounts.reservationFailures ?? "—"}</small></div>
              <div><span>알림 이벤트</span><strong>{data.windowCounts.notificationEvents ?? "—"}</strong><small>시간창 내 생성</small></div>
              <div><span>알림 성공</span><strong>{data.windowCounts.notificationSent ?? "—"}</strong><small>최종 전달</small></div>
              <div><span>알림 실패</span><strong>{data.windowCounts.notificationFailed ?? "—"}</strong><small>최종 실패</small></div>
            </div>
            <div className="operations-rate-grid">
              <div className="operations-error-rate">
                <ChartBar size={25} aria-hidden="true" />
                <div><span>좌석 관측 오류율</span><strong>{rateLabel(data.seatObservationErrorRate)}</strong></div>
                <p>오류 {data.seatObservationErrorRate.numerator ?? "—"}건 / 전체 관측 {data.seatObservationErrorRate.denominator ?? "—"}건</p>
                {data.seatObservationErrorRate.definition && <small>{data.seatObservationErrorRate.definition}</small>}
              </div>
              <div className="operations-error-rate">
                <ChartBar size={25} aria-hidden="true" />
                <div><span>알림 최종 실패율</span><strong>{rateLabel(data.notificationDeliveryFailureRate)}</strong></div>
                <p>실패 {data.notificationDeliveryFailureRate.numerator ?? "—"}건 / 처리 완료 {data.notificationDeliveryFailureRate.denominator ?? "—"}건</p>
                {data.notificationDeliveryFailureRate.definition && <small>{data.notificationDeliveryFailureRate.definition}</small>}
              </div>
            </div>
          </section>

          <section className="operations-section" aria-labelledby="operations-current-title">
            <div className="operations-section-heading">
              <h3 id="operations-current-title">현재 진행 상태</h3>
              <span>알림 대기 {data.currentCounts.notificationOutboxPending ?? "—"}건</span>
            </div>
            {data.currentCounts.watchesByStatus.length > 0 ? (
              <div className="operations-status-breakdown">
                {data.currentCounts.watchesByStatus.map((item, index) => (
                  <div key={`${item.status}-${index}`}>
                    <span>{watchStatusLabels[item.status] ?? "기타 상태"}</span>
                    <strong>{item.count ?? "—"}</strong>
                  </div>
                ))}
              </div>
            ) : <p className="operations-inline-empty">진행 상태 집계 없음</p>}
          </section>

          <section className="operations-section" aria-labelledby="operations-circuits-title">
            <div className="operations-section-heading">
              <h3 id="operations-circuits-title">운영사 요청 상태</h3>
              <span>보호 회로 기준</span>
            </div>
            {data.providerCircuits.length > 0 ? (
              <div className="operations-circuit-list">
                {data.providerCircuits.map((circuit, index) => (
                  <div key={`${circuit.provider}-${index}`}>
                    <strong>{providerLabels[circuit.provider] ?? "기타 운영사"}</strong>
                    <span className={`operations-status ${circuitTone(circuit.state)}`}>{circuitLabels[circuit.state]}</span>
                    <div className="operations-circuit-meta">
                      <time dateTime={circuit.updatedAt ?? undefined}>{displayTimestamp(circuit.updatedAt)}</time>
                      {circuit.manualResumeRequired === true && <span>수동 재개 필요</span>}
                    </div>
                  </div>
                ))}
              </div>
            ) : <p className="operations-inline-empty">운영사 요청 상태 없음</p>}
          </section>

          <section className="operations-section" aria-labelledby="operations-seat-status-sources-title">
            <div className="operations-section-heading">
              <h3 id="operations-seat-status-sources-title">좌석 조회 제공원 상태</h3>
              <span>별도 조회 대기 기준</span>
            </div>
            {seatStatusSourcesState.phase === "loading" && (
              <p className="operations-inline-empty" role="status">좌석 조회 제공원 상태를 불러오는 중입니다.</p>
            )}
            {seatStatusSourcesState.phase === "error" && !seatStatusSourcesState.data && (
              <p className="operations-inline-empty" role="status">좌석 조회 제공원 상태를 확인할 수 없습니다.</p>
            )}
            {seatStatusSourcesState.data && seatStatusSourcesState.data.length > 0 && (
              <div className="operations-circuit-list">
                {seatStatusSourcesState.data.map((source) => (
                  <div key={source.source}>
                    <strong>{seatStatusSourceLabels[source.source]}</strong>
                    <span className={`operations-status ${seatStatusSourceTone(source.state)}`}>
                      {seatStatusSourceStateLabels[source.state]}
                    </span>
                    <div className="operations-circuit-meta">
                      <span>{seatStatusSourceDetail(source)}</span>
                    </div>
                  </div>
                ))}
              </div>
            )}
            {seatStatusSourcesState.phase === "error" && seatStatusSourcesState.data && (
              <p className="operations-inline-empty">마지막으로 확인한 좌석 조회 제공원 상태를 유지합니다.</p>
            )}
          </section>

          <section className="operations-section" aria-labelledby="operations-recent-title">
            <div className="operations-section-heading">
              <h3 id="operations-recent-title">최근 진행 기록</h3>
              <span>활동·오류 최대 20개 · 반복 정상 관측 제외</span>
            </div>
            {data.recentEntries.length > 0 ? (
              <ol className="operations-event-list" role="list">
                {data.recentEntries.map((entry, index) => (
                  <li key={`${entry.occurredAt ?? "unknown"}-${index}`}>
                    <span className={`operations-event-dot is-${entry.level}`} aria-hidden="true" />
                    <div>
                      <strong>{safeEventDescription(entry)}</strong>
                      <span>{safeEventDetail(entry)}</span>
                    </div>
                    <time
                      dateTime={entry.occurredAt ?? undefined}
                      aria-label={`기록 시각 ${displayTimestamp(entry.occurredAt)}`}
                    >
                      {displayTimestamp(entry.occurredAt)}
                    </time>
                  </li>
                ))}
              </ol>
            ) : <p className="operations-inline-empty">최근 활동·오류 기록 없음</p>}
          </section>

          {data.limitations.length > 0 && (
            <aside className="operations-limitations" aria-labelledby="operations-limitations-title">
              <strong id="operations-limitations-title">집계 범위 안내</strong>
              <ul>
                {data.limitations.map((item) => <li key={item}>{limitationLabels[item]}</li>)}
              </ul>
            </aside>
          )}
        </>
      )}
    </div>
  );
}
