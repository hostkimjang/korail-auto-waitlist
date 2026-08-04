import { logout } from "./api/auth";
import { AppShell } from "./app/AppShell";
import { useAppNavigation } from "./app/useAppNavigation";
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
import { AuthGate } from "./features/auth/AuthGate";
import { useAuthState } from "./features/auth/useAuthState";
import { AppNotificationCenter } from "./features/app/AppNotificationCenter";
import { useWatchCollection } from "./features/app/useWatchCollection";
import { useWatchMutations } from "./features/app/useWatchMutations";
import { HomePage } from "./features/home/HomePage";
import { PaymentRequiredSection } from "./features/home/PaymentRequiredSection";
import { ReservationsPage } from "./features/reservations/ReservationsPage";
import { NewWaitPage } from "./features/new-wait/NewWaitPage";
import {
  copyTrainJourney,
  seatClassNames,
} from "./features/new-wait/TrainResultCard";
import { SettingsPage } from "./features/settings/SettingsPage";
import { useNotificationChannelSettings } from "./features/settings/useNotificationChannelSettings";
import { useProviderAccountSettings } from "./features/settings/useProviderAccountSettings";
import { useUiPreferencesSettings } from "./features/settings/useUiPreferencesSettings";
import { formatNewWaitDateLabel } from "./features/new-wait/newWaitForm";
import { useAppNotifications } from "./features/app/useAppNotifications";
import { hasObservedSeatEvidence } from "./domain/seatEvidence";
import { DEMO_MODE } from "./shared/lib/runtimeConfig";
import { OfficialHandoff } from "./features/official-handoff/OfficialHandoff";
import {
  createDemoWatch,
  demoPaymentWatch,
  initialWatches,
} from "./fixtures/demoData";

const activeWatchStatuses = new Set(["draft", "scheduled", "watching", "official_waitlist", "seat_found", "reserving", "paused", "cooldown", "auth_required"]);
const initialWatchCollection = DEMO_MODE ? initialWatches : [];

export function isActiveWatch(watch) {
  return activeWatchStatuses.has(watch.status);
}

export function PaymentHero({ watch, onOfficialPayment }) {
  return <PaymentRequiredSection watches={[watch]} onOpenPayment={() => onOfficialPayment()} />;
}

function activeWatchHandoffTrain(watch) {
  const [routeOrigin = "", routeDestination = ""] = String(watch.route ?? "").split(" → ");
  const origin = watch.origin || routeOrigin;
  const destination = watch.destination || routeDestination;
  const travelDate = watch.travelDate || "";
  return {
    id: `active-watch-${watch.id}`,
    provider: watch.provider,
    origin,
    destination,
    name: watch.train,
    date: watch.date,
    departure_at: travelDate ? `${travelDate}T${watch.departure}:00+09:00` : "",
    departure: watch.departure,
    arrival: watch.arrival,
    seat_classes: [],
  };
}

function renderHomeSeatFoundAction(watch) {
  return (
    <OfficialHandoff
      train={activeWatchHandoffTrain(watch)}
      selectedSeatClass={watch.seatClass}
      onCopy={copyTrainJourney}
      triggerLabel="예매"
      actionUrl={watch.officialBookingUrl}
      seatFoundObservation={watch.seatFoundObservation}
      triggerClassName="button button-primary compact watch-booking-button"
    />
  );
}

/** @param {import("./features/home/HomePage").HomeCompatibilityProps} props */
export function Home({
  watches,
  paymentWatch = null,
  paymentWatches = paymentWatch ? [paymentWatch] : [],
  watchRefreshState = { isRefreshing: false, lastRefreshedAt: null },
  onRefreshWatches = undefined,
  onNavigate,
  onPause,
  onResume,
  onCancel,
  onChangeReservationPolicy = undefined,
  reservationPolicyUpdatingIds = new Set(),
  onToast,
}) {
  return (
    <HomePage
      watches={watches}
      paymentWatches={paymentWatches}
      watchRefreshState={watchRefreshState}
      {...(onRefreshWatches ? { onRefreshWatches } : {})}
      onCreate={() => onNavigate("new")}
      onViewReservations={() => onNavigate("reservations")}
      onOpenRailAccounts={() => onNavigate("settings", "rail-accounts")}
      onPause={onPause}
      onResume={onResume}
      onCancel={onCancel}
      {...(onChangeReservationPolicy ? { onChangeReservationPolicy } : {})}
      reservationPolicyUpdatingIds={reservationPolicyUpdatingIds}
      onToast={onToast}
      renderSeatFoundAction={renderHomeSeatFoundAction}
    />
  );
}

export { WatchRow } from "./features/home/ActiveWatchList";
export { OfficialHandoff, hasObservedSeatEvidence };

/** @param {Omit<import("./features/new-wait/NewWaitPage").NewWaitPageProps, "officialHandoffComponent">} props */
export function NewWait(props) {
  return <NewWaitPage {...props} officialHandoffComponent={OfficialHandoff} />;
}

export function Reservations({ watches, onNavigate, onDelete = () => undefined }) {
  return <ReservationsPage watches={watches} onCreate={() => onNavigate("new")} onDelete={onDelete} />;
}

export { SettingsPage as Settings };

export function App() {
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
    onAuthenticationExpired: markUnauthenticated,
    onProviderAuthenticationTransition: handleProviderAuthenticationTransition,
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

  const completeWizard = async ({ form, selectedTrains }) => {
    let createdWatches;
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

  const signOut = async () => {
    try {
      if (!auth.demo) await logout();
    } finally {
      commitWatches([]);
      resetNotificationChannels();
      resetProviderAccounts();
      resetUiPreferences();
      clearNotifications();
      markUnauthenticated();
    }
  };

  if (auth.loading) return <main className="auth-page"><div className="loading-state"><img src="/icons/app-icon-512.png" alt="" /><span>안전하게 연결하는 중…</span></div></main>;
  if (!auth.authenticated) return <AuthGate status={auth} onAuthenticated={markAuthenticated} onRetryStatus={retryAuthStatus} />;

  const paymentWatches = watches.filter((watch) => watch.status === "payment_required");
  if (auth.demo && paymentWatches.length === 0) paymentWatches.push(demoPaymentWatch);
  const activeWatches = watches.filter(isActiveWatch).map((watch) => {
    const accountAuthStatus = accountAuthStatusFor(watch.provider);
    return { ...watch, accountAuthStatus };
  });
  const reservationWatches = auth.demo && !watches.some((watch) => watch.id === demoPaymentWatch.id)
    ? [demoPaymentWatch, ...watches]
    : watches;
  return (
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
      {activeView === "home" && <HomePage watches={activeWatches} paymentWatches={paymentWatches} watchRefreshState={watchRefreshState} onRefreshWatches={requestWatchesRefresh} onCreate={() => navigate("new")} onViewReservations={() => navigate("reservations")} onOpenRailAccounts={() => navigate("settings", "rail-accounts")} onPause={pauseWatch} onResume={resumeWatch} onCancel={cancelWatchItem} onChangeReservationPolicy={changeWatchReservationPolicy} reservationPolicyUpdatingIds={reservationPolicyUpdatingIds} onToast={setToast} renderSeatFoundAction={renderHomeSeatFoundAction} />}
      {activeView === "new" && <NewWaitPage demo={auth.demo} watches={watches} providerAccounts={providerAccounts} refreshIntervalSeconds={uiPreferences.timetableRefreshIntervalSeconds} onComplete={completeWizard} onCancelWatch={cancelWatchItem} onCancel={() => navigate("home")} officialHandoffComponent={OfficialHandoff} />}
      {activeView === "reservations" && <ReservationsPage watches={reservationWatches} onCreate={() => navigate("new")} onDelete={deleteWatchRecord} />}
      {activeView === "settings" && <SettingsPage channels={channels} demo={auth.demo} browserPushState={browserPushState} providerAccounts={providerAccounts} providerRuntimeStatuses={providerRuntimeStatuses} providerAccountsLoading={providerAccountsLoading} pendingProviderAccount={pendingProviderAccount} uiPreferences={uiPreferences} savingUiPreferences={savingUiPreferences} onSaveUiPreferences={saveUiPreferences} onSaveChannel={saveChannel} onToggleChannel={toggleChannel} onTestChannel={testChannel} onConnectWebPush={connectWebPushChannel} onSaveProviderAccount={saveRailProviderAccount} onDeleteProviderAccount={removeRailProviderAccount} onSectionChange={onSettingsSectionChange} onLogout={signOut} initialSection={settingsInitialSection} />}
    </AppShell>
  );
}
