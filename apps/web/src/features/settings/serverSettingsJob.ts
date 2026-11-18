import type { SettingsScope } from "../../api/systemSettings";
import type { UpdateJob } from "../../api/systemUpdate";

export type SettingsJobMode = "fields" | "raw";

// Remembered per browser tab so reopening the settings view keeps following the same job.
export interface SettingsJobContext {
  id: string;
  mode: SettingsJobMode;
  /** Restart scope computed by the form; null when the server decides it (raw mode). */
  scope: SettingsScope | null;
  /** Whether a web-build setting changed, which needs a page reload to take effect. */
  reloadRequired: boolean;
}

export type SettingsJobTone = "progress" | "success" | "warning" | "error";

export interface SettingsJobOutcome {
  tone: SettingsJobTone;
  message: string;
  /** A CLI command the user can run on the install computer for details. */
  command: string | null;
}

const JOB_ID = /^[a-f0-9]{32}$/;

export function parseSettingsJobContext(stored: string | null): SettingsJobContext | null {
  if (stored === null) return null;
  let value: unknown;
  try {
    value = JSON.parse(stored);
  } catch {
    return null;
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const item = Object.fromEntries(Object.entries(value));
  if (typeof item.id !== "string" || !JOB_ID.test(item.id)) return null;
  if (item.mode !== "fields" && item.mode !== "raw") return null;
  if (typeof item.reloadRequired !== "boolean") return null;
  const scope = item.scope === "web" || item.scope === "app" ? item.scope : null;
  if (item.mode === "fields" && scope === null) return null;
  return { id: item.id, mode: item.mode, scope, reloadRequired: item.reloadRequired };
}

export function serializeSettingsJobContext(context: SettingsJobContext): string {
  return JSON.stringify(context);
}

export function isSettingsJobRunning(job: UpdateJob | null): boolean {
  return job === null || job.state === "queued" || job.state === "running";
}

const STAGE_LABELS: Readonly<Record<string, string>> = {
  queued: "관리 서비스가 요청을 받기를 기다리는 중",
  accepted: "요청 접수",
  checking: "현재 설정 확인 중",
  preparing_config: "새 설정 준비 중",
  draining: "진행 중인 작업이 끝나기를 기다리는 중",
  backing_up: "암호화 백업 중",
  applying: "새 설정 적용 중",
  verifying: "서비스 상태 확인 중",
  resuming: "기존 작업 재개 중",
  rolled_back: "이전 설정으로 되돌리는 중",
  succeeded: "설정 적용 완료",
  rejected: "요청 조건 확인 중",
};

export function settingsStageLabel(stage: string): string {
  return STAGE_LABELS[stage] ?? "설정 적용 진행 중";
}

function statusCommand(id: string): string {
  return `railwait status --job ${id}`;
}

function rejectedOutcome(job: UpdateJob, mode: SettingsJobMode): SettingsJobOutcome {
  switch (job.errorCode) {
    case "E_SETTINGS_CHANGED":
      return {
        tone: "warning",
        message: "다른 곳에서 설정이 바뀌어 적용하지 않았습니다. 새로 불러온 값을 확인한 뒤 다시 적용하세요.",
        command: null,
      };
    case "E_SETTINGS_INVALID":
      return {
        tone: "error",
        message: mode === "raw"
          ? "원문에 관리 서비스가 받아들이지 않는 줄이나 값이 있어 적용하지 않았습니다. 형식을 확인해 주세요."
          : "관리 서비스가 입력값을 받아들이지 않아 적용하지 않았습니다. 값을 확인해 주세요.",
        command: null,
      };
    case "E_SETTINGS_FORBIDDEN":
      return {
        tone: "error",
        message: "•••••• 로 가린 값이나 CLI로만 바꿀 수 있는 항목이 바뀌어 적용하지 않았습니다. 그 줄을 원래대로 두고 다시 적용하세요.",
        command: null,
      };
    case "E_SETTINGS_UNCHANGED":
      return { tone: "warning", message: "바뀐 항목이 없어 적용하지 않았습니다.", command: null };
    case "E_UNAUTHORIZED":
      return {
        tone: "error",
        message: "관리 서비스가 이 요청의 인증을 확인하지 못해 적용하지 않았습니다. 설치한 컴퓨터에서 관리 서비스 상태를 확인해 주세요.",
        command: "railwait manager status",
      };
    default:
      return {
        tone: "error",
        message: "관리 서비스가 설정 변경 요청을 거부했습니다. 설치한 컴퓨터에서 사유를 확인해 주세요.",
        command: statusCommand(job.id),
      };
  }
}

export function settingsJobOutcome(job: UpdateJob, context: SettingsJobContext): SettingsJobOutcome {
  switch (job.state) {
    case "queued":
    case "running":
      return {
        tone: "progress",
        message: `서버 설정 적용 중: ${settingsStageLabel(job.state === "queued" ? "queued" : job.stage)}`,
        command: null,
      };
    case "succeeded":
      return {
        tone: "success",
        message: context.reloadRequired
          ? "서버 설정을 적용했습니다. 바뀐 앱 연결 설정을 쓰려면 이 페이지를 새로고침하세요."
          : "서버 설정을 적용했습니다.",
        command: null,
      };
    case "rejected":
      return rejectedOutcome(job, context.mode);
    case "deferred":
      return {
        tone: "warning",
        message: "진행 중인 작업이 있어 설정 적용을 보류했습니다. 진행 중인 예매가 끝난 뒤 다시 적용해 주세요.",
        command: null,
      };
    case "failed":
      if (job.stage === "rolled_back") {
        return {
          tone: "error",
          message: "적용에 실패해 이전 설정으로 되돌렸습니다. 설치한 컴퓨터에서 원인을 확인할 수 있습니다.",
          command: statusCommand(job.id),
        };
      }
      return {
        tone: "error",
        message: "설정 적용을 끝내지 못했습니다. 설치한 컴퓨터에서 상태를 확인해 주세요.",
        command: statusCommand(job.id),
      };
    case "recovery_required":
      return {
        tone: "error",
        message: "설정 적용 뒤 복구 확인이 필요합니다. 설치한 컴퓨터에서 상태를 확인해 주세요.",
        command: statusCommand(job.id),
      };
  }
}
