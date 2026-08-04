import { useCallback, useLayoutEffect, useRef, useState } from "react";

import type { RailProvider } from "../../api/providerAccounts";
import { isExpiredWatchCreateConflict } from "../../domain/apiErrors";
import type { NewWaitForm } from "./newWaitForm";
import { recoverRefreshedRegistrationTrain } from "./registrationEvidenceRecovery";
import {
  seatRegistrationKey,
  useInstantWatchRegistration,
} from "./useInstantWatchRegistration";
import type {
  SeatClass,
  WatchRegistrationResult,
  WatchRegistrationState,
} from "./useInstantWatchRegistration";
import { resolvedSeatRegistration } from "./watchRegistrationHydration";

interface SeatWatchRegistrationSeat extends Record<string, unknown> {
  seat_class: SeatClass;
  actions: unknown[];
}

export interface SeatWatchRegistrationTrain extends Record<string, unknown> {
  id: string;
  provider: RailProvider;
  seat_classes: SeatWatchRegistrationSeat[];
}

export interface SelectedSeatWatchRegistrationTrain extends SeatWatchRegistrationTrain {
  selected_seat_class: SeatClass;
}

export interface SeatWatchRegistrationSubmission {
  form: NewWaitForm;
  train: SelectedSeatWatchRegistrationTrain;
  selectedTrains: [SelectedSeatWatchRegistrationTrain];
}

type RegistrationCompletion = (
  submission: SeatWatchRegistrationSubmission,
) => Promise<WatchRegistrationResult | readonly WatchRegistrationResult[]>
  | WatchRegistrationResult
  | readonly WatchRegistrationResult[];

type WatchCancellation = (watchId: string) => Promise<unknown> | unknown;

interface RegistrationInputs {
  form: NewWaitForm;
  trains: readonly unknown[];
  onComplete: RegistrationCompletion;
  onCancelWatch: WatchCancellation;
  refreshProviderSeatStatus: (
    provider: RailProvider,
    form: NewWaitForm,
  ) => Promise<unknown[]>;
}

interface RegistrationSnapshot extends RegistrationInputs {
  watches: readonly unknown[];
  getRegistrationState: (key: string) => WatchRegistrationState;
  register: ReturnType<typeof useInstantWatchRegistration>["register"];
  cancel: ReturnType<typeof useInstantWatchRegistration>["cancel"];
}

export interface UseSeatWatchRegistrationOptions extends RegistrationInputs {
  watches: readonly unknown[];
}

export interface SeatWatchRegistrationController {
  registrationStateForSeat: (
    train: SeatWatchRegistrationTrain,
    seatClass: SeatClass,
  ) => WatchRegistrationState;
  chooseTrainSeat: (id: string, seatClass: SeatClass) => Promise<void>;
  hasActiveRegistration: boolean;
  submitError: string;
}

const refreshFailureMessage = "좌석 상태를 다시 확인하지 못해 등록하지 않았습니다. 잠시 후 다시 조회해 주세요.";
const unsupportedSeatClassMessage = "선택한 좌석 등급을 확인할 수 없어 등록하지 않았습니다. 최신 시간표에서 다시 선택해 주세요.";

function hasAddToWatchAction(train: SeatWatchRegistrationTrain, seatClass: SeatClass): boolean {
  const seat = train.seat_classes.find((item) => item.seat_class === seatClass);
  return seat?.actions.some((action) => (
    isRecord(action) && action.kind === "add_to_watch"
  )) ?? false;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isSeatClass(value: unknown): value is SeatClass {
  return value === "standard" || value === "first" || value === "any";
}

function isSeatWatchRegistrationTrain(value: unknown): value is SeatWatchRegistrationTrain {
  if (
    !isRecord(value)
    || typeof value.id !== "string"
    || !value.id.trim()
    || (value.provider !== "KORAIL" && value.provider !== "SRT")
    || !Array.isArray(value.seat_classes)
  ) return false;
  return value.seat_classes.every((seat) => (
    isRecord(seat)
    && isSeatClass(seat.seat_class)
    && Array.isArray(seat.actions)
  ));
}

function selectedTrain(
  train: SeatWatchRegistrationTrain,
  seatClass: SeatClass,
): SelectedSeatWatchRegistrationTrain {
  return { ...train, selected_seat_class: seatClass };
}

export function useSeatWatchRegistration({
  form,
  trains,
  watches,
  onComplete,
  onCancelWatch,
  refreshProviderSeatStatus,
}: UseSeatWatchRegistrationOptions): SeatWatchRegistrationController {
  const [submitError, setSubmitError] = useState("");
  const {
    getRegistrationState,
    register,
    cancel,
    successCount,
  } = useInstantWatchRegistration();
  const committedSnapshotRef = useRef<RegistrationSnapshot>({
    form,
    trains,
    watches,
    onComplete,
    onCancelWatch,
    refreshProviderSeatStatus,
    getRegistrationState,
    register,
    cancel,
  });

  useLayoutEffect(() => {
    committedSnapshotRef.current = {
      form,
      trains,
      watches,
      onComplete,
      onCancelWatch,
      refreshProviderSeatStatus,
      getRegistrationState,
      register,
      cancel,
    };
  }, [
    cancel,
    form,
    getRegistrationState,
    onCancelWatch,
    onComplete,
    refreshProviderSeatStatus,
    register,
    trains,
    watches,
  ]);

  const registrationStateForSeat = useCallback((
    train: SeatWatchRegistrationTrain,
    seatClass: SeatClass,
  ): WatchRegistrationState => {
    const local = getRegistrationState(seatRegistrationKey(train.id, seatClass));
    return resolvedSeatRegistration(local, watches, train, seatClass);
  }, [getRegistrationState, watches]);

  const chooseTrainSeat = useCallback(async (id: string, seatClass: SeatClass): Promise<void> => {
    const snapshot = committedSnapshotRef.current;
    const train = snapshot.trains.find((item): item is SeatWatchRegistrationTrain => (
      isSeatWatchRegistrationTrain(item) && item.id === id
    ));
    if (!train || !snapshot.form.providers.includes(train.provider)) return;

    const key = seatRegistrationKey(train.id, seatClass);
    const local = snapshot.getRegistrationState(key);
    const currentRegistration = resolvedSeatRegistration(
      local,
      snapshot.watches,
      train,
      seatClass,
    );
    if (currentRegistration.status === "active") {
      const cancelled = await snapshot.cancel(
        key,
        snapshot.onCancelWatch,
        currentRegistration.watchId,
      );
      if (cancelled) setSubmitError("");
      return;
    }
    if (
      currentRegistration.status === "pending"
      || currentRegistration.status === "cancelling"
    ) return;
    if (!hasAddToWatchAction(train, seatClass)) return;

    const registrationForm = { ...snapshot.form };
    const initiallySelectedTrain = selectedTrain(train, seatClass);
    const registered = await snapshot.register(key, async () => {
      try {
        return await snapshot.onComplete({
          form: registrationForm,
          train: initiallySelectedTrain,
          selectedTrains: [initiallySelectedTrain],
        });
      } catch (error) {
        if (!isExpiredWatchCreateConflict(error)) throw error;
        if (seatClass !== "standard" && seatClass !== "first") {
          setSubmitError(unsupportedSeatClassMessage);
          throw new Error(unsupportedSeatClassMessage);
        }

        let refreshedTrains: unknown[];
        try {
          refreshedTrains = await snapshot.refreshProviderSeatStatus(
            train.provider,
            registrationForm,
          );
        } catch {
          setSubmitError(refreshFailureMessage);
          throw new Error(refreshFailureMessage);
        }
        const recovery = recoverRefreshedRegistrationTrain(
          train,
          refreshedTrains,
          seatClass,
        );
        if (!recovery.ok) {
          setSubmitError(recovery.message);
          throw new Error(recovery.message);
        }
        if (!isSeatWatchRegistrationTrain(recovery.train)) {
          setSubmitError(unsupportedSeatClassMessage);
          throw new Error(unsupportedSeatClassMessage);
        }
        const recoveredTrain = selectedTrain(recovery.train, seatClass);
        return snapshot.onComplete({
          form: registrationForm,
          train: recoveredTrain,
          selectedTrains: [recoveredTrain],
        });
      }
    });
    if (registered) setSubmitError("");
  }, []);

  const validTrains = trains.filter(isSeatWatchRegistrationTrain);
  const hasActiveRegistration = successCount > 0 || validTrains.some((train) => (
    train.seat_classes.some((seat) => (
      registrationStateForSeat(train, seat.seat_class).status === "active"
    ))
  ));

  return {
    registrationStateForSeat,
    chooseTrainSeat,
    hasActiveRegistration,
    submitError,
  };
}
