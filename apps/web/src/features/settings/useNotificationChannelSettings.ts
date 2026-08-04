import { useCallback, useEffect, useState } from "react";

import { ApiError } from "../../api/client";
import {
  connectBrowserPush,
  createNotificationChannel,
  disconnectBrowserPush,
  fetchNotificationChannels,
  readBrowserPushState,
  testNotificationChannel,
  updateNotificationChannel,
  type BrowserPushState,
  type NotificationChannel,
  type NotificationChannelEditorSubmission,
} from "../../api/notifications";

export interface UseNotificationChannelSettingsOptions {
  authenticated: boolean;
  demo: boolean;
  onAuthenticationExpired: () => void;
  pushToast: (message: string) => void;
}

export interface NotificationChannelSettingsController {
  channels: readonly NotificationChannel[];
  browserPushState: BrowserPushState;
  saveChannel: (submission: NotificationChannelEditorSubmission) => Promise<void>;
  toggleChannel: (channel: NotificationChannel, nextEnabled: boolean) => Promise<void>;
  testChannel: (channel: NotificationChannel) => Promise<void>;
  connectWebPushChannel: () => Promise<void>;
  reset: () => void;
}

const initialBrowserPushState: BrowserPushState = {
  support: "checking",
  permission: "default",
  subscribed: false,
};

const unavailableBrowserPushState: BrowserPushState = {
  support: "unsupported",
  permission: "default",
  subscribed: false,
};

function errorMessage(reason: unknown, fallback: string): string {
  return reason instanceof Error && reason.message ? reason.message : fallback;
}

function replaceChannel(
  channels: readonly NotificationChannel[],
  saved: NotificationChannel,
): NotificationChannel[] {
  return [saved, ...channels.filter((channel) => channel.kind !== saved.kind)];
}

export function useNotificationChannelSettings({
  authenticated,
  demo,
  onAuthenticationExpired,
  pushToast,
}: UseNotificationChannelSettingsOptions): NotificationChannelSettingsController {
  const [channels, setChannels] = useState<NotificationChannel[]>([]);
  const [browserPushState, setBrowserPushState] = useState<BrowserPushState>(
    initialBrowserPushState,
  );

  useEffect(() => {
    if (!authenticated || demo) return undefined;
    let active = true;
    void fetchNotificationChannels().then((channelItems) => {
      if (active) setChannels(channelItems);
    }).catch((reason: unknown) => {
      if (active && reason instanceof ApiError && reason.status === 401) {
        onAuthenticationExpired();
      }
    });
    return () => {
      active = false;
    };
  }, [authenticated, demo, onAuthenticationExpired]);

  useEffect(() => {
    if (!authenticated || demo) return undefined;
    let active = true;
    const refresh = (): void => {
      void readBrowserPushState().then((state) => {
        if (active) setBrowserPushState(state);
      }).catch(() => {
        if (active) setBrowserPushState(unavailableBrowserPushState);
      });
    };
    refresh();
    window.addEventListener("focus", refresh);
    return () => {
      active = false;
      window.removeEventListener("focus", refresh);
    };
  }, [authenticated, demo]);

  const saveChannel = useCallback(async (
    submission: NotificationChannelEditorSubmission,
  ): Promise<void> => {
    try {
      const existing = channels.find((channel) => channel.kind === submission.kind);
      const saved = existing
        ? await updateNotificationChannel(existing.id, {
          name: submission.name,
          config: submission.config,
          enabled: true,
        })
        : await createNotificationChannel({ ...submission, enabled: true });
      setChannels((items) => replaceChannel(items, saved));
      pushToast("알림 채널을 연결했습니다.");
    } catch (reason: unknown) {
      pushToast(errorMessage(reason, "알림 채널을 연결하지 못했습니다."));
      throw reason;
    }
  }, [channels, pushToast]);

  const toggleChannel = useCallback(async (
    channel: NotificationChannel,
    nextEnabled: boolean,
  ): Promise<void> => {
    try {
      if (demo) {
        setChannels((items) => items.map((item) => (
          item.id === channel.id ? { ...item, enabled: nextEnabled } : item
        )));
        return;
      }
      if (channel.kind === "web_push") {
        if (nextEnabled) {
          const updated = await connectBrowserPush(channel.name, channel.id);
          setChannels((items) => items.map((item) => (
            item.id === channel.id ? updated : item
          )));
        } else {
          const updated = await updateNotificationChannel(channel.id, { enabled: false });
          setChannels((items) => items.map((item) => (
            item.id === channel.id ? updated : item
          )));
          await disconnectBrowserPush();
        }
        setBrowserPushState(await readBrowserPushState());
        return;
      }
      const updated = await updateNotificationChannel(channel.id, { enabled: nextEnabled });
      setChannels((items) => items.map((item) => (
        item.id === channel.id ? updated : item
      )));
    } catch (reason: unknown) {
      pushToast(errorMessage(reason, "알림 채널 상태를 변경하지 못했습니다."));
    }
  }, [demo, pushToast]);

  const testChannel = useCallback(async (channel: NotificationChannel): Promise<void> => {
    try {
      if (channel.kind === "web_push") {
        const state = await readBrowserPushState();
        setBrowserPushState(state);
        if (state.permission !== "granted" || !state.subscribed) {
          throw new ApiError("이 기기의 OS 알림 구독을 먼저 켜 주세요.");
        }
      }
      if (!demo) await testNotificationChannel(channel.id);
      pushToast(`${channel.name} 시험 알림을 전송 대기열에 넣었습니다.`);
    } catch (reason: unknown) {
      pushToast(errorMessage(reason, "시험 알림을 보내지 못했습니다."));
    }
  }, [demo, pushToast]);

  const connectWebPushChannel = useCallback(async (): Promise<void> => {
    try {
      if (demo) {
        const now = new Date().toISOString();
        const saved: NotificationChannel = {
          id: "demo-web-push",
          kind: "web_push",
          name: "이 브라우저",
          enabled: true,
          configured: true,
          createdAt: now,
          updatedAt: now,
        };
        setChannels((items) => replaceChannel(items, saved));
        setBrowserPushState({ support: "supported", permission: "granted", subscribed: true });
      } else {
        const existing = channels.find((channel) => channel.kind === "web_push");
        const saved = await connectBrowserPush(
          existing?.name ?? "이 브라우저",
          existing?.id ?? null,
        );
        setChannels((items) => replaceChannel(items, saved));
        setBrowserPushState(await readBrowserPushState());
      }
      pushToast("이 기기의 OS 알림을 연결했습니다.");
    } catch (reason: unknown) {
      pushToast(errorMessage(reason, "이 기기의 OS 알림을 연결하지 못했습니다."));
    }
  }, [channels, demo, pushToast]);

  const reset = useCallback((): void => {
    setChannels([]);
    setBrowserPushState(initialBrowserPushState);
  }, []);

  return {
    channels,
    browserPushState,
    saveChannel,
    toggleChannel,
    testChannel,
    connectWebPushChannel,
    reset,
  };
}
