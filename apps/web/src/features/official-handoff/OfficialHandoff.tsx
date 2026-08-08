import { useEffect, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { createPortal } from "react-dom";
import {
  ArrowSquareOut,
  Copy,
  ShieldCheck,
  WarningCircle,
  X,
} from "@phosphor-icons/react";

import { hasObservedSeatEvidence } from "../../domain/seatEvidence";
import { useDocumentScrollLock } from "../../hooks/useDocumentScrollLock";
import {
  KORAIL_OFFICIAL_ENTRY_URL,
  isStrictKorailOfficialSearchUrl,
} from "./korailOfficialSearchUrl";
import {
  resolveOfficialOpenTarget,
  SRT_OFFICIAL_ENTRY_URL,
} from "../../shared/lib/officialAppIntentUrl";

type SeatClassId = "standard" | "first" | "any";

interface HandoffSeat {
  seat_class: string;
  provenance?: {
    kind?: string;
    source?: string;
    observed_at?: string;
    fresh_until?: string;
    client_freshness?: { ttl_ms?: number; received_monotonic_ms?: number };
  };
}

export interface OfficialHandoffTrain {
  id: string;
  provider: string;
  name: string;
  origin: string;
  destination: string;
  departure_at?: string;
  departure?: string;
  arrival?: string;
  date?: string;
  seat_classes?: HandoffSeat[];
  official_search_url?: string | null;
}

interface SeatFoundObservation {
  kind?: string;
  observedLabel?: string;
}

interface OfficialHandoffProps {
  train: OfficialHandoffTrain;
  selectedSeatClass?: SeatClassId | null;
  onCopy: (train: OfficialHandoffTrain) => Promise<boolean> | boolean;
  triggerLabel?: string;
  actionUrl?: string | null;
  searchUrl?: string | null;
  triggerClassName?: string;
  seatFoundObservation?: SeatFoundObservation | null;
}

const seatClassNames: Readonly<Record<SeatClassId, string>> = {
  standard: "일반실",
  first: "특실",
  any: "좌석 무관",
};

const officialBookingUrls: Readonly<Record<string, string>> = {
  KORAIL: KORAIL_OFFICIAL_ENTRY_URL,
  SRT: SRT_OFFICIAL_ENTRY_URL,
};

function providerOfficialEntryUrl(provider: string, candidate: string | null): string | null {
  const normalizedProvider = provider.toUpperCase();
  const fallback = officialBookingUrls[normalizedProvider] ?? null;
  if (!fallback || !candidate) return fallback;
  try {
    const candidateUrl = new URL(candidate);
    const fallbackUrl = new URL(fallback);
    const isAllowlistedEntry = candidateUrl.protocol === "https:"
      && candidateUrl.origin === fallbackUrl.origin
      && candidateUrl.pathname === fallbackUrl.pathname
      && (normalizedProvider !== "SRT"
        || candidateUrl.searchParams.get("pageId") === "TK0101010000");
    if (isAllowlistedEntry) return fallback;
  } catch {
    // 고정 공식 진입점으로 안전하게 되돌립니다.
  }
  return fallback;
}

function officialAction(
  provider: string,
  entryCandidate: string | null,
  searchCandidate: string | null,
): { url: string | null; conditionsPrefilled: boolean } {
  if (provider.toUpperCase() === "KORAIL" && isStrictKorailOfficialSearchUrl(searchCandidate)) {
    return { url: searchCandidate, conditionsPrefilled: true };
  }
  return {
    url: providerOfficialEntryUrl(provider, entryCandidate),
    conditionsPrefilled: false,
  };
}

function displayTrainName(value: string): string {
  return value.replace(/^0+(?=\d+$)/, "").replace(/(\s)0+(?=\d+$)/, "$1");
}

function dateLabel(value: string): string {
  if (!value) return "날짜 미정";
  const parsed = new Date(`${value}T00:00:00+09:00`);
  if (Number.isNaN(parsed.getTime())) return "날짜 미정";
  return new Intl.DateTimeFormat("ko-KR", {
    month: "long",
    day: "numeric",
    weekday: "short",
  }).format(parsed);
}

export function OfficialHandoff({
  train,
  selectedSeatClass = null,
  onCopy,
  triggerLabel = "공식 좌석 확인",
  actionUrl = null,
  searchUrl = train.official_search_url ?? null,
  triggerClassName = "button button-outline compact",
  seatFoundObservation = null,
}: OfficialHandoffProps) {
  const [open, setOpen] = useState(false);
  const [copyState, setCopyState] = useState<"idle" | "pending" | "success" | "error">("idle");
  const triggerRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLElement>(null);
  const copyRequestRef = useRef(0);
  const safeId = train.id.replace(/[^a-zA-Z0-9_-]/g, "-");
  const titleId = `official-handoff-title-${safeId}`;
  const descriptionId = `official-handoff-description-${safeId}`;
  const action = officialAction(train.provider, actionUrl, searchUrl);
  const fixedOfficialUrl = officialBookingUrls[train.provider.toUpperCase()] ?? null;
  const officialOpenTarget = resolveOfficialOpenTarget(train.provider, fixedOfficialUrl);
  const officialAppIntentUrl = officialOpenTarget?.usesAndroidApp
    ? officialOpenTarget.url
    : undefined;
  const primaryActionUsesAndroidApp = officialAppIntentUrl !== undefined;
  const primaryActionConditionsPrefilled = action.conditionsPrefilled
    && !primaryActionUsesAndroidApp;
  const travelDate = train.date ?? dateLabel(String(train.departure_at ?? "").slice(0, 10));
  const journeySummary = `${String(train.departure_at ?? "").slice(0, 10)} / ${train.origin} → ${train.destination} / ${train.name} / ${train.departure ?? "시간 확인"} 출발`;
  const selectedSeat = selectedSeatClass
    ? train.seat_classes?.find((seat) => seat.seat_class === selectedSeatClass)
    : null;
  const hasObservedSeatState = hasObservedSeatEvidence(selectedSeat);
  const hasDemoSeatState = hasObservedSeatState && selectedSeat?.provenance?.kind === "mock";
  const hasAuthorizedSeatState = hasObservedSeatState
    && selectedSeat?.provenance?.kind === "official_provider";
  const modalTitle = seatFoundObservation ? "공식 예매 안내" : "공식 좌석 확인 전 안내";
  const closeLabel = seatFoundObservation ? "공식 예매 안내 닫기" : "공식 좌석 확인 안내 닫기";
  useDocumentScrollLock(open);

  const closeHandoff = () => {
    copyRequestRef.current += 1;
    setOpen(false);
    window.setTimeout(() => triggerRef.current?.focus(), 0);
  };

  useEffect(() => {
    if (!open) return undefined;
    const appRoot = triggerRef.current?.closest<HTMLElement>(".app-shell");
    if (appRoot) {
      appRoot.inert = true;
      appRoot.setAttribute("aria-hidden", "true");
    }
    const focusTimer = window.setTimeout(() => {
      dialogRef.current?.querySelector<HTMLElement>("[data-autofocus]")?.focus();
    }, 0);
    return () => {
      window.clearTimeout(focusTimer);
      if (appRoot) {
        appRoot.inert = false;
        appRoot.removeAttribute("aria-hidden");
      }
    };
  }, [open]);

  const handleCopy = async () => {
    const requestId = copyRequestRef.current + 1;
    copyRequestRef.current = requestId;
    setCopyState("pending");
    try {
      const copied = await onCopy(train);
      if (copyRequestRef.current !== requestId) return;
      setCopyState(copied === true ? "success" : "error");
    } catch {
      if (copyRequestRef.current !== requestId) return;
      setCopyState("error");
    }
  };

  const handleDialogKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      closeHandoff();
      return;
    }
    if (event.key !== "Tab" || !dialogRef.current) return;
    const focusable = [...dialogRef.current.querySelectorAll<HTMLElement>(
      'button:not(:disabled), [href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])',
    )];
    const first = focusable[0];
    const last = focusable.at(-1);
    if (!first || !last) return;
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        className={triggerClassName}
        aria-label={`${train.name}${selectedSeatClass ? ` ${seatClassNames[selectedSeatClass]}` : ""} ${triggerLabel} 전 안내 열기`}
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => {
          copyRequestRef.current += 1;
          setCopyState("idle");
          setOpen(true);
        }}
      >
        {triggerLabel} <ArrowSquareOut aria-hidden="true" />
      </button>
      {open && createPortal((
        <div className="official-handoff-layer">
          <div className="official-handoff-scrim" aria-hidden="true" onClick={closeHandoff} />
          <section
            ref={dialogRef}
            className="official-handoff-panel"
            role="dialog"
            aria-modal="true"
            aria-labelledby={titleId}
            aria-describedby={descriptionId}
            onKeyDown={handleDialogKeyDown}
          >
            <header className="official-handoff-header">
              <div>
                <span className={`provider-chip ${train.provider === "SRT" ? "provider-srt" : "provider-korail"}`}>{train.provider}</span>
                <h2 id={titleId} aria-label={`${train.name} ${modalTitle}`}>{displayTrainName(train.name)} {modalTitle}</h2>
              </div>
              <button data-autofocus type="button" className="icon-button" aria-label={closeLabel} onClick={closeHandoff}><X size={22} /></button>
            </header>
            <div className="official-handoff-summary" aria-label="선택 열차 요약">
              <div><span>여정</span><strong>{train.origin} → {train.destination}</strong></div>
              <div><span>시간</span><strong>{travelDate} · {train.departure ?? "출발 확인"} → {train.arrival || "도착 확인"}</strong></div>
              <div><span>열차</span><strong>{displayTrainName(train.name)}{selectedSeatClass ? ` · ${seatClassNames[selectedSeatClass]}` : ""}</strong></div>
            </div>
            <div className="official-handoff-fact">
              <ShieldCheck size={24} weight="fill" aria-hidden="true" />
              {seatFoundObservation?.kind === "official_provider"
                ? <div><strong>좌석 가능 상태가 최근 관측되었습니다</strong><span>{selectedSeatClass ? seatClassNames[selectedSeatClass] : "선택 좌석"} 기준 · {seatFoundObservation.observedLabel}. 좌석은 계속 바뀔 수 있으므로 공식 플랫폼에서 최종 확인하세요.</span></div>
                : seatFoundObservation?.kind === "mock"
                  ? <div><strong>좌석 발견 상태는 UX 검증용 데모입니다</strong><span>{seatFoundObservation.observedLabel}. 실제 좌석이나 예매 가능 상태를 뜻하지 않습니다.</span></div>
                  : hasDemoSeatState
                    ? <div><strong>좌석 상태는 UX 검증용 데모입니다</strong><span>정식 앱에서 관찰한 상태 표현을 재현했으며 실시간 좌석을 뜻하지 않습니다.</span></div>
                    : hasAuthorizedSeatState
                      ? <div><strong>허가된 좌석 출처의 관측값입니다</strong><span>표시 시각 이후 상태가 바뀔 수 있으므로 공식 플랫폼에서 최종 확인하세요.</span></div>
                      : <div><strong>좌석 상태는 아직 확인되지 않았습니다</strong><span>공식 시간표와 좌석 재고는 별도 정보이므로 공식 화면에서 직접 확인해 주세요.</span></div>}
            </div>
            <p id={descriptionId} className="official-handoff-description">
              {primaryActionUsesAndroidApp
                ? "Android에서는 공식 앱 열기를 먼저 시도합니다. 앱이 없거나 연결을 지원하지 않으면 레일웨잇을 유지한 채 외부 브라우저 창에서 고정된 공식 홈페이지를 엽니다. 어느 경로든 좌석 확보나 예약 성공을 뜻하지 않으며, 결과는 공식 화면에서 직접 확인해야 합니다."
                : primaryActionConditionsPrefilled
                ? "선택한 여정 조건을 공식 검색 화면에 미리 입력합니다. 특정 열차 선택·좌석 확보·예매 성공을 뜻하지 않습니다. 결과는 공식 화면에서 직접 확인해야 합니다."
                : "공식 페이지는 새 탭에서 열립니다. 페이지를 열어도 좌석 확보나 예약 성공 상태로 바뀌지 않으며, 결과는 공식 화면에서 직접 확인해야 합니다."}
            </p>
            <div className="official-handoff-warning">
              <WarningCircle size={23} weight="fill" aria-hidden="true" />
              <div><strong>접근 제한 화면이 나타나면 자동 재시도하지 않습니다</strong><span>보안문자, 접속 대기, CODE -8002·-8003 또는 접근 불가 화면을 닫고 나중에 공식 앱이나 홈페이지에서 직접 확인해 주세요.</span></div>
            </div>
            {copyState === "success" && <div className="official-handoff-copy-status" role="status">여정 정보를 복사했습니다. 공식 검색 화면에 그대로 참고해 주세요.</div>}
            {copyState === "error" && <div className="official-handoff-copy-error" role="alert"><strong>자동 복사에 실패했습니다</strong><span>{journeySummary}</span></div>}
            <footer className="official-handoff-actions">
              <button type="button" className="button button-outline" aria-busy={copyState === "pending"} disabled={copyState === "pending"} onClick={handleCopy}><Copy size={20} aria-hidden="true" />{copyState === "pending" ? "복사 중…" : "여정 복사"}</button>
              {primaryActionUsesAndroidApp ? (
                <a
                  className="button button-primary"
                  href={officialAppIntentUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  공식 앱 또는 홈페이지 열기 <small>(Android)</small><ArrowSquareOut size={20} aria-hidden="true" />
                </a>
              ) : (
                <button type="button" className="button button-primary" disabled={!action.url} onClick={() => action.url && window.open(action.url, "_blank", "noopener,noreferrer")}>{primaryActionConditionsPrefilled ? "조건 입력하고 공식 페이지 열기" : "공식 페이지 열기"} <small>(새 탭)</small><ArrowSquareOut size={20} aria-hidden="true" /></button>
              )}
            </footer>
          </section>
        </div>
      ), document.body)}
    </>
  );
}
