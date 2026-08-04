import { useRef, useState } from "react";

import type { ReservationPolicy } from "../../domain/reservationPolicy";

export type SeatClass = "standard" | "first" | "any";

export type WatchRegistrationState =
  | { status: "idle" }
  | { status: "pending" }
  | { status: "active"; watchId: string; reservationPolicy: ReservationPolicy; message?: string }
  | { status: "cancelling"; watchId: string; reservationPolicy?: ReservationPolicy }
  | { status: "error"; message: string };

export type WatchRegistrationResult = {
  id: string;
  reservationPolicy?: ReservationPolicy;
  reservation_policy?: ReservationPolicy;
};

type RegistrationTask = () => Promise<WatchRegistrationResult | readonly WatchRegistrationResult[]> | WatchRegistrationResult | readonly WatchRegistrationResult[];
type CancellationTask = (watchId: string) => Promise<unknown> | unknown;

const idleRegistration: WatchRegistrationState = { status: "idle" };

export function seatRegistrationKey(trainId: string, seatClass: SeatClass): string {
  return `${trainId}::${seatClass}`;
}

export interface InstantWatchRegistration {
  getRegistrationState: (key: string) => WatchRegistrationState;
  register: (key: string, task: RegistrationTask) => Promise<boolean>;
  cancel: (key: string, task: CancellationTask, persistedWatchId?: string) => Promise<boolean>;
  successCount: number;
}

export function useInstantWatchRegistration(): InstantWatchRegistration {
  const [states, setStates] = useState<Record<string, WatchRegistrationState>>({});
  const statesRef = useRef<Record<string, WatchRegistrationState>>({});

  const updateState = (key: string, state: WatchRegistrationState): void => {
    statesRef.current = { ...statesRef.current, [key]: state };
    setStates(statesRef.current);
  };

  const register = async (key: string, task: RegistrationTask): Promise<boolean> => {
    const current = statesRef.current[key];
    if (
      current?.status === "pending"
      || current?.status === "active"
      || current?.status === "cancelling"
    ) return false;

    updateState(key, { status: "pending" });
    try {
      const result = await task();
      updateState(key, activeRegistrationFrom(result));
      return true;
    } catch (error) {
      updateState(key, {
        status: "error",
        message: error instanceof Error && error.message
          ? error.message
          : "대기를 등록하지 못했습니다. 다시 시도해 주세요.",
      });
      return false;
    }
  };

  const cancel = async (
    key: string,
    task: CancellationTask,
    persistedWatchId?: string,
  ): Promise<boolean> => {
    const current = statesRef.current[key];
    if (current && current.status !== "idle" && current.status !== "active") return false;
    const watchId = current?.status === "active" ? current.watchId : persistedWatchId;
    if (!watchId) return false;

    updateState(key, {
      status: "cancelling",
      watchId,
      ...(current?.status === "active"
        ? { reservationPolicy: current.reservationPolicy }
        : {}),
    });
    try {
      await task(watchId);
      updateState(key, idleRegistration);
      return true;
    } catch (error) {
      updateState(key, {
        status: "active",
        watchId,
        reservationPolicy: current?.status === "active"
          ? current.reservationPolicy
          : "notify_only",
        message: error instanceof Error && error.message
          ? error.message
          : "대기를 취소하지 못했습니다. 다시 시도해 주세요.",
      });
      return false;
    }
  };

  return {
    getRegistrationState: (key) => states[key] ?? idleRegistration,
    register,
    cancel,
    successCount: Object.values(states).filter((state) => state.status === "active").length,
  };
}

function activeRegistrationFrom(
  result: WatchRegistrationResult | readonly WatchRegistrationResult[],
): WatchRegistrationState {
  const watches = Array.isArray(result) ? result : [result];
  if (watches.length !== 1) {
    throw new Error("대기 등록 결과가 정확히 하나여야 합니다.");
  }
  const watch = watches[0];
  if (!watch || typeof watch.id !== "string" || !watch.id.trim()) {
    throw new Error("등록한 대기의 식별자를 확인하지 못했습니다.");
  }
  const reservationPolicy = watch.reservationPolicy === "reserve_once_before_payment"
    || watch.reservation_policy === "reserve_once_before_payment"
    ? "reserve_once_before_payment"
    : "notify_only";
  return { status: "active", watchId: watch.id, reservationPolicy };
}
