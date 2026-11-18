import { useState } from "react";
import { useSystemUpdate } from "./useSystemUpdate";
import "./systemUpdateSettings.css";

const reasons: Record<string, string> = {
  managed_install_required: "CLI로 관리하는 설치에서 간편 업데이트를 사용할 수 있습니다.",
  manager_unavailable: "설치한 컴퓨터의 관리 서비스와 Docker 실행 상태를 확인해 주세요.",
  checking: "최신 릴리스를 확인하고 있습니다.",
  up_to_date: "현재 사용할 수 있는 최신 버전입니다.",
  update_available: "새 버전으로 업데이트할 수 있습니다.",
  update_blocked: "자동 업데이트 조건을 충족하지 않습니다. CLI에서 차단 사유를 확인해 주세요.",
  recovery_required: "이전 업데이트를 확인해야 합니다. CLI에서 작업 상태를 확인해 주세요.",
};
const stages: Record<string, string> = {
  queued: "업데이트 요청 접수", checking: "설치 상태 확인", downloading: "새 버전 다운로드",
  draining: "진행 중인 작업 종료 대기", backing_up: "암호화 백업과 복구 가능 여부 확인",
  applying: "새 버전 적용", verifying: "서비스 상태 확인", resuming: "기존 작업 재개",
  succeeded: "업데이트 완료", complete: "업데이트 완료", failed: "업데이트 실패",
  deferred: "진행 중인 작업이 있어 업데이트 보류", recovery_required: "복구 확인 필요", rejected: "업데이트 요청 조건 확인 필요",
};
export function SystemUpdateSettings({ demo }: { demo: boolean }) {
  const { data, job, loading, submitting, error, refresh, apply } = useSystemUpdate(demo);
  const [confirmedPlan, setConfirmedPlan] = useState<string | null>(null);
  const running = submitting || job?.state === "queued" || job?.state === "running";
  return <section className="system-update-settings" aria-labelledby="system-update-title">
    <div className="panel-heading"><h2 id="system-update-title">앱 업데이트</h2>
      <p>설치한 서버의 새 버전을 확인하고 업데이트합니다.</p></div>
    {demo ? <p>체험 화면에서는 서버 업데이트를 실행하지 않습니다.</p> : <>
      {data?.currentVersion && <p>현재 버전 <strong>v{data.currentVersion}</strong>{data.latestVersion && <> · 최신 안정 버전 <strong>v{data.latestVersion}</strong></>}</p>}
      <p>{loading ? "업데이트 정보 확인 중…" : data ? reasons[data.reason] ?? reasons.update_blocked : "업데이트 정보를 확인해 주세요."}</p>
      {job && <p role="status">{stages[job.state === "running" ? job.stage : job.state] ?? "업데이트 작업 진행 중"}</p>}
      {error && <p role="status">{error}</p>}
      {job?.state === "succeeded" && <p>업데이트가 완료되었습니다. 화면을 새로고침해 새 버전을 확인하세요.</p>}
      {data?.available && !running && <label className="system-update-consent">
        <input type="checkbox" checked={confirmedPlan === data.planId}
          onChange={(event) => setConfirmedPlan(event.target.checked ? data.planId : null)} />
        <span>업데이트 중 잠시 접속이 끊깁니다. 진행 중인 예매가 끝난 뒤 백업하고 업데이트하는 데 동의합니다.</span>
      </label>}
      <div className="system-update-actions">
        <button type="button" className="button button-outline" disabled={running || loading} onClick={refresh}>업데이트 확인</button>
        {data?.available && <button type="button" className="button button-primary"
          disabled={running || confirmedPlan !== data.planId} onClick={() => void apply()}>
          {running ? "업데이트 진행 중…" : `v${data.latestVersion} 업데이트`}</button>}
      </div>
    </>}
  </section>;
}
