import { BellRinging, WarningCircle } from "@phosphor-icons/react";
import { useState, type ReactElement } from "react";

import type {
  BrowserPushState,
  NotificationChannel,
} from "../../api/notifications";

export interface WebPushEnrollmentPromptProps {
  browserPushState: BrowserPushState;
  channels: readonly NotificationChannel[];
  channelsLoaded: boolean;
  demo: boolean;
  suppressed: boolean;
  onConnect: () => Promise<void>;
}

export function WebPushEnrollmentPrompt({
  browserPushState,
  channels,
  channelsLoaded,
  demo,
  suppressed,
  onConnect,
}: WebPushEnrollmentPromptProps): ReactElement | null {
  const [connecting, setConnecting] = useState(false);
  if (demo || !channelsLoaded || browserPushState.support !== "supported") return null;

  const currentChannel = browserPushState.deviceKey === null
    ? null
    : channels.find((channel) => (
      channel.kind === "web_push" && channel.deviceKey === browserPushState.deviceKey
    )) ?? null;
  const connected = browserPushState.subscribed && currentChannel?.enabled === true;
  if (connected || (suppressed && browserPushState.permission !== "default")) return null;

  const blocked = browserPushState.permission === "denied";
  return (
    <aside
      className={`web-push-enrollment${blocked ? " is-blocked" : ""}`}
      role="region"
      aria-label="OS 알림 연결"
    >
      {blocked
        ? <WarningCircle size={24} weight="fill" aria-hidden="true" />
        : <BellRinging size={24} weight="fill" aria-hidden="true" />}
      <div>
        <strong>{blocked ? "OS 알림이 차단되어 있습니다" : "OS 알림을 켜 주세요"}</strong>
        <span>
          {blocked
            ? "브라우저의 이 사이트 권한에서 알림을 허용해야 다시 연결할 수 있습니다."
            : "설정 화면으로 이동하지 않고 이 기기의 권한 요청과 알림 연결을 진행합니다."}
        </span>
      </div>
      {!blocked && (
        <button
          type="button"
          disabled={connecting}
          onClick={() => {
            setConnecting(true);
            void onConnect().finally(() => setConnecting(false));
          }}
        >
          {connecting ? "연결 중" : "OS 알림 켜기"}
        </button>
      )}
    </aside>
  );
}
