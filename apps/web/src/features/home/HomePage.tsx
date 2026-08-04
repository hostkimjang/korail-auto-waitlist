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
  type PaymentRequiredWatch,
} from "./PaymentRequiredSection";

export interface HomeWatchRefreshState {
  isRefreshing: boolean;
  lastRefreshedAt: Date | null;
}

type HomeToast = (message: string) => void;
type OpenOfficialWindow = (
  url: string,
  target: "_blank",
  features: "noopener,noreferrer",
) => WindowProxy | null;

export interface HomePageProps {
  watches: ActiveWatch[];
  paymentWatches?: ReadonlyArray<PaymentRequiredWatch>;
  watchRefreshState?: HomeWatchRefreshState;
  onRefreshWatches?: () => void;
  onCreate: () => void;
  onViewReservations: () => void;
  onOpenRailAccounts: () => void;
  onPause: ActiveWatchListProps["onPause"];
  onResume: ActiveWatchListProps["onResume"];
  onCancel: ActiveWatchListProps["onCancel"];
  onChangeReservationPolicy?: ActiveWatchListProps["onChangeReservationPolicy"];
  reservationPolicyUpdatingIds?: ReadonlySet<string>;
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
  | "onToast"
> {
  paymentWatch?: PaymentRequiredWatch | null;
  paymentWatches?: ReadonlyArray<PaymentRequiredWatch>;
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
  watch: PaymentRequiredWatch,
  onToast: HomeToast,
  openWindow: OpenOfficialWindow = (url, target, features) => (
    window.open(url, target, features)
  ),
): void {
  if (!watch.official_booking_url) {
    onToast("공식 예매 주소를 확인할 수 없습니다.");
    return;
  }
  onToast("공식 결제 화면을 새 창에서 엽니다.");
  openWindow(watch.official_booking_url, "_blank", "noopener,noreferrer");
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
  reservationPolicyUpdatingIds = new Set<string>(),
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
        onOpenRailAccounts={onOpenRailAccounts}
        renderSeatFoundAction={renderSeatFoundAction}
        {...(onRefreshWatches ? { onRefresh: onRefreshWatches } : {})}
        {...(onChangeReservationPolicy ? { onChangeReservationPolicy } : {})}
      />
    </div>
  );
}
