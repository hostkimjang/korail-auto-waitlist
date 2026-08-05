import { useCallback, type Dispatch, type SetStateAction } from "react";

import { logout } from "../api/auth";
import type { WatchReadModel } from "../api/watches";

export interface UseAppLogoutOptions {
  demo: boolean;
  commitWatches: Dispatch<SetStateAction<ReadonlyArray<WatchReadModel>>>;
  resetNotificationChannels: () => void;
  resetProviderAccounts: () => void;
  resetUiPreferences: () => void;
  clearNotifications: () => void;
  markUnauthenticated: () => void;
  logoutRequest?: () => Promise<unknown>;
}

export function useAppLogout({
  demo,
  commitWatches,
  resetNotificationChannels,
  resetProviderAccounts,
  resetUiPreferences,
  clearNotifications,
  markUnauthenticated,
  logoutRequest = logout,
}: UseAppLogoutOptions): () => Promise<void> {
  return useCallback(async (): Promise<void> => {
    try {
      if (!demo) await logoutRequest();
    } finally {
      commitWatches([]);
      resetNotificationChannels();
      resetProviderAccounts();
      resetUiPreferences();
      clearNotifications();
      markUnauthenticated();
    }
  }, [
    clearNotifications,
    commitWatches,
    demo,
    logoutRequest,
    markUnauthenticated,
    resetNotificationChannels,
    resetProviderAccounts,
    resetUiPreferences,
  ]);
}
