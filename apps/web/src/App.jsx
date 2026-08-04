import { useCallback, useEffect, useState } from "react";
import {
  Bell,
  GearSix,
  House,
  Plus,
  ShieldCheck,
  Ticket,
} from "@phosphor-icons/react";
import { ApiError } from "./api/client";
import { logout } from "./api/auth";
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
import {
  connectBrowserPush,
  createNotificationChannel,
  disconnectBrowserPush,
  fetchNotificationChannels,
  readBrowserPushState,
  testNotificationChannel,
  updateNotificationChannel,
} from "./api/notifications";
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
import { formatNewWaitDateLabel } from "./features/new-wait/newWaitForm";
import { useAppNotifications } from "./features/app/useAppNotifications";
import { Brand } from "./shared/ui/Brand";
import { hasObservedSeatEvidence } from "./domain/seatEvidence";
import { DEMO_MODE } from "./shared/lib/runtimeConfig";
import { OfficialHandoff } from "./features/official-handoff/OfficialHandoff";
import {
  DEFAULT_SEAT_OBSERVATION_INTERVAL_SECONDS,
  DEFAULT_TIMETABLE_REFRESH_INTERVAL_SECONDS,
  fetchUiPreferences,
  updateUiPreferences,
} from "./api/uiPreferences";
import {
  deleteProviderAccount,
  fetchProviderAccounts,
  saveProviderAccount,
} from "./api/providerAccounts";
import { fetchProviderRuntimeStatuses } from "./api/providerRuntime";
import {
  createDemoWatch,
  demoPaymentWatch,
  demoProviderAccounts,
  demoProviderRuntimeStatuses,
  initialWatches,
} from "./fixtures/demoData";

const navItems = [
  { id: "home", label: "홈", icon: House },
  { id: "new", label: "새 대기", icon: Plus },
  { id: "reservations", label: "내 예약", icon: Ticket },
  { id: "settings", label: "설정", icon: GearSix },
];

const activeWatchStatuses = new Set(["draft", "scheduled", "watching", "official_waitlist", "seat_found", "reserving", "paused", "cooldown", "auth_required"]);
const initialWatchCollection = DEMO_MODE ? initialWatches : [];

function currentRailAccountStatus(provider, accounts, loaded) {
  if (!loaded || !["KORAIL", "SRT"].includes(provider)) return null;
  const account = accounts.find((item) => item.provider === provider);
  return account?.configured && account.enabled ? account.lastAuthStatus : "not_checked";
}

export function isActiveWatch(watch) {
  return activeWatchStatuses.has(watch.status);
}

function Sidebar({ activeView, onNavigate }) {
  return (
    <aside className="sidebar">
      <Brand />
      <nav aria-label="주 메뉴" className="side-nav">
        {navItems.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            type="button"
            className={activeView === id ? "nav-item is-active" : "nav-item"}
            onClick={() => onNavigate(id)}
          >
            <Icon size={24} weight={activeView === id ? "fill" : "regular"} />
            <span>{label}</span>
          </button>
        ))}
      </nav>
      <div className="private-badge"><ShieldCheck size={19} weight="fill" /><span>Tailscale 보호됨</span></div>
    </aside>
  );
}

function BottomNav({ activeView, onNavigate }) {
  return (
    <nav className="bottom-nav" aria-label="모바일 주 메뉴">
      {navItems.map(({ id, label, icon: Icon }) => (
        <button
          key={id}
          type="button"
          className={activeView === id ? "bottom-item is-active" : "bottom-item"}
          onClick={() => onNavigate(id)}
        >
          <Icon size={24} weight={activeView === id ? "fill" : "regular"} />
          <span>{label}</span>
        </button>
      ))}
    </nav>
  );
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
  const [activeView, setActiveView] = useState("home");
  const [settingsInitialSection, setSettingsInitialSection] = useState("notifications");
  const [settingsActiveSection, setSettingsActiveSection] = useState("notifications");
  const { auth, markAuthenticated, markUnauthenticated, retryAuthStatus } = useAuthState();
  const [channels, setChannels] = useState([]);
  const [browserPushState, setBrowserPushState] = useState({
    support: "checking",
    permission: "default",
    subscribed: false,
  });
  const [providerAccounts, setProviderAccounts] = useState(DEMO_MODE ? demoProviderAccounts : []);
  const [providerRuntimeStatuses, setProviderRuntimeStatuses] = useState(
    DEMO_MODE ? demoProviderRuntimeStatuses : [],
  );
  const [providerAccountsLoaded, setProviderAccountsLoaded] = useState(DEMO_MODE);
  const [providerAccountsLoading, setProviderAccountsLoading] = useState(false);
  const [pendingProviderAccount, setPendingProviderAccount] = useState(null);
  const [uiPreferences, setUiPreferences] = useState({
    timetableRefreshIntervalSeconds: DEFAULT_TIMETABLE_REFRESH_INTERVAL_SECONDS,
    seatObservationIntervalSeconds: DEFAULT_SEAT_OBSERVATION_INTERVAL_SECONDS,
    updatedAt: new Date(0).toISOString(),
  });
  const [savingUiPreferences, setSavingUiPreferences] = useState(false);
  const {
    state: notificationState,
    push: setToast,
    pushMany: pushNotifications,
    dismiss: dismissNotification,
    dismissGroup: dismissNotificationGroup,
    dismissTimed: dismissTimedNotifications,
    clear: clearNotifications,
  } = useAppNotifications();
  const refreshProviderRuntimeStatuses = useCallback(async () => {
    if (auth.demo) {
      setProviderRuntimeStatuses(demoProviderRuntimeStatuses);
      return;
    }
    const statuses = await fetchProviderRuntimeStatuses();
    setProviderRuntimeStatuses(statuses);
  }, [auth.demo]);

  const handleProviderAuthenticationTransition = useCallback(() => {
    if (auth.demo) return;
    setProviderAccountsLoaded(false);
    void fetchProviderAccounts().then((items) => {
      setProviderAccounts(items);
      setProviderAccountsLoaded(true);
      return refreshProviderRuntimeStatuses();
    }).catch(() => {
      // Keep the activity row neutral until a no-store account read succeeds.
    });
  }, [auth.demo, refreshProviderRuntimeStatuses]);

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

  useEffect(() => {
    if (!auth.authenticated || auth.demo) return undefined;
    let active = true;
    fetchNotificationChannels().then((channelItems) => {
      if (active) setChannels(channelItems);
    }).catch((error) => {
      if (error instanceof ApiError && error.status === 401) markUnauthenticated();
    });
    return () => { active = false; };
  }, [auth.authenticated, auth.demo]);

  useEffect(() => {
    if (!auth.authenticated || auth.demo) return undefined;
    let active = true;
    const refresh = () => {
      readBrowserPushState().then((state) => {
        if (active) setBrowserPushState(state);
      }).catch(() => {
        if (active) setBrowserPushState({ support: "unsupported", permission: "default", subscribed: false });
      });
    };
    refresh();
    window.addEventListener("focus", refresh);
    return () => {
      active = false;
      window.removeEventListener("focus", refresh);
    };
  }, [auth.authenticated, auth.demo]);

  useEffect(() => {
    if (!auth.authenticated) return undefined;
    if (auth.demo) {
      setProviderAccounts(demoProviderAccounts);
      setProviderRuntimeStatuses(demoProviderRuntimeStatuses);
      setProviderAccountsLoaded(true);
      return undefined;
    }
    let active = true;
    setProviderAccountsLoading(true);
    setProviderAccountsLoaded(false);
    fetchProviderAccounts().then((items) => {
      if (active) {
        setProviderAccounts(items);
        setProviderAccountsLoaded(true);
        void refreshProviderRuntimeStatuses().catch(() => {
          // Preserve the latest known runtime state when this supplemental read fails.
        });
      }
    }).catch((error) => {
      if (active) setToast(error instanceof Error ? error.message : "철도 계정 상태를 불러오지 못했습니다.");
    }).finally(() => {
      if (active) setProviderAccountsLoading(false);
    });
    return () => { active = false; };
  }, [auth.authenticated, auth.demo, refreshProviderRuntimeStatuses]);

  useEffect(() => {
    if (
      !auth.authenticated
      || auth.demo
      || activeView !== "settings"
      || settingsActiveSection !== "rail-accounts"
    ) {
      return undefined;
    }
    void refreshProviderRuntimeStatuses().catch(() => {
      // A polling failure must not erase a previously confirmed status or interrupt settings.
    });
    const timer = window.setInterval(() => {
      void refreshProviderRuntimeStatuses().catch(() => {
        // Keep the last successful status visible until the next successful poll.
      });
    }, 15_000);
    return () => window.clearInterval(timer);
  }, [activeView, auth.authenticated, auth.demo, refreshProviderRuntimeStatuses, settingsActiveSection]);

  useEffect(() => {
    if (!auth.authenticated || auth.demo) return undefined;
    let active = true;
    fetchUiPreferences().then((preferences) => {
      if (active) setUiPreferences(preferences);
    }).catch((error) => {
      if (active) setToast(error instanceof Error ? error.message : "화면 갱신 설정을 불러오지 못했습니다.");
    });
    return () => { active = false; };
  }, [auth.authenticated, auth.demo]);

  const navigate = (view, settingsSection) => {
    setActiveView(view);
    if (view === "settings") {
      const nextSettingsSection = settingsSection ?? "notifications";
      setSettingsInitialSection(nextSettingsSection);
      setSettingsActiveSection(nextSettingsSection);
    }
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const saveUiPreferences = async (input) => {
    setSavingUiPreferences(true);
    try {
      const saved = auth.demo
        ? { ...input, updatedAt: new Date().toISOString() }
        : await updateUiPreferences(input);
      setUiPreferences(saved);
      setToast("화면·좌석 관측 간격을 저장했습니다. 활성 작업의 다음 관측부터 적용됩니다.");
      return saved;
    } catch (error) {
      setToast(error instanceof Error ? error.message : "화면·좌석 관측 간격을 저장하지 못했습니다.");
      throw error;
    } finally {
      setSavingUiPreferences(false);
    }
  };

  const saveRailProviderAccount = async (provider, input) => {
    setPendingProviderAccount(provider);
    try {
      const saved = auth.demo
        ? { ...demoProviderAccounts.find((item) => item.provider === provider), maskedLoginId: `${input.loginId.slice(0, 2)}***`, updatedAt: new Date().toISOString() }
        : await saveProviderAccount(provider, input);
      setProviderAccounts((items) => [saved, ...items.filter((item) => item.provider !== provider)]);
      setProviderAccountsLoaded(true);
      void refreshProviderRuntimeStatuses().catch(() => {
        // The saved account remains valid even if its supplemental runtime read is unavailable.
      });
      setToast(`${provider} 철도 계정을 저장했습니다.`);
    } catch (error) {
      setToast(error instanceof Error ? error.message : "철도 계정을 저장하지 못했습니다.");
      throw error;
    } finally {
      setPendingProviderAccount(null);
    }
  };

  const removeRailProviderAccount = async (provider) => {
    setPendingProviderAccount(provider);
    try {
      if (!auth.demo) await deleteProviderAccount(provider);
      setProviderAccounts((items) => items.map((item) => item.provider === provider ? {
        ...item,
        configured: false,
        enabled: false,
        maskedLoginId: null,
        credentialVersion: 0,
        lastAuthStatus: "not_checked",
        lastAuthenticatedAt: null,
        updatedAt: null,
      } : item));
      setProviderAccountsLoaded(true);
      void refreshProviderRuntimeStatuses().catch(() => {
        // Preserve the prior status instead of replacing it with an unverified value.
      });
      setToast(`${provider} 철도 계정 연결을 해제했습니다.`);
    } catch (error) {
      setToast(error instanceof Error ? error.message : "철도 계정 연결을 해제하지 못했습니다.");
      throw error;
    } finally {
      setPendingProviderAccount(null);
    }
  };

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

  const saveChannel = async ({ kind, name, config }) => {
    try {
      const existing = channels.find((channel) => channel.kind === kind);
      const saved = existing
        ? await updateNotificationChannel(existing.id, { name, config, enabled: true })
        : await createNotificationChannel({ kind, name, config, enabled: true });
      setChannels((items) => [saved, ...items.filter((item) => item.kind !== kind)]);
      setToast("알림 채널을 연결했습니다.");
    } catch (error) {
      setToast(error.message);
      throw error;
    }
  };

  const toggleChannel = async (channel, nextEnabled = !channel?.enabled) => {
    if (!channel) return;
    try {
      if (auth.demo) setChannels((items) => items.map((item) => item.id === channel.id ? { ...item, enabled: nextEnabled } : item));
      else if (channel.kind === "web_push") {
        if (nextEnabled) {
          const updated = await connectBrowserPush(channel.name, channel.id);
          setChannels((items) => items.map((item) => item.id === channel.id ? updated : item));
        } else {
          const updated = await updateNotificationChannel(channel.id, { enabled: false });
          setChannels((items) => items.map((item) => item.id === channel.id ? updated : item));
          await disconnectBrowserPush();
        }
        setBrowserPushState(await readBrowserPushState());
      } else {
        const updated = await updateNotificationChannel(channel.id, { enabled: nextEnabled });
        setChannels((items) => items.map((item) => item.id === channel.id ? updated : item));
      }
    } catch (error) {
      setToast(error.message);
    }
  };

  const testChannel = async (channel) => {
    try {
      if (channel.kind === "web_push") {
        const state = await readBrowserPushState();
        setBrowserPushState(state);
        if (state.permission !== "granted" || !state.subscribed) {
          throw new ApiError("이 기기의 OS 알림 구독을 먼저 켜 주세요.");
        }
      }
      if (!auth.demo) await testNotificationChannel(channel.id);
      setToast(`${channel.name} 시험 알림을 전송 대기열에 넣었습니다.`);
    } catch (error) {
      setToast(error.message);
    }
  };

  const connectWebPushChannel = async () => {
    try {
      if (auth.demo) {
        const now = new Date().toISOString();
        setChannels((items) => [{ id: "demo-web-push", kind: "web_push", name: "이 브라우저", enabled: true, configured: true, createdAt: now, updatedAt: now }, ...items.filter((item) => item.kind !== "web_push")]);
        setBrowserPushState({ support: "supported", permission: "granted", subscribed: true });
      } else {
        const existing = channels.find((channel) => channel.kind === "web_push");
        const channel = await connectBrowserPush(existing?.name ?? "이 브라우저", existing?.id ?? null);
        setChannels((items) => [channel, ...items.filter((item) => item.kind !== "web_push")]);
        setBrowserPushState(await readBrowserPushState());
      }
      setToast("이 기기의 OS 알림을 연결했습니다.");
    } catch (error) {
      setToast(error.message);
    }
  };

  const signOut = async () => {
    try {
      if (!auth.demo) await logout();
    } finally {
      commitWatches([]);
      setChannels([]);
      setProviderAccounts([]);
      clearNotifications();
      setUiPreferences({
        timetableRefreshIntervalSeconds: DEFAULT_TIMETABLE_REFRESH_INTERVAL_SECONDS,
        seatObservationIntervalSeconds: DEFAULT_SEAT_OBSERVATION_INTERVAL_SECONDS,
        updatedAt: new Date(0).toISOString(),
      });
      markUnauthenticated();
    }
  };

  if (auth.loading) return <main className="auth-page"><div className="loading-state"><img src="/icons/app-icon-512.png" alt="" /><span>안전하게 연결하는 중…</span></div></main>;
  if (!auth.authenticated) return <AuthGate status={auth} onAuthenticated={markAuthenticated} onRetryStatus={retryAuthStatus} />;

  const paymentWatches = watches.filter((watch) => watch.status === "payment_required");
  if (auth.demo && paymentWatches.length === 0) paymentWatches.push(demoPaymentWatch);
  const activeWatches = watches.filter(isActiveWatch).map((watch) => {
    const accountAuthStatus = currentRailAccountStatus(
      watch.provider,
      providerAccounts,
      providerAccountsLoaded,
    );
    return { ...watch, accountAuthStatus };
  });
  const reservationWatches = auth.demo && !watches.some((watch) => watch.id === demoPaymentWatch.id)
    ? [demoPaymentWatch, ...watches]
    : watches;
  return (
    <div className="app-shell">
      <Sidebar activeView={activeView} onNavigate={navigate} />
      <main className="main-content">
        <div className="mobile-header"><Brand /><button type="button" className="icon-button" aria-label="알림"><Bell size={23} /></button></div>
        {activeView === "home" && <HomePage watches={activeWatches} paymentWatches={paymentWatches} watchRefreshState={watchRefreshState} onRefreshWatches={requestWatchesRefresh} onCreate={() => navigate("new")} onViewReservations={() => navigate("reservations")} onOpenRailAccounts={() => navigate("settings", "rail-accounts")} onPause={pauseWatch} onResume={resumeWatch} onCancel={cancelWatchItem} onChangeReservationPolicy={changeWatchReservationPolicy} reservationPolicyUpdatingIds={reservationPolicyUpdatingIds} onToast={setToast} renderSeatFoundAction={renderHomeSeatFoundAction} />}
        {activeView === "new" && <NewWaitPage demo={auth.demo} watches={watches} providerAccounts={providerAccounts} refreshIntervalSeconds={uiPreferences.timetableRefreshIntervalSeconds} onComplete={completeWizard} onCancelWatch={cancelWatchItem} onCancel={() => navigate("home")} officialHandoffComponent={OfficialHandoff} />}
        {activeView === "reservations" && <ReservationsPage watches={reservationWatches} onCreate={() => navigate("new")} onDelete={deleteWatchRecord} />}
        {activeView === "settings" && <SettingsPage channels={channels} demo={auth.demo} browserPushState={browserPushState} providerAccounts={providerAccounts} providerRuntimeStatuses={providerRuntimeStatuses} providerAccountsLoading={providerAccountsLoading} pendingProviderAccount={pendingProviderAccount} uiPreferences={uiPreferences} savingUiPreferences={savingUiPreferences} onSaveUiPreferences={saveUiPreferences} onSaveChannel={saveChannel} onToggleChannel={toggleChannel} onTestChannel={testChannel} onConnectWebPush={connectWebPushChannel} onSaveProviderAccount={saveRailProviderAccount} onDeleteProviderAccount={removeRailProviderAccount} onSectionChange={setSettingsActiveSection} onLogout={signOut} initialSection={settingsInitialSection} />}
      </main>
      <BottomNav activeView={activeView} onNavigate={navigate} />
      <AppNotificationCenter
        state={notificationState}
        onDismiss={dismissNotification}
        onDismissGroup={dismissNotificationGroup}
        onDismissTimed={dismissTimedNotifications}
      />
    </div>
  );
}
