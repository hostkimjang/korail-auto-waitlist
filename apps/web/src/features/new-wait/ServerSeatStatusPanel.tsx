import { useState } from "react";

import { ArrowsClockwise, CheckCircle, WarningCircle } from "@phosphor-icons/react";

import type { ServerSeatStatusSummary } from "./serverSeatStatusSummary";

interface ServerSeatStatusPanelProps {
  summary: ServerSeatStatusSummary;
  onRetry: (providers: readonly string[]) => Promise<unknown>;
}

export function ServerSeatStatusPanel({ summary, onRetry }: ServerSeatStatusPanelProps) {
  const [retrying, setRetrying] = useState(false);
  const content = panelContent(summary);
  const retryableProviders = summary.retryableProviders;
  const retryable = retryableProviders.length > 0
    && (summary.state === "partial" || summary.state === "error");

  const retry = async () => {
    if (retrying) return;
    setRetrying(true);
    try {
      await onRetry(retryableProviders);
    } finally {
      setRetrying(false);
    }
  };

  return (
    <section className={`server-seat-status-panel is-${summary.state}`} aria-live="polite">
      <span className="server-seat-status-icon" aria-hidden="true">
        {summary.state === "loading"
          ? <ArrowsClockwise className="is-spinning" />
          : summary.state === "complete" || summary.state === "empty"
            ? <CheckCircle weight="fill" />
            : <WarningCircle weight="fill" />}
      </span>
      <div className="server-seat-status-copy">
        <strong>{content.title}</strong>
        <span>{content.description}</span>
      </div>
      {retryable && (
        <button
          type="button"
          className="button button-outline compact"
          disabled={retrying}
          aria-busy={retrying}
          onClick={() => void retry()}
        >
          {retrying ? "좌석 상태 다시 조회 중" : "서버에서 좌석 상태 다시 조회"}
        </button>
      )}
    </section>
  );
}

function panelContent(summary: ServerSeatStatusSummary): { title: string; description: string } {
  if (summary.state === "loading") {
    return {
      title: "좌석 상태 자동 조회 중",
      description: "서버가 선택한 날짜·시간대의 시간표와 일반실·특실 상태를 함께 확인하고 있습니다.",
    };
  }
  if (summary.state === "complete") {
    return {
      title: "좌석 상태 자동 반영 완료",
      description: `${summary.observedSeatCount}개 좌석 등급을 확인했습니다. 현재 실행 가능한 공식 예매·예약대기·취소표 감시 행동만 표시합니다.`,
    };
  }
  if (summary.state === "empty") {
    return {
      title: "좌석 상태 자동 조회 완료",
      description: "선택한 날짜·시간대에 표시할 열차가 없습니다.",
    };
  }
  if (summary.state === "restricted") {
    return {
      title: "공식 좌석 조회가 제한되었습니다",
      description: "조회 대기 시간 동안 서버는 운영사에 다시 요청하지 않습니다. 좌석은 미확인 상태로 유지되며 예매·대기 행동을 제공하지 않습니다.",
    };
  }
  if (summary.state === "elapsed") {
    return {
      title: "선택한 출발 시간대가 지났습니다",
      description: "이미 운행이 끝난 시간대라 현재 좌석 상태를 다시 조회하지 않습니다. 날짜나 출발 시간을 변경해 주세요.",
    };
  }
  if (summary.state === "error") {
    return {
      title: "좌석 상태를 가져오지 못했습니다",
      description: "확인되지 않은 좌석은 예매 가능이나 매진으로 추정하지 않으며 대기에도 등록하지 않습니다.",
    };
  }
  return {
    title: "일부 좌석 상태를 확인하지 못했습니다",
    description: `${summary.unknownSeatCount}개 좌석 등급은 확인 전 상태입니다. 확인된 열차만 상태에 맞는 행동을 제공합니다.`,
  };
}
