import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ApiError } from "../src/api/client";
import type { NewWaitForm } from "../src/features/new-wait/newWaitForm";
import {
  useSeatWatchRegistration,
} from "../src/features/new-wait/useSeatWatchRegistration";
import type {
  SeatWatchRegistrationTrain,
  UseSeatWatchRegistrationOptions,
} from "../src/features/new-wait/useSeatWatchRegistration";

function form(date = "2026-08-05"): NewWaitForm {
  return {
    provider: "KORAIL",
    providers: ["KORAIL"],
    origin: "서울",
    origin_node_id: "N-SEOUL",
    destination: "부산",
    destination_node_id: "N-BUSAN",
    date,
    time: "12:00",
    timeEnd: "18:00",
    selectedWeekdays: ["수"],
    passengers: "1",
    seat: "일반실",
    channels: ["web_push"],
    reservationPolicy: "notify_only",
  };
}

function train(evidenceId = "evidence-old"): SeatWatchRegistrationTrain {
  return {
    id: "KORAIL:901:2026-08-05T14:30:00+09:00",
    provider: "KORAIL",
    name: "KTX 901",
    train_number: "KTX 901",
    departure_at: "2026-08-05T14:30:00+09:00",
    arrival_at: "2026-08-05T17:00:00+09:00",
    departure: "14:30",
    arrival: "17:00",
    official_booking_url: "https://www.korail.com/ticket/search/list",
    seat_classes: [{
      seat_class: "standard",
      status: "sold_out",
      provenance: {
        kind: "official_provider",
        source: "authorized-test",
        observed_at: "2026-08-05T12:34:00+09:00",
      },
      registration_evidence_id: evidenceId,
      actions: [{ kind: "add_to_watch" }],
    }, {
      seat_class: "first",
      status: "sold_out",
      provenance: {
        kind: "official_provider",
        source: "authorized-test",
        observed_at: "2026-08-05T12:34:00+09:00",
      },
      registration_evidence_id: `${evidenceId}-first`,
      actions: [{ kind: "add_to_watch" }],
    }],
  };
}

function expiredEvidenceConflict(): ApiError {
  const error = new ApiError(
    "좌석 등록 근거가 만료되었습니다.",
    409,
    {
      detail: {
        code: "registration_evidence_conflict",
        reason: "expired",
        message: "좌석 등록 근거가 만료되었습니다.",
      },
    },
  );
  error.operation = "watch.create";
  return error;
}

function options(
  overrides: Partial<UseSeatWatchRegistrationOptions> = {},
): UseSeatWatchRegistrationOptions {
  return {
    form: form(),
    trains: [train()],
    watches: [],
    onComplete: vi.fn().mockResolvedValue([{ id: "watch-standard" }]),
    onCancelWatch: vi.fn().mockResolvedValue(undefined),
    refreshProviderSeatStatus: vi.fn().mockResolvedValue([train("evidence-new")]),
    ...overrides,
  };
}

describe("useSeatWatchRegistration", () => {
  it("keeps the choose callback stable and uses the latest committed snapshot", async () => {
    const firstComplete = vi.fn().mockResolvedValue([{ id: "watch-old" }]);
    const latestComplete = vi.fn().mockResolvedValue([{ id: "watch-latest" }]);
    const initialOptions = options({ onComplete: firstComplete });
    const { result, rerender } = renderHook(
      ({ value }) => useSeatWatchRegistration(value),
      { initialProps: { value: initialOptions } },
    );
    const chooseTrainSeat = result.current.chooseTrainSeat;
    const latestTrain = train("evidence-latest");
    const latestForm = form("2026-08-12");

    rerender({
      value: options({
        form: latestForm,
        trains: [latestTrain],
        onComplete: latestComplete,
      }),
    });

    expect(result.current.chooseTrainSeat).toBe(chooseTrainSeat);
    await act(() => chooseTrainSeat(latestTrain.id, "standard"));
    expect(firstComplete).not.toHaveBeenCalled();
    expect(latestComplete).toHaveBeenCalledOnce();
    expect(latestComplete.mock.calls[0]?.[0].form).toEqual(latestForm);
    expect(latestComplete.mock.calls[0]?.[0].train.registration_evidence_id).toBeUndefined();
    expect(latestComplete.mock.calls[0]?.[0].train.seat_classes[0].registration_evidence_id)
      .toBe("evidence-latest");
  });

  it("ignores malformed and provider-mismatched trains without isolating valid seats", async () => {
    const onComplete = vi.fn().mockResolvedValue([{ id: "watch-standard" }]);
    const validTrain = train();
    const malformedTrain = { id: "malformed", provider: "KORAIL", seat_classes: null };
    const wrongProviderTrain = { ...train(), id: "wrong-provider", provider: "SRT" };
    const { result } = renderHook(() => useSeatWatchRegistration(options({
      trains: [malformedTrain, wrongProviderTrain, validTrain],
      onComplete,
    })));

    await act(() => result.current.chooseTrainSeat("malformed", "standard"));
    await act(() => result.current.chooseTrainSeat("wrong-provider", "standard"));
    await act(() => result.current.chooseTrainSeat(validTrain.id, "standard"));

    expect(onComplete).toHaveBeenCalledOnce();
    expect(result.current.registrationStateForSeat(validTrain, "standard").status).toBe("active");
    expect(result.current.hasActiveRegistration).toBe(true);
  });

  it("fails closed when canonical watch-creation train fields are missing or malformed", async () => {
    const onComplete = vi.fn().mockResolvedValue([{ id: "watch-standard" }]);
    const canonicalTrain = train();
    const { name: removedName, ...withoutName } = canonicalTrain;
    const missingNameTrain = { ...withoutName, id: "missing-name" };
    const malformedOfficialUrlTrain = {
      ...canonicalTrain,
      id: "malformed-official-url",
      official_booking_url: 42,
    };
    const { result } = renderHook(() => useSeatWatchRegistration(options({
      trains: [missingNameTrain, malformedOfficialUrlTrain, canonicalTrain],
      onComplete,
    })));

    expect(removedName).toBe("KTX 901");
    await act(() => result.current.chooseTrainSeat(missingNameTrain.id, "standard"));
    await act(() => result.current.chooseTrainSeat(malformedOfficialUrlTrain.id, "standard"));
    expect(onComplete).not.toHaveBeenCalled();

    await act(() => result.current.chooseTrainSeat(canonicalTrain.id, "standard"));
    expect(onComplete).toHaveBeenCalledOnce();
  });

  it("cancels the exact hydrated watch id and keeps local cancelling state ahead of hydration", async () => {
    let finishCancellation: (() => void) | undefined;
    const cancellation = new Promise<void>((resolve) => { finishCancellation = resolve; });
    const onCancelWatch = vi.fn().mockReturnValue(cancellation);
    const persistedTrain = train();
    const watches = [{
      id: "persisted-watch-standard",
      provider: "KORAIL",
      status: "watching",
      reservationPolicy: "notify_only",
      candidates: [{
        train_number: "KTX 901",
        departure_at: "2026-08-05T05:30:00Z",
        seat_class: "standard",
      }],
    }];
    const { result } = renderHook(() => useSeatWatchRegistration(options({
      trains: [persistedTrain],
      watches,
      onCancelWatch,
    })));

    expect(result.current.registrationStateForSeat(persistedTrain, "standard").status).toBe("active");
    let cancellationTask: Promise<void> | undefined;
    act(() => {
      cancellationTask = result.current.chooseTrainSeat(persistedTrain.id, "standard");
    });
    expect(result.current.registrationStateForSeat(persistedTrain, "standard").status)
      .toBe("cancelling");
    expect(onCancelWatch).toHaveBeenCalledWith("persisted-watch-standard");
    await act(async () => {
      finishCancellation?.();
      await cancellationTask;
    });
    expect(result.current.registrationStateForSeat(persistedTrain, "standard").status).toBe("active");
  });

  it("refreshes expired evidence once and retries once with the exact refreshed seat", async () => {
    const refreshedTrain = train("evidence-new");
    const onComplete = vi.fn()
      .mockRejectedValueOnce(expiredEvidenceConflict())
      .mockResolvedValueOnce([{ id: "watch-recovered" }]);
    const refreshProviderSeatStatus = vi.fn().mockResolvedValue([refreshedTrain]);
    const originalTrain = train("evidence-old");
    const { result } = renderHook(() => useSeatWatchRegistration(options({
      trains: [originalTrain],
      onComplete,
      refreshProviderSeatStatus,
    })));

    await act(() => result.current.chooseTrainSeat(originalTrain.id, "standard"));

    expect(refreshProviderSeatStatus).toHaveBeenCalledOnce();
    expect(onComplete).toHaveBeenCalledTimes(2);
    expect(onComplete.mock.calls[1]?.[0].train.seat_classes[0].registration_evidence_id)
      .toBe("evidence-new");
    expect(result.current.registrationStateForSeat(originalTrain, "standard").status).toBe("active");
    expect(result.current.submitError).toBe("");
  });

  it("fails closed after malformed recovery and never attempts a second retry", async () => {
    const onComplete = vi.fn().mockRejectedValue(expiredEvidenceConflict());
    const refreshProviderSeatStatus = vi.fn().mockResolvedValue([{ provider: "KORAIL" }]);
    const originalTrain = train();
    const { result } = renderHook(() => useSeatWatchRegistration(options({
      trains: [originalTrain],
      onComplete,
      refreshProviderSeatStatus,
    })));

    await act(() => result.current.chooseTrainSeat(originalTrain.id, "standard"));

    expect(refreshProviderSeatStatus).toHaveBeenCalledOnce();
    expect(onComplete).toHaveBeenCalledOnce();
    expect(result.current.registrationStateForSeat(originalTrain, "standard").status).toBe("error");
    expect(result.current.submitError).toContain("재조회한 열차가 기존 선택과 달라");
  });
});
