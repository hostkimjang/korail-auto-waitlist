import type { ReactElement } from "react";

import { AppAuthenticationBoundary } from "./app/AppAuthenticationBoundary";
import {
  Home,
  NewWait,
  PaymentHero,
  Reservations,
} from "./app/AppCompatibility";
import { renderHomeSeatFoundAction } from "./app/HomeSeatFoundOfficialHandoff";
import { AppShell } from "./app/AppShell";
import { useAppNavigation } from "./app/useAppNavigation";
import { useAppLogout } from "./app/useAppLogout";
import {
  buildWatchCreatePayloads,
  cancelWatch,
  createWatch,
  deleteWatch,
  fetchWatches,
  pauseWatch as pauseWatchRequest,
  startWatch,
  updateWatch,
} from "./api/watches";
import { useAuthState } from "./features/auth/useAuthState";
import { AppNotificationCenter } from "./features/app/AppNotificationCenter";
import { useWatchCollection } from "./features/app/useWatchCollection";
import { mapWatchLifecycleSnapshot } from "./features/app/watchLifecycleSnapshot";
import { useWatchMutations } from "./features/app/useWatchMutations";
import { useDemoCaptureLifecycle } from "./features/app/useDemoCaptureLifecycle";
import { HomePage } from "./features/home/HomePage";
import { mapActiveWatch } from "./features/home/activeWatchViewModel";
import {
  mapLegacyPaymentRequiredWatch,
  mapPaymentRequiredWatch,
  type PaymentRequiredViewModel,
} from "./features/home/paymentRequiredViewModel";
import { ReservationsPage } from "./features/reservations/ReservationsPage";
import {
  mapLegacyReservationWatch,
  mapReservationWatch,
} from "./features/reservations/reservationViewModel";
import { NewWaitPage } from "./features/new-wait/NewWaitPage";
import { seatClassNames } from "./features/new-wait/TrainResultCard";
import type { SeatWatchRegistrationCompletion } from "./features/new-wait/useSeatWatchRegistration";
import { SettingsPage } from "./features/settings/SettingsPage";
import { useNotificationChannelSettings } from "./features/settings/useNotificationChannelSettings";
import { useProviderAccountSettings } from "./features/settings/useProviderAccountSettings";
import { useUiPreferencesSettings } from "./features/settings/useUiPreferencesSettings";
import { formatNewWaitDateLabel } from "./features/new-wait/newWaitForm";
import { useAppNotifications } from "./features/app/useAppNotifications";
import { isActiveWatch } from "./features/app/watchSelectors";
import { hasObservedSeatEvidence } from "./domain/seatEvidence";
import {
  DEMO_CAPTURE_RESERVATION_LIFECYCLE,
  DEMO_MODE,
} from "./shared/lib/runtimeConfig";
import { OfficialHandoff } from "./features/official-handoff/OfficialHandoff";
import type { WatchReadModel } from "./api/watches";
import {
  createDemoWatch,
  demoPaymentWatch,
  initialWatches,
} from "./fixtures/demoData";

const initialWatchCollection: WatchReadModel[] = DEMO_MODE ? initialWatches : [];

export { WatchRow } from "./features/home/ActiveWatchList";
export { Home, NewWait, PaymentHero, Reservations, isActiveWatch };
export { OfficialHandoff, hasObservedSeatEvidence };
export { SettingsPage as Settings };

export function App(): ReactElement {
  const {
    activeView,
    settingsInitialSection,
    settingsActiveSection,
    navigate,
    onSettingsSectionChange,
  } = useAppNavigation();
  const { auth, markAuthenticated, markUnauthenticated, retryAuthStatus } = useAuthState();
  const {
    state: notificationState,
    push: setToast,
    pushMany: pushNotifications,
    dismiss: dismissNotification,
    dismissGroup: dismissNotificationGroup,
    dismissTimed: dismissTimedNotifications,
    clear: clearNotifications,
  } = useAppNotifications();
  const {
    channels,
    browserPushState,
    saveChannel,
    toggleChannel,
    testChannel,
    connectWebPushChannel,
    reset: resetNotificationChannels,
  } = useNotificationChannelSettings({
    authenticated: auth.authenticated,
    demo: auth.demo,
    onAuthenticationExpired: markUnauthenticated,
    pushToast: setToast,
  });
  const {
    accounts: providerAccounts,
    runtimeStatuses: providerRuntimeStatuses,
    loading: providerAccountsLoading,
    pendingProvider: pendingProviderAccount,
    saveAccount: saveRailProviderAccount,
    deleteAccount: removeRailProviderAccount,
    onProviderAuthenticationTransition: handleProviderAuthenticationTransition,
    accountAuthStatusFor,
    reset: resetProviderAccounts,
  } = useProviderAccountSettings({
    authenticated: auth.authenticated,
    demo: auth.demo,
    runtimePollingEnabled: activeView === "settings"
      && settingsActiveSection === "rail-accounts",
    pushToast: setToast,
  });
  const {
    preferences: uiPreferences,
    saving: savingUiPreferences,
    save: saveUiPreferences,
    reset: resetUiPreferences,
  } = useUiPreferencesSettings({
    authenticated: auth.authenticated,
    demo: auth.demo,
    pushToast: setToast,
  });

  const {
    watches,
    commitWatches,
    refreshState: watchRefreshState,
    requestRefresh: requestWatchesRefresh,
    beginReservationPolicyMutation,
    endReservationPolicyMutation,
  } = useWatchCollection({
    authenticated: auth.authenticated,
    demo: auth.demo,
    initialWatches: initialWatchCollection,
    pollIntervalSeconds: uiPreferences.timetableRefreshIntervalSeconds,
    loadWatches: fetchWatches,
    snapshotOf: mapWatchLifecycleSnapshot,
    onAuthenticationExpired: markUnauthenticated,
    onProviderAuthenticationTransition: handleProviderAuthenticationTransition,
    pushNotifications,
  });
  useDemoCaptureLifecycle({
    enabled: auth.demo && DEMO_CAPTURE_RESERVATION_LIFECYCLE,
    watches,
    commitWatches,
    dismissTimedNotifications,
    pushNotifications,
  });
  const {
    pauseWatch,
    resumeWatch,
    cancelWatch: cancelWatchItem,
    changeReservationPolicy: changeWatchReservationPolicy,
    deleteWatchRecord,
    reservationPolicyUpdatingIds,
  } = useWatchMutations({
    demo: auth.demo,
    watches,
    commitWatches,
    pushToast: setToast,
    beginReservationPolicyMutation,
    endReservationPolicyMutation,
    requestWatchesRefresh,
    pauseWatchRequest,
    startWatchRequest: startWatch,
    cancelWatchRequest: cancelWatch,
    updateWatchRequest: updateWatch,
    deleteWatchRequest: deleteWatch,
  });

  const completeWizard: SeatWatchRegistrationCompletion = async ({ form, selectedTrains }) => {
    let createdWatches: WatchReadModel[];
    if (auth.demo) {
      createdWatches = selectedTrains.map((item, index) => {
        const seatClass = item.selected_seat_class || "any";
        const watchId = `watch-${Date.now()}-${item.id}-${seatClass}-${index}`;
        return createDemoWatch({
          id: watchId,
          provider: item.provider,
          train: item.name,
          route: `${form.origin} → ${form.destination}`,
          origin: form.origin,
          destination: form.destination,
          departure: item.departure,
          arrival: item.arrival,
          date: formatNewWaitDateLabel(form.date),
          travelDate: form.date,
          status: "watching",
          statusLabel: "감시 중",
          seatClass,
          seatClassLabel: seatClassNames[seatClass],
          seatEvidenceLabel: `${seatClassNames[seatClass]} · 데모 좌석 상태`,
          officialBookingUrl: item.official_booking_url,
          reservationPolicy: form.reservationPolicy,
          candidates: [{
            train_number: item.train_number ?? item.name,
            departure_at: item.departure_at,
            arrival_at: item.arrival_at ?? null,
            seat_class: seatClass,
            priority: 1,
          }],
        });
      });
    } else {
      const channelIds = channels.filter((channel) => form.channels.includes(channel.kind) && channel.enabled).map((channel) => channel.id);
      const payloads = buildWatchCreatePayloads(form, selectedTrains, channelIds);
      const created = await Promise.all(payloads.map((payload) => createWatch(payload)));
      createdWatches = await Promise.all(created.map((watch) => startWatch(watch.id)));
    }
    const createdIds = new Set(createdWatches.map((watch) => watch.id));
    commitWatches((items) => [...createdWatches, ...items.filter((item) => !createdIds.has(item.id))]);
    setToast("대기를 등록했습니다. 열차를 더 추가할 수 있습니다.");
    return createdWatches;
  };

  const signOut = useAppLogout({
    demo: auth.demo,
    commitWatches,
    resetNotificationChannels,
    resetProviderAccounts,
    resetUiPreferences,
    clearNotifications,
    markUnauthenticated,
  });

  const paymentWatches: PaymentRequiredViewModel[] = watches.filter(
    (watch) => watch.status === "payment_required",
  ).map(mapPaymentRequiredWatch);
  if (
    auth.demo
    && !DEMO_CAPTURE_RESERVATION_LIFECYCLE
    && paymentWatches.length === 0
  ) {
    paymentWatches.push(mapLegacyPaymentRequiredWatch(demoPaymentWatch));
  }
  const activeWatches = watches.filter(isActiveWatch).map((watch) => (
    mapActiveWatch(watch, accountAuthStatusFor(watch.provider))
  ));
  const reservationWatches = watches.map(mapReservationWatch);
  if (auth.demo && !watches.some((watch) => watch.id === demoPaymentWatch.id)) {
    reservationWatches.unshift(mapLegacyReservationWatch(demoPaymentWatch));
  }
  return (
    <AppAuthenticationBoundary
      status={auth}
      onAuthenticated={markAuthenticated}
      onRetryStatus={retryAuthStatus}
    >
      <AppShell
        activeView={activeView}
        onNavigate={navigate}
        overlay={<AppNotificationCenter
          state={notificationState}
          onDismiss={dismissNotification}
          onDismissGroup={dismissNotificationGroup}
          onDismissTimed={dismissTimedNotifications}
        />}
      >
        {activeView === "home" && <HomePage watches={activeWatches} paymentWatches={paymentWatches} watchRefreshState={watchRefreshState} onRefreshWatches={requestWatchesRefresh} onCreate={() => navigate("new")} onViewReservations={() => navigate("reservations")} onOpenRailAccounts={() => navigate("settings", "rail-accounts")} onPause={pauseWatch} onResume={resumeWatch} onCancel={async (watchId) => { await cancelWatchItem(watchId); }} onChangeReservationPolicy={changeWatchReservationPolicy} reservationPolicyUpdatingIds={reservationPolicyUpdatingIds} onToast={setToast} renderSeatFoundAction={renderHomeSeatFoundAction} />}
        {activeView === "new" && <NewWaitPage demo={auth.demo} watches={watches} providerAccounts={providerAccounts} refreshIntervalSeconds={uiPreferences.timetableRefreshIntervalSeconds} onComplete={completeWizard} onCancelWatch={cancelWatchItem} onCancel={() => navigate("home")} officialHandoffComponent={OfficialHandoff} />}
        {activeView === "reservations" && <ReservationsPage watches={reservationWatches} onCreate={() => navigate("new")} onDelete={deleteWatchRecord} />}
        {activeView === "settings" && <SettingsPage channels={channels} demo={auth.demo} browserPushState={browserPushState} providerAccounts={providerAccounts} providerRuntimeStatuses={providerRuntimeStatuses} providerAccountsLoading={providerAccountsLoading} pendingProviderAccount={pendingProviderAccount} uiPreferences={uiPreferences} savingUiPreferences={savingUiPreferences} onSaveUiPreferences={saveUiPreferences} onSaveChannel={saveChannel} onToggleChannel={toggleChannel} onTestChannel={testChannel} onConnectWebPush={connectWebPushChannel} onSaveProviderAccount={saveRailProviderAccount} onDeleteProviderAccount={removeRailProviderAccount} onSectionChange={onSettingsSectionChange} onLogout={signOut} initialSection={settingsInitialSection} />}
      </AppShell>
    </AppAuthenticationBoundary>
  );
}
