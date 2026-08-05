import type { ReactElement } from "react";

import { HomePage, type HomeCompatibilityProps } from "../features/home/HomePage";
import { PaymentRequiredSection } from "../features/home/PaymentRequiredSection";
import {
  mapLegacyPaymentRequiredWatch,
  type LegacyPaymentRequiredWatch,
} from "../features/home/paymentRequiredViewModel";
import { NewWaitPage, type NewWaitPageProps } from "../features/new-wait/NewWaitPage";
import { OfficialHandoff } from "../features/official-handoff/OfficialHandoff";
import {
  ReservationsPage,
  type ReservationsPageProps,
} from "../features/reservations/ReservationsPage";
import { renderHomeSeatFoundAction } from "./HomeSeatFoundOfficialHandoff";

export interface PaymentHeroProps {
  watch: LegacyPaymentRequiredWatch;
  onOfficialPayment: () => void;
}

export function PaymentHero({
  watch,
  onOfficialPayment,
}: PaymentHeroProps): ReactElement {
  return (
    <PaymentRequiredSection
      watches={[mapLegacyPaymentRequiredWatch(watch)]}
      onOpenPayment={() => onOfficialPayment()}
    />
  );
}

export function Home({
  watches,
  paymentWatch = null,
  paymentWatches = paymentWatch ? [paymentWatch] : [],
  watchRefreshState = { isRefreshing: false, lastRefreshedAt: null },
  onRefreshWatches,
  onNavigate,
  onPause,
  onResume,
  onCancel,
  onChangeReservationPolicy,
  reservationPolicyUpdatingIds = new Set<string>(),
  onToast,
}: HomeCompatibilityProps): ReactElement {
  return (
    <HomePage
      watches={watches}
      paymentWatches={paymentWatches.map(mapLegacyPaymentRequiredWatch)}
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

export type NewWaitCompatibilityProps = Omit<
  NewWaitPageProps,
  "officialHandoffComponent"
>;

export function NewWait(props: NewWaitCompatibilityProps): ReactElement {
  return <NewWaitPage {...props} officialHandoffComponent={OfficialHandoff} />;
}

export interface ReservationsCompatibilityProps {
  watches: ReservationsPageProps["watches"];
  onNavigate: (view: "new") => void;
  onDelete?: NonNullable<ReservationsPageProps["onDelete"]>;
}

const ignoreDelete = (_watchId: string): void => undefined;

export function Reservations({
  watches,
  onNavigate,
  onDelete = ignoreDelete,
}: ReservationsCompatibilityProps): ReactElement {
  return (
    <ReservationsPage
      watches={watches}
      onCreate={() => onNavigate("new")}
      onDelete={onDelete}
    />
  );
}
