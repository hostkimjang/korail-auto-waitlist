import { useMemo, useRef, useState, type ReactNode } from "react";
import {
  Bell,
  DiscordLogo,
  GlobeSimple,
  PaperPlaneTilt,
} from "@phosphor-icons/react";

import type {
  BrowserPushState,
  NotificationChannel,
  NotificationChannelEditorSubmission,
  NotificationChannelKind,
} from "../../api/notifications";

type ConfigurableNotificationChannelKind = Exclude<NotificationChannelKind, "web_push">;

interface NotificationEditorDraft {
  name: string;
  token: string;
  chatId: string;
  url: string;
  authorization: string;
}

export interface NotificationChannelSettingsProps {
  channels: readonly NotificationChannel[];
  browserPushState: BrowserPushState;
  onSaveChannel: (submission: NotificationChannelEditorSubmission) => Promise<void>;
  onToggleChannel: (channel: NotificationChannel, nextEnabled: boolean) => Promise<void>;
  onTestChannel: (channel: NotificationChannel) => Promise<void>;
  onConnectWebPush: () => Promise<void>;
}

interface NotificationFieldProps {
  label: string;
  children: ReactNode;
}

const notificationOptions = [
  {
    id: "web_push",
    label: "OS 알림",
    helper: "Windows·Android·iOS 시스템 알림",
    icon: Bell,
  },
  {
    id: "telegram",
    label: "텔레그램",
    helper: "Bot API로 즉시 전송",
    icon: PaperPlaneTilt,
  },
  {
    id: "discord_webhook",
    label: "디스코드",
    helper: "Webhook 채널 알림",
    icon: DiscordLogo,
  },
  {
    id: "generic_webhook",
    label: "범용 Webhook",
    helper: "HTTPS JSON endpoint",
    icon: GlobeSimple,
  },
] as const;

function emptyDraft(): NotificationEditorDraft {
  return { name: "", token: "", chatId: "", url: "", authorization: "" };
}

function NotificationField({ label, children }: NotificationFieldProps) {
  return <label className="field"><span>{label}</span>{children}</label>;
}

function editorSubmission(
  kind: ConfigurableNotificationChannelKind,
  draft: NotificationEditorDraft,
): NotificationChannelEditorSubmission {
  const option = notificationOptions.find((item) => item.id === kind);
  const name = draft.name.trim() || option?.label || "알림 채널";
  if (kind === "telegram") {
    return {
      kind,
      name,
      config: { bot_token: draft.token.trim(), chat_id: draft.chatId.trim() },
    };
  }
  return {
    kind,
    name,
    config: {
      url: draft.url.trim(),
      ...(kind === "generic_webhook" && draft.authorization.trim()
        ? { authorization: draft.authorization.trim() }
        : {}),
    },
  };
}

function editorValidationError(
  kind: ConfigurableNotificationChannelKind,
  draft: NotificationEditorDraft,
): string | null {
  if (draft.name.trim().length > 80) {
    return "표시 이름은 80자 이하로 입력해 주세요.";
  }
  if (kind === "telegram") {
    if (!draft.token.trim()) return "Telegram Bot token을 입력해 주세요.";
    if (!draft.chatId.trim()) return "Telegram Chat ID를 입력해 주세요.";
    return null;
  }
  const url = draft.url.trim();
  if (!url) return "Webhook HTTPS URL을 입력해 주세요.";
  try {
    if (new URL(url).protocol !== "https:") {
      return "Webhook URL은 HTTPS 주소로 입력해 주세요.";
    }
  } catch {
    return "올바른 Webhook HTTPS URL을 입력해 주세요.";
  }
  return null;
}

export function NotificationChannelSettings({
  channels,
  browserPushState,
  onSaveChannel,
  onToggleChannel,
  onTestChannel,
  onConnectWebPush,
}: NotificationChannelSettingsProps) {
  const [editingKind, setEditingKind] = useState<ConfigurableNotificationChannelKind | null>(
    null,
  );
  const [draft, setDraft] = useState<NotificationEditorDraft>(emptyDraft);
  const [editorError, setEditorError] = useState<string | null>(null);
  const [pendingActions, setPendingActions] = useState<ReadonlySet<string>>(() => new Set());
  const editorHeadingRef = useRef<HTMLHeadingElement>(null);
  const configuredByKind = useMemo(
    () => new Map(
      channels
        .filter((channel) => channel.kind !== "web_push")
        .map((channel) => [channel.kind, channel] as const),
    ),
    [channels],
  );
  const webPushChannels = useMemo(
    () => channels.filter((channel) => channel.kind === "web_push" && channel.configured),
    [channels],
  );
  const currentWebPushChannel = useMemo(
    () => browserPushState.deviceKey === null
      ? undefined
      : webPushChannels.find((channel) => channel.deviceKey === browserPushState.deviceKey),
    [browserPushState.deviceKey, webPushChannels],
  );
  const activeWebPushDeviceCount = useMemo(() => {
    const reportedCount = webPushChannels.find(
      (channel) => channel.activeDeviceCount !== null,
    )?.activeDeviceCount;
    return reportedCount ?? webPushChannels.filter((channel) => channel.enabled).length;
  }, [webPushChannels]);

  const startAction = (key: string): void => {
    setPendingActions((current) => new Set(current).add(key));
  };

  const finishAction = (key: string): void => {
    setPendingActions((current) => {
      const next = new Set(current);
      next.delete(key);
      return next;
    });
  };

  const clearEditor = (): void => {
    setEditingKind(null);
    setDraft(emptyDraft());
    setEditorError(null);
  };

  const beginConfigure = async (kind: NotificationChannelKind): Promise<void> => {
    if (kind === "web_push") {
      const actionKey = "connect:web_push";
      startAction(actionKey);
      try {
        await onConnectWebPush();
      } catch {
        // The App notification center owns the public API error.
      } finally {
        finishAction(actionKey);
      }
      return;
    }
    const existing = configuredByKind.get(kind);
    setDraft({ ...emptyDraft(), name: existing?.name ?? "" });
    setEditorError(null);
    setEditingKind(kind);
    window.requestAnimationFrame(() => editorHeadingRef.current?.focus());
  };

  const saveDraft = async (): Promise<void> => {
    if (editingKind === null) return;
    const validationError = editorValidationError(editingKind, draft);
    if (validationError !== null) {
      setEditorError(validationError);
      return;
    }
    const actionKey = `save:${editingKind}`;
    setEditorError(null);
    startAction(actionKey);
    try {
      await onSaveChannel(editorSubmission(editingKind, draft));
      clearEditor();
    } catch {
      // The App notification center owns the public API error. Keep the draft for retry.
    } finally {
      finishAction(actionKey);
    }
  };

  const toggleOption = async (
    kind: NotificationChannelKind,
    channel: NotificationChannel | undefined,
    nextEnabled: boolean,
  ): Promise<void> => {
    if (!channel) {
      await beginConfigure(kind);
      return;
    }
    const actionKey = `toggle:${kind}`;
    startAction(actionKey);
    try {
      await onToggleChannel(channel, nextEnabled);
    } catch {
      // The App notification center owns the public API error.
    } finally {
      finishAction(actionKey);
    }
  };

  const testOption = async (
    kind: NotificationChannelKind,
    channel: NotificationChannel,
  ): Promise<void> => {
    const actionKey = `test:${kind}`;
    startAction(actionKey);
    try {
      await onTestChannel(channel);
    } catch {
      // The App notification center owns the public API error.
    } finally {
      finishAction(actionKey);
    }
  };

  return (
    <>
      <div className="panel-heading">
        <h2>알림 채널</h2>
        <p>여러 채널을 함께 켜 중요한 알림 누락을 줄입니다.</p>
      </div>
      <div className="settings-list">
        {notificationOptions.map(({ id, label, helper, icon: Icon }) => {
          const storedChannel = id === "web_push"
            ? currentWebPushChannel
            : configuredByKind.get(id);
          const channel = storedChannel?.configured ? storedChannel : undefined;
          const isPending = [...pendingActions].some((key) => key.endsWith(`:${id}`));
          const isWebPush = id === "web_push";
          const webPushReady = browserPushState.support === "supported"
            && browserPushState.permission === "granted"
            && browserPushState.subscribed
            && channel?.deviceKey === browserPushState.deviceKey;
          const checked = isWebPush
            ? Boolean(
              channel?.enabled
              && (browserPushState.support === "checking" || webPushReady),
            )
            : Boolean(channel?.enabled);
          const webPushDetail = browserPushState.support === "checking"
            ? `이 기기 구독 확인 중… · 전체 활성 기기 ${activeWebPushDeviceCount}대`
            : browserPushState.support === "unsupported"
            ? `이 브라우저는 OS 알림을 지원하지 않음 · 전체 활성 기기 ${activeWebPushDeviceCount}대`
            : browserPushState.support === "insecure"
              ? `HTTPS 또는 localhost 접속 필요 · 전체 활성 기기 ${activeWebPushDeviceCount}대`
              : browserPushState.permission === "denied"
                ? `브라우저 사이트 설정에서 알림 권한이 차단됨 · 전체 활성 기기 ${activeWebPushDeviceCount}대`
                : channel?.enabled && webPushReady
                  ? `이 기기 사용 중 · 전체 활성 기기 ${activeWebPushDeviceCount}대`
                  : channel
                    ? `이 기기 꺼짐 · 전체 활성 기기 ${activeWebPushDeviceCount}대`
                    : browserPushState.subscribed
                      ? `이 기기는 서버에 연결되지 않음 · 전체 활성 기기 ${activeWebPushDeviceCount}대`
                      : `이 기기 연결 안 됨 · 전체 활성 기기 ${activeWebPushDeviceCount}대`;
          const detail = isPending
            ? "처리 중…"
            : isWebPush
              ? webPushDetail
              : channel
                ? `${channel.name} · ${channel.enabled ? "사용 중" : "꺼짐"}`
                : helper;
          return (
            <div key={id} className="setting-row" aria-busy={isPending || undefined}>
              <Icon size={25} />
              <div>
                <strong>{label}</strong>
                <span>{detail}</span>
                {isWebPush && (
                  <small className="setting-row-note">
                    기기·브라우저마다 한 번씩 연결하면 Chrome·Edge·설치한 PWA에 함께
                    전달됩니다. 이 스위치는 다른 기기를 건드리지 않고 현재 기기만 켜거나
                    끕니다. iOS·iPadOS 16.4 이상은 홈 화면에 설치한 PWA에서 지원됩니다.
                  </small>
                )}
              </div>
              {channel ? (
                <button
                  type="button"
                  className="button button-ghost compact"
                  disabled={isPending || !channel.enabled || (isWebPush && !webPushReady)}
                  aria-label={`${label} 시험 알림 보내기`}
                  onClick={() => testOption(id, channel)}
                >
                  시험
                </button>
              ) : (
                <button
                  type="button"
                  className="button button-ghost compact"
                  disabled={isPending}
                  aria-label={`${label} 연결 설정 열기`}
                  aria-controls={isWebPush ? undefined : `${id}-channel-editor`}
                  aria-expanded={isWebPush ? undefined : editingKind === id}
                  onClick={() => beginConfigure(id)}
                >
                  {isPending ? "연결 중…" : "설정"}
                </button>
              )}
              <label className="switch">
                <input
                  type="checkbox"
                  disabled={
                    isPending
                    || (isWebPush && ["unsupported", "insecure"].includes(
                      browserPushState.support,
                    ))
                  }
                  aria-label={`${label} ${checked ? "끄기" : "켜기"}`}
                  aria-controls={channel || isWebPush ? undefined : `${id}-channel-editor`}
                  aria-expanded={channel || isWebPush ? undefined : editingKind === id}
                  checked={checked}
                  onChange={(event) => toggleOption(id, channel, event.target.checked)}
                />
                <span />
              </label>
            </div>
          );
        })}
      </div>
      {editingKind !== null && (
        <form
          id={`${editingKind}-channel-editor`}
          className="channel-editor"
          noValidate
          onSubmit={(event) => {
            event.preventDefault();
            void saveDraft();
          }}
        >
          <h3 ref={editorHeadingRef} tabIndex={-1}>
            {notificationOptions.find((item) => item.id === editingKind)?.label} 연결
          </h3>
          <NotificationField label="표시 이름">
            <input
              name="railwait-notification-name"
              autoComplete="off"
              value={draft.name}
              onChange={(event) => setDraft((current) => ({
                ...current,
                name: event.target.value,
              }))}
              placeholder="내 알림"
            />
          </NotificationField>
          {editingKind === "telegram" ? (
            <div className="form-grid">
              <NotificationField label="Bot token">
                <input
                  type="password"
                  name="railwait-telegram-bot-token"
                  value={draft.token}
                  onChange={(event) => {
                    setEditorError(null);
                    setDraft((current) => ({ ...current, token: event.target.value }));
                  }}
                  autoComplete="new-password"
                  data-lpignore="true"
                />
              </NotificationField>
              <NotificationField label="Chat ID">
                <input
                  name="railwait-telegram-chat-id"
                  autoComplete="off"
                  value={draft.chatId}
                  onChange={(event) => {
                    setEditorError(null);
                    setDraft((current) => ({ ...current, chatId: event.target.value }));
                  }}
                />
              </NotificationField>
            </div>
          ) : (
            <>
              <NotificationField label="HTTPS URL">
                <input
                  type="url"
                  name={`railwait-${editingKind}-url`}
                  autoComplete="off"
                  value={draft.url}
                  onChange={(event) => {
                    setEditorError(null);
                    setDraft((current) => ({ ...current, url: event.target.value }));
                  }}
                  placeholder="https://"
                />
              </NotificationField>
              {editingKind === "generic_webhook" && (
                <NotificationField label="Authorization (선택)">
                  <input
                    type="password"
                    name="railwait-webhook-authorization"
                    value={draft.authorization}
                    onChange={(event) => {
                      setEditorError(null);
                      setDraft((current) => ({
                        ...current,
                        authorization: event.target.value,
                      }));
                    }}
                    autoComplete="new-password"
                    data-lpignore="true"
                  />
                </NotificationField>
              )}
            </>
          )}
          {editorError !== null && (
            <p className="channel-editor-error" role="alert">{editorError}</p>
          )}
          <div className="editor-actions">
            <button
              type="button"
              className="button button-ghost"
              disabled={pendingActions.has(`save:${editingKind}`)}
              onClick={clearEditor}
            >
              취소
            </button>
            <button
              type="submit"
              className="button button-primary"
              disabled={pendingActions.has(`save:${editingKind}`)}
            >
              {pendingActions.has(`save:${editingKind}`) ? "저장 중…" : "저장"}
            </button>
          </div>
        </form>
      )}
    </>
  );
}
