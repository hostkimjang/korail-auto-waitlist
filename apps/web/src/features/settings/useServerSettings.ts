import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "../../api/client";
import {
  fetchSystemSettings,
  submitSystemSettings,
  type SettingsFieldKey,
  type SettingsScope,
  type SystemSettings,
} from "../../api/systemSettings";
import { fetchUpdateJob, type UpdateJob } from "../../api/systemUpdate";
import { managementAuthorizationError, serverDetail } from "./managementRequestErrors";
import {
  isSettingsJobRunning,
  parseSettingsJobContext,
  serializeSettingsJobContext,
  type SettingsJobContext,
} from "./serverSettingsJob";

export const SETTINGS_JOB_STORAGE_KEY = "railwait-settings-job";
const POLL_INTERVAL_MS = 3000;
// About a minute of "job not found yet" before suggesting a manager check on the install computer.
const SLOW_PICKUP_POLLS = 20;

export const MANAGER_UNAVAILABLE_MESSAGE =
  "설치한 컴퓨터의 관리 서비스와 Docker 실행 상태를 확인해 주세요. 관리 서비스가 준비되면 이 화면에서 서버 설정을 바꿀 수 있습니다.";
export const MANAGER_UPGRADE_MESSAGE =
  "서버 설정을 바꾸려면 설치한 컴퓨터의 관리 서비스를 한 번 교체해야 합니다.";
export const SETTINGS_CHANGED_MESSAGE =
  "다른 곳에서 설정이 바뀌었습니다. 새로 불러온 값을 확인한 뒤 다시 적용하세요.";

export type ServerSettingsRequest =
  | {
    mode: "fields";
    fields: Readonly<Partial<Record<SettingsFieldKey, string>>>;
    scope: SettingsScope;
    reloadRequired: boolean;
  }
  | { mode: "raw"; raw: string; reloadRequired: boolean };

export interface ServerSettingsSubmitError {
  message: string;
  guidance: "manager_upgrade" | null;
}

export interface TrackedSettingsJob {
  context: SettingsJobContext;
  job: UpdateJob;
}

export interface ServerSettingsController {
  snapshot: SystemSettings | null;
  loading: boolean;
  loadError: string | null;
  tracked: TrackedSettingsJob | null;
  jobRunning: boolean;
  jobError: string | null;
  submitting: boolean;
  busy: boolean;
  submitError: ServerSettingsSubmitError | null;
  reload: () => void;
  submit: (request: ServerSettingsRequest) => Promise<void>;
}

function queuedJob(id: string): UpdateJob {
  return { id, state: "queued", stage: "queued", errorCode: null };
}

function rememberedJob(): TrackedSettingsJob | null {
  try {
    const context = parseSettingsJobContext(sessionStorage.getItem(SETTINGS_JOB_STORAGE_KEY));
    return context === null ? null : { context, job: queuedJob(context.id) };
  } catch {
    return null;
  }
}

function rememberJob(context: SettingsJobContext): void {
  try {
    sessionStorage.setItem(SETTINGS_JOB_STORAGE_KEY, serializeSettingsJobContext(context));
  } catch {
    // Polling still works without session storage; only reopening loses the job.
  }
}

function forgetJob(): void {
  try {
    sessionStorage.removeItem(SETTINGS_JOB_STORAGE_KEY);
  } catch {
    // Session storage is optional.
  }
}

function newIdempotencyKey(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
}

function loadFailure(cause: unknown): string {
  if (cause instanceof ApiError) {
    if (cause.status === 404) {
      return "이 서버에는 서버 설정 화면이 아직 설치되지 않았습니다. 앱을 업데이트한 뒤 다시 확인해 주세요.";
    }
    if (cause.status === 401) return "로그인이 만료되었습니다. 다시 로그인한 뒤 서버 설정을 확인해 주세요.";
    if (cause.status === 503 && serverDetail(cause.detail) === "maintenance_in_progress") {
      return "서버가 다시 시작되는 중입니다. 잠시 후 다시 불러와 주세요.";
    }
  }
  return "서버 설정을 불러오지 못했습니다. 잠시 후 다시 불러와 주세요.";
}

interface SubmissionFailure {
  error: ServerSettingsSubmitError;
  reload: boolean;
}

function submissionFailure(cause: unknown): SubmissionFailure {
  if (cause instanceof ApiError) {
    const detail = serverDetail(cause.detail);
    if (cause.status === 409 && detail === "settings_manager_upgrade_required") {
      return { error: { message: MANAGER_UPGRADE_MESSAGE, guidance: "manager_upgrade" }, reload: false };
    }
    if (cause.status === 409) return { error: { message: SETTINGS_CHANGED_MESSAGE, guidance: null }, reload: true };
    if (cause.status === 503) return { error: { message: MANAGER_UNAVAILABLE_MESSAGE, guidance: null }, reload: false };
    if (cause.status === 422) {
      return { error: { message: "입력값을 서버가 받아들이지 않았습니다. 값을 확인해 주세요.", guidance: null }, reload: false };
    }
    const rejection = managementAuthorizationError(cause, {
      request: "설정 변경 요청",
      retryAfterLogin: "다시 적용해 주세요.",
    });
    if (rejection !== null) return { error: { message: rejection, guidance: null }, reload: false };
  }
  return {
    error: {
      message: "설정 변경 요청을 확인하지 못했습니다. 다시 시도해도 같은 요청은 중복 실행하지 않습니다.",
      guidance: null,
    },
    reload: false,
  };
}

export function useServerSettings(demo: boolean): ServerSettingsController {
  const [snapshot, setSnapshot] = useState<SystemSettings | null>(null);
  const [loadRequest, setLoadRequest] = useState(0);
  const [settledRequest, setSettledRequest] = useState(-1);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [tracked, setTracked] = useState<TrackedSettingsJob | null>(() => (demo ? null : rememberedJob()));
  const [jobError, setJobError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<ServerSettingsSubmitError | null>(null);
  const activeSubmission = useRef(false);
  // One key per attempt. A retry of the identical request reuses it so the manager never applies
  // the same change twice; an accepted request or any edit starts a new attempt.
  const attempt = useRef<{ fingerprint: string; key: string } | null>(null);
  const reload = useCallback(() => setLoadRequest((value) => value + 1), []);

  useEffect(() => {
    if (demo) return;
    const abort = new AbortController();
    void fetchSystemSettings(abort.signal)
      .then((result) => {
        if (abort.signal.aborted) return;
        setSnapshot(result);
        setLoadError(null);
      })
      .catch((cause: unknown) => {
        // Keep the last snapshot: a restart in progress must not wipe the form or the progress.
        if (!abort.signal.aborted) setLoadError(loadFailure(cause));
      })
      .finally(() => {
        if (!abort.signal.aborted) setSettledRequest(loadRequest);
      });
    return () => abort.abort();
  }, [demo, loadRequest]);

  const jobId = tracked?.context.id ?? null;
  useEffect(() => {
    if (demo || jobId === null) return;
    const abort = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let missingPolls = 0;
    const poll = async (): Promise<void> => {
      try {
        const result = await fetchUpdateJob(jobId, abort.signal);
        if (abort.signal.aborted) return;
        setTracked((current) => (current?.context.id === result.id ? { ...current, job: result } : current));
        setJobError(null);
        if (!isSettingsJobRunning(result)) {
          forgetJob();
          setLoadRequest((value) => value + 1);
          return;
        }
      } catch (cause: unknown) {
        if (abort.signal.aborted) return;
        if (cause instanceof ApiError && cause.status === 401) {
          setJobError("로그인이 만료되었습니다. 다시 로그인한 뒤 설정 적용 결과를 확인해 주세요.");
          return;
        }
        if (cause instanceof ApiError && cause.status === 404) {
          // The manager writes the job record only after it picks the request up.
          missingPolls += 1;
          setJobError(missingPolls >= SLOW_PICKUP_POLLS
            ? "관리 서비스가 아직 요청을 처리하지 않았습니다. 설치한 컴퓨터에서 관리 서비스 상태(railwait manager status)를 확인해 주세요."
            : null);
        } else {
          setJobError("서버에 다시 연결하는 중입니다. 설정 적용 결과가 확인될 때까지 기다려 주세요.");
        }
      }
      timer = setTimeout(() => void poll(), POLL_INTERVAL_MS);
    };
    void poll();
    return () => {
      abort.abort();
      clearTimeout(timer);
    };
  }, [demo, jobId]);

  const jobRunning = tracked !== null && isSettingsJobRunning(tracked.job);

  const submit = async (request: ServerSettingsRequest): Promise<void> => {
    if (demo || activeSubmission.current || jobRunning || snapshot === null || !snapshot.available) return;
    activeSubmission.current = true;
    setSubmitting(true);
    setSubmitError(null);
    setJobError(null);
    setTracked(null);
    const baseRevision = snapshot.revision;
    const fingerprint = JSON.stringify(request.mode === "fields"
      ? [baseRevision, request.mode, request.fields]
      : [baseRevision, request.mode, request.raw]);
    if (attempt.current?.fingerprint !== fingerprint) {
      attempt.current = { fingerprint, key: newIdempotencyKey() };
    }
    const idempotencyKey = attempt.current.key;
    try {
      const accepted = await submitSystemSettings(request.mode === "fields"
        ? { mode: "fields", baseRevision, idempotencyKey, fields: request.fields }
        : { mode: "raw", baseRevision, idempotencyKey, raw: request.raw });
      attempt.current = null;
      const context: SettingsJobContext = {
        id: accepted.id,
        mode: request.mode,
        scope: request.mode === "fields" ? request.scope : null,
        reloadRequired: request.reloadRequired,
      };
      rememberJob(context);
      setTracked({ context, job: queuedJob(accepted.id) });
    } catch (cause: unknown) {
      const failure = submissionFailure(cause);
      setSubmitError(failure.error);
      if (failure.reload) reload();
    } finally {
      activeSubmission.current = false;
      setSubmitting(false);
    }
  };

  return {
    snapshot,
    loading: !demo && settledRequest !== loadRequest,
    loadError,
    tracked,
    jobRunning,
    jobError,
    submitting,
    busy: submitting || jobRunning,
    submitError,
    reload,
    submit,
  };
}
