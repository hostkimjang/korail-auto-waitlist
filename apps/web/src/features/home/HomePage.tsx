import { Plus } from "@phosphor-icons/react";
import type { ReactElement } from "react";

import { PageHeader } from "../../shared/ui/PageHeader";
import { StatusPill } from "../../shared/ui/StatusPill";
import {
  ActiveWatchList,
  type ActiveWatch,
  type ActiveWatchListProps,
} from "./ActiveWatchList";
import {
  PaymentRequiredSection,
  type PaymentRequiredViewModel,
} from "./PaymentRequiredSection";
import type { LegacyPaymentRequiredWatch } from "./paymentRequiredViewModel";
import {
  launchOfficialOpenTarget,
  resolveOfficialOpenTarget,
  type OfficialWindowLike,
  type RailDeepLinkConfig,
} from "../../shared/lib/officialAppIntentUrl";

export interface HomeWatchRefreshState {
  isRefreshing: boolean;
  lastRefreshedAt: Date | null;
}

type HomeToast = (message: string) => void;

export interface HomePageProps {
  watches: ActiveWatch[];
  paymentWatches?: ReadonlyArray<PaymentRequiredViewModel>;
  watchRefreshState?: HomeWatchRefreshState;
  onRefreshWatches?: () => void;
  onCreate: () => void;
  onViewReservations: () => void;
  onOpenRailAccounts: () => void;
  onPause: ActiveWatchListProps["onPause"];
  onResume: ActiveWatchListProps["onResume"];
  onCancel: ActiveWatchListProps["onCancel"];
  onChangeReservationPolicy?: ActiveWatchListProps["onChangeReservationPolicy"];
  onManualReservationRearm?: ActiveWatchListProps["onManualReservationRearm"];
  reservationPolicyUpdatingIds?: ReadonlySet<string>;
  watchMutationPendingIds?: ReadonlySet<string>;
  onToast: HomeToast;
  renderSeatFoundAction: NonNullable<ActiveWatchListProps["renderSeatFoundAction"]>;
}

export interface HomeCompatibilityProps extends Pick<
  HomePageProps,
  | "watches"
  | "watchRefreshState"
  | "onRefreshWatches"
  | "onPause"
  | "onResume"
  | "onCancel"
  | "onChangeReservationPolicy"
  | "reservationPolicyUpdatingIds"
  | "watchMutationPendingIds"
  | "onToast"
> {
  paymentWatch?: LegacyPaymentRequiredWatch | null;
  paymentWatches?: ReadonlyArray<LegacyPaymentRequiredWatch>;
  onNavigate: (
    page: "new" | "reservations" | "settings",
    section?: "rail-accounts",
  ) => void;
}

const defaultWatchRefreshState: HomeWatchRefreshState = {
  isRefreshing: false,
  lastRefreshedAt: null,
};

function WatchManagementHero({ onCreate }: { onCreate: () => void }): ReactElement {
  return (
    <section className="watch-management-hero">
      <div>
        <StatusPill status="watching">관심 열차 관리</StatusPill>
        <h2>공식 예약대기와 예매를<br />한곳에서 관리하세요.</h2>
        <p>선택한 열차와 좌석 등급을 한곳에서 확인하세요.</p>
      </div>
      <button className="button button-primary" type="button" onClick={onCreate}>
        <Plus size={21} />새 대기 만들기
      </button>
    </section>
  );
}

export function openHomeOfficialPayment(
  watch: PaymentRequiredViewModel,
  onToast: HomeToast,
  officialWindow: OfficialWindowLike | undefined = typeof window === "undefined"
    ? undefined
    : window,
  userAgent: unknown = typeof navigator === "undefined" ? "" : navigator.userAgent,
  deepLinkConfig?: RailDeepLinkConfig,
): void {
  const target = resolveOfficialOpenTarget(
    watch.provider,
    watch.officialBookingUrl,
    userAgent,
    "ticket",
    deepLinkConfig,
  );
  if (!target || !officialWindow) {
    onToast("공식 예매 주소를 확인할 수 없습니다.");
    return;
  }
  onToast(target.usesAndroidApp
    ? "공식 앱 열기를 시도합니다. 연결되지 않으면 외부 브라우저에서 공식 홈페이지를 엽니다."
    : "공식 결제 화면을 새 창에서 엽니다.");
  launchOfficialOpenTarget(target, officialWindow);
}

export function HomePage({
  watches,
  paymentWatches = [],
  watchRefreshState = defaultWatchRefreshState,
  onRefreshWatches,
  onCreate,
  onViewReservations,
  onOpenRailAccounts,
  onPause,
  onResume,
  onCancel,
  onChangeReservationPolicy,
  onManualReservationRearm,
  reservationPolicyUpdatingIds = new Set<string>(),
  watchMutationPendingIds = new Set<string>(),
  onToast,
  renderSeatFoundAction,
}: HomePageProps): ReactElement {
  const today = new Intl.DateTimeFormat("ko-KR", {
    year: "numeric",
    month: "long",
    day: "numeric",
    weekday: "short",
  }).format(new Date());

  return (
    <div className="page home-page">
      <PageHeader title="지금 할 일" helper={today} />
      <PaymentRequiredSection
        watches={paymentWatches}
        onOpenPayment={(watch) => openHomeOfficialPayment(watch, onToast)}
        emptyState={<WatchManagementHero onCreate={onCreate} />}
      />
      <ActiveWatchList
        watches={watches}
        isRefreshing={watchRefreshState.isRefreshing}
        lastRefreshedAt={watchRefreshState.lastRefreshedAt}
        onCreate={onCreate}
        onViewAll={onViewReservations}
        onPause={onPause}
        onResume={onResume}
        onCancel={onCancel}
        reservationPolicyUpdatingIds={reservationPolicyUpdatingIds}
        watchMutationPendingIds={watchMutationPendingIds}
        onOpenRailAccounts={onOpenRailAccounts}
        renderSeatFoundAction={renderSeatFoundAction}
        {...(onRefreshWatches ? { onRefresh: onRefreshWatches } : {})}
        {...(onChangeReservationPolicy ? { onChangeReservationPolicy } : {})}
        {...(onManualReservationRearm ? { onManualReservationRearm } : {})}
      />
    </div>
  );
}
