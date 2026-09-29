import { Clock } from "@phosphor-icons/react";

import type { RailProvider } from "../../api/providerAccounts";
import type { TimetableProgress } from "../../api/timetables";

interface TimetableLoadingStatusProps {
  loadingProviders: RailProvider[];
  korailProgress: TimetableProgress;
}

function elapsedWaitLabel(seconds: number): string {
  const minutes = Math.floor(seconds / 60).toLocaleString("ko-KR");
  const remainingSeconds = seconds % 60;
  return `${minutes}분 ${remainingSeconds}초 경과`;
}

export function TimetableLoadingStatus({
  loadingProviders,
  korailProgress,
}: TimetableLoadingStatusProps) {
  if (loadingProviders.length === 0) return null;

  if (loadingProviders.includes("KORAIL") && korailProgress.state === "official_queue") {
    const elapsedWaitSeconds = korailProgress.queue?.elapsedWaitSeconds;
    return (
      <div className="timetable-state timetable-state-queue" role="status" aria-live="polite">
        <Clock size={24} aria-hidden="true" />
        <div className="timetable-state-copy">
          <strong>코레일 서비스 접속 대기 중입니다.</strong>
          <span>공식 대기열에서 연결을 기다리고 있어 조회가 오래 걸릴 수 있습니다.</span>
          {elapsedWaitSeconds !== undefined && (
            <dl className="official-queue-details" aria-live="off">
              <div><dt>앱에서 대기 감지 후</dt><dd>{elapsedWaitLabel(elapsedWaitSeconds)}</dd></div>
            </dl>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="timetable-state" role="status" aria-live="polite">
      <Clock size={24} aria-hidden="true" />
      <span>{loadingProviders.join(" · ")} 공식 시간표를 조회하고 있습니다.</span>
    </div>
  );
}
