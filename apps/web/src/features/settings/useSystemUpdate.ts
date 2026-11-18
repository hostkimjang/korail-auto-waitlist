import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../../api/client";
import { fetchSystemUpdate, fetchUpdateJob, submitSystemUpdate, type SystemUpdate, type UpdateJob } from "../../api/systemUpdate";

const storageKey = "railwait-update-job";
function rememberedJob(): string | null {
  try {
    const id = sessionStorage.getItem(storageKey);
    return id && /^[a-f0-9]{32}$/.test(id) ? id : null;
  } catch { return null; }
}
export function useSystemUpdate(demo: boolean) {
  const [data, setData] = useState<SystemUpdate | null>(null);
  const [job, setJob] = useState<UpdateJob | null>(null);
  const [jobId, setJobId] = useState<string | null>(() => demo ? null : rememberedJob());
  const [loading, setLoading] = useState(!demo);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const activeSubmission = useRef(false);
  const submissionKey = useRef<{ plan: string; key: string } | null>(null);
  const refresh = useCallback(() => setRevision((value) => value + 1), []);
  useEffect(() => {
    if (demo) return;
    const abort = new AbortController();
    void fetchSystemUpdate(abort.signal).then((result) => {
      if (!abort.signal.aborted) { setData(result); setError(null); }
    }).catch((cause: unknown) => {
      if (!abort.signal.aborted) setError(cause instanceof ApiError && cause.status === 404
        ? "이 서버에는 간편 업데이트가 아직 설치되지 않았습니다."
        : "업데이트 정보를 확인하지 못했습니다. 잠시 후 다시 확인해 주세요.");
    }).finally(() => { if (!abort.signal.aborted) setLoading(false); });
    return () => abort.abort();
  }, [demo, revision]);
  useEffect(() => {
    if (demo || !jobId) return;
    const abort = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      try {
        const result = await fetchUpdateJob(jobId, abort.signal);
        if (abort.signal.aborted) return;
        setJob(result);
        setError(null);
        if (result.state !== "queued" && result.state !== "running") {
          try { sessionStorage.removeItem(storageKey); } catch { /* Session storage is optional. */ }
          refresh();
          return;
        }
      } catch (cause: unknown) {
        if (abort.signal.aborted) return;
        if (cause instanceof ApiError && cause.status === 401) {
          setError("로그인이 만료되었습니다. 다시 로그인한 뒤 업데이트 결과를 확인해 주세요.");
          return;
        }
        setError("서버에 다시 연결하는 중입니다. 업데이트 결과가 확인될 때까지 기다려 주세요.");
      }
      timer = setTimeout(() => void poll(), 3000);
    };
    void poll();
    return () => { abort.abort(); clearTimeout(timer); };
  }, [demo, jobId, refresh]);
  const apply = async () => {
    if (activeSubmission.current || demo || !data?.available || !data.latestVersion || !data.planId) return;
    activeSubmission.current = true;
    setSubmitting(true);
    setError(null);
    try {
      if (submissionKey.current?.plan !== data.planId) {
        const bytes = crypto.getRandomValues(new Uint8Array(16));
        submissionKey.current = { plan: data.planId, key: Array.from(bytes, (v) => v.toString(16).padStart(2, "0")).join("") };
      }
      const result = await submitSystemUpdate(data.latestVersion, data.planId, submissionKey.current.key);
      setJob(result);
      setJobId(result.id);
      try { sessionStorage.setItem(storageKey, result.id); } catch { /* Polling still works without storage. */ }
    } catch (cause: unknown) {
      setError(cause instanceof ApiError && cause.status === 409
        ? "업데이트 계획이 바뀌었습니다. 정보를 새로 확인한 뒤 다시 실행해 주세요."
        : "업데이트 요청을 확인하지 못했습니다. 다시 시도해도 같은 요청은 중복 실행하지 않습니다.");
      refresh();
    } finally { activeSubmission.current = false; setSubmitting(false); }
  };
  return { data, job, loading, submitting, error, refresh, apply };
}
