import { CheckCircle, Copy, LinkSimple, Trash, WarningCircle } from "@phosphor-icons/react";
import { useEffect, useState } from "react";

import {
  createBrowserCompanionPairing,
  fetchBrowserCompanionStatus,
  revokeBrowserCompanionCredential,
} from "../../api.js";

interface Credential {
  id: string;
  label: string;
  extension_origin: string;
  created_at: string;
  last_used_at: string | null;
}

interface CompanionStatus {
  enabled: boolean;
  credentials: Credential[];
}

interface Pairing {
  pairing_code: string;
  expires_at: string;
}

interface Props {
  demo: boolean;
  onToast?: (message: string) => void;
}

function dateTimeLabel(value: string | null): string {
  if (!value) return "아직 전송 없음";
  return new Intl.DateTimeFormat("ko-KR", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

export function KorailBrowserPairingPanel({ demo, onToast }: Props) {
  const [status, setStatus] = useState<CompanionStatus | null>(null);
  const [pairing, setPairing] = useState<Pairing | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [feedback, setFeedback] = useState("");

  const reload = async () => {
    if (demo) {
      setStatus({ enabled: false, credentials: [] });
      return;
    }
    try {
      setStatus(await fetchBrowserCompanionStatus() as CompanionStatus);
      setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "연결 상태를 불러오지 못했습니다.");
    }
  };

  useEffect(() => {
    void reload();
  }, [demo]);

  const issuePairing = async () => {
    setLoading(true);
    try {
      setPairing(await createBrowserCompanionPairing("내 브라우저") as Pairing);
      setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "연결 코드를 만들지 못했습니다.");
    } finally {
      setLoading(false);
    }
  };

  const copyPairing = async () => {
    if (!pairing) return;
    await navigator.clipboard.writeText(pairing.pairing_code);
    const message = "1회 연결 코드를 복사했습니다.";
    setFeedback(message);
    onToast?.(message);
  };

  const revoke = async (credential: Credential) => {
    setLoading(true);
    try {
      await revokeBrowserCompanionCredential(credential.id);
      const message = `${credential.label} 연결을 해제했습니다.`;
      setFeedback(message);
      onToast?.(message);
      await reload();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "연결을 해제하지 못했습니다.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      <div className="panel-heading">
        <h2>KORAIL 브라우저 연결</h2>
        <p>공식 화면에서 읽은 좌석 상태를 레일웨잇에 안전하게 한 번 전송합니다.</p>
      </div>

      {status && !status.enabled && (
        <div className="companion-notice is-warning">
          <WarningCircle size={24} />
          <div><strong>브라우저 브리지가 꺼져 있습니다.</strong><span><code>KORAIL_BROWSER_BRIDGE_ENABLED=true</code>로 서버를 다시 시작해 주세요.</span></div>
        </div>
      )}

      <section className="companion-pairing-card">
        <div>
          <span className="eyebrow">1회 연결</span>
          <h3>확장 프로그램을 이 서비스와 연결</h3>
          <p>코드는 5분 동안 한 번만 사용할 수 있습니다. 영구 토큰을 복사하거나 <code>.env</code>에 넣지 않습니다.</p>
        </div>
        <button type="button" className="button button-primary" disabled={loading || demo || !status?.enabled} onClick={issuePairing}>
          <LinkSimple size={20} /> 연결 코드 만들기
        </button>
        {pairing && (
          <div className="companion-pairing-code">
            <div><span>1회 연결 코드</span><strong>{pairing.pairing_code}</strong><small>{dateTimeLabel(pairing.expires_at)}까지 유효</small></div>
            <button type="button" className="button button-outline compact" onClick={copyPairing}><Copy size={18} />복사</button>
          </div>
        )}
      </section>

      <section className="companion-connections" aria-live="polite">
        <div className="companion-section-heading"><h3>연결된 브라우저</h3><span>{status?.credentials.length ?? 0}개</span></div>
        {status?.credentials.length ? status.credentials.map((credential) => (
          <article key={credential.id} className="companion-credential-row">
            <CheckCircle size={25} weight="fill" />
            <div><strong>{credential.label}</strong><span>최근 사용 {dateTimeLabel(credential.last_used_at)} · {credential.extension_origin}</span></div>
            <button type="button" className="button button-ghost compact" disabled={loading} onClick={() => void revoke(credential)} aria-label={`${credential.label} 연결 해제`}><Trash size={19} />해제</button>
          </article>
        )) : <div className="companion-empty">아직 연결된 브라우저가 없습니다.</div>}
      </section>

      {error && <div className="companion-notice is-error" role="alert"><WarningCircle size={22} /><span>{error}</span></div>}
      {feedback && <p className="korail-import-feedback is-success" role="status">{feedback}</p>}
    </>
  );
}
