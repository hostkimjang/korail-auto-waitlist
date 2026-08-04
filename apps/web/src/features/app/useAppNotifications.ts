import { useCallback, useReducer } from "react";

import {
  initialNotificationCenterState,
  notificationCenterReducer,
  type AppNotificationInput,
  type NotificationCenterState,
  type NotificationKind,
} from "./notificationCenter";

interface UseAppNotificationsResult {
  state: NotificationCenterState;
  push: (input: AppNotificationInput | string) => void;
  pushMany: (inputs: ReadonlyArray<AppNotificationInput>) => void;
  dismiss: (id: string) => void;
  dismissGroup: (kind: NotificationKind) => void;
  dismissTimed: () => void;
  clear: () => void;
}

export function useAppNotifications(): UseAppNotificationsResult {
  const [state, dispatch] = useReducer(
    notificationCenterReducer,
    initialNotificationCenterState,
  );
  const push = useCallback((input: AppNotificationInput | string) => {
    const receivedAt = new Date().toISOString();
    const normalized = typeof input === "string" ? { title: input } : input;
    dispatch({
      type: "push",
      inputs: [{ ...normalized, occurredAt: normalized.occurredAt ?? receivedAt }],
    });
  }, []);
  const pushMany = useCallback((inputs: ReadonlyArray<AppNotificationInput>) => {
    const receivedAt = new Date().toISOString();
    dispatch({
      type: "push",
      inputs: inputs.map((input) => ({
        ...input,
        occurredAt: input.occurredAt ?? input.revisionAt ?? receivedAt,
      })),
    });
  }, []);
  const dismiss = useCallback((id: string) => dispatch({ type: "dismiss", id }), []);
  const dismissGroup = useCallback(
    (kind: NotificationKind) => dispatch({ type: "dismiss_group", kind }),
    [],
  );
  const dismissTimed = useCallback(() => dispatch({ type: "dismiss_timed" }), []);
  const clear = useCallback(() => dispatch({ type: "clear" }), []);

  return { state, push, pushMany, dismiss, dismissGroup, dismissTimed, clear };
}
