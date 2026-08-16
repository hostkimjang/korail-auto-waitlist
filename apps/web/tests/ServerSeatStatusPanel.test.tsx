import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ServerSeatStatusPanel } from "../src/features/new-wait/ServerSeatStatusPanel";
import { summarizeServerSeatStatus } from "../src/features/new-wait/serverSeatStatusSummary";

describe("server seat status summary", () => {
  it("reports a complete automatic server observation", () => {
    const summary = summarizeServerSeatStatus(
      [train("KORAIL", "available"), train("SRT", "sold_out")],
      ["KORAIL", "SRT"],
      {
        KORAIL: { status: "success" },
        SRT: { status: "success" },
      },
      [],
    );

    expect(summary).toEqual({
      state: "complete",
      observedSeatCount: 4,
      unknownSeatCount: 0,
      retryableProviders: [],
      korailImportContext: null,
    });
  });

  it("accepts standing-only as a complete observed server status", () => {
    expect(summarizeServerSeatStatus(
      [train("KORAIL", "standing_only")],
      ["KORAIL"],
      { KORAIL: { status: "success" } },
      [],
    )).toMatchObject({
      state: "complete",
      observedSeatCount: 2,
      unknownSeatCount: 0,
    });
  });

  it("keeps unknown seats fail-closed and identifies only their provider for retry", () => {
    const summary = summarizeServerSeatStatus(
      [train("KORAIL", "unknown", "not_observed"), train("SRT", "sold_out")],
      ["KORAIL", "SRT"],
      {
        KORAIL: { status: "success" },
        SRT: { status: "success" },
      },
      [],
    );

    expect(summary).toMatchObject({
      state: "partial",
      observedSeatCount: 2,
      unknownSeatCount: 2,
      retryableProviders: ["KORAIL"],
    });
  });

  it("marks a provider protection response as restricted without offering a retry", () => {
    const summary = summarizeServerSeatStatus(
      [train(
        "KORAIL",
        "unknown",
        "not_observed",
        {},
        "provider_access_restricted",
      )],
      ["KORAIL"],
      { KORAIL: { status: "success" } },
      [],
    );

    expect(summary).toMatchObject({
      state: "restricted",
      observedSeatCount: 0,
      unknownSeatCount: 2,
      retryableProviders: [],
    });
  });

  it("does not overstate a mixed unknown result as a provider restriction", () => {
    const mixedTrain = train(
      "KORAIL",
      "unknown",
      "not_observed",
      {},
      "provider_access_restricted",
    );
    mixedTrain.seat_classes = mixedTrain.seat_classes.map((seatClass, index) => ({
      ...seatClass,
      provenance: {
        ...seatClass.provenance,
        reason: index === 0 ? "provider_access_restricted" : "source_not_configured",
      },
    }));

    const summary = summarizeServerSeatStatus(
      [mixedTrain],
      ["KORAIL"],
      { KORAIL: { status: "success" } },
      [],
    );

    expect(summary.state).toBe("partial");
  });

  it("reports an elapsed departure window without a retryable provider", () => {
    const summary = summarizeServerSeatStatus(
      [train(
        "KORAIL",
        "unknown",
        "not_observed",
        {},
        "departure_window_elapsed",
      )],
      ["KORAIL"],
      { KORAIL: { status: "success" } },
      [],
    );

    expect(summary).toMatchObject({
      state: "elapsed",
      observedSeatCount: 0,
      unknownSeatCount: 2,
      retryableProviders: [],
    });
  });

  it("retries only providers with genuine unknown results when elapsed results are mixed in", () => {
    const summary = summarizeServerSeatStatus(
      [
        train(
          "KORAIL",
          "unknown",
          "not_observed",
          {},
          "departure_window_elapsed",
        ),
        train("SRT", "unknown", "not_observed", {}, "source_not_configured"),
      ],
      ["KORAIL", "SRT"],
      {
        KORAIL: { status: "success" },
        SRT: { status: "success" },
      },
      [],
    );

    expect(summary).toMatchObject({
      state: "partial",
      observedSeatCount: 0,
      unknownSeatCount: 4,
      retryableProviders: ["SRT"],
    });
  });

  it("keeps an observed future result partial when elapsed trains are mixed in", () => {
    const summary = summarizeServerSeatStatus(
      [
        train("KORAIL", "available"),
        train(
          "KORAIL",
          "unknown",
          "not_observed",
          {},
          "departure_window_elapsed",
        ),
      ],
      ["KORAIL"],
      { KORAIL: { status: "success" } },
      [],
    );

    expect(summary).toMatchObject({
      state: "partial",
      observedSeatCount: 2,
      unknownSeatCount: 2,
      retryableProviders: [],
    });
  });

  it("never retries a restricted provider when it is mixed with elapsed results", () => {
    const summary = summarizeServerSeatStatus(
      [
        train(
          "KORAIL",
          "unknown",
          "not_observed",
          {},
          "departure_window_elapsed",
        ),
        train(
          "SRT",
          "unknown",
          "not_observed",
          {},
          "provider_access_restricted",
        ),
      ],
      ["KORAIL", "SRT"],
      { KORAIL: { status: "success" }, SRT: { status: "success" } },
      [],
    );

    expect(summary).toMatchObject({
      state: "partial",
      retryableProviders: [],
    });
  });

  it("retries an errored provider without retrying a restricted provider", () => {
    const summary = summarizeServerSeatStatus(
      [train(
        "SRT",
        "unknown",
        "not_observed",
        {},
        "provider_access_restricted",
      )],
      ["KORAIL", "SRT"],
      { KORAIL: { status: "error" }, SRT: { status: "success" } },
      [],
    );

    expect(summary).toMatchObject({
      state: "error",
      retryableProviders: ["KORAIL"],
    });
  });

  it("fails closed when external status or provenance strings are invalid", () => {
    const invalidStatus = train("KORAIL", "invented_status");
    const invalidProvenance = train("SRT", "available", "invented_provenance");
    const summary = summarizeServerSeatStatus(
      [invalidStatus, invalidProvenance],
      ["KORAIL", "SRT"],
      { KORAIL: { status: "success" }, SRT: { status: "success" } },
      [],
    );

    expect(summary).toMatchObject({
      state: "partial",
      observedSeatCount: 0,
      unknownSeatCount: 4,
      retryableProviders: ["KORAIL", "SRT"],
    });
  });

  it("derives one exact KORAIL route and date for a user-triggered browser import", () => {
    const summary = summarizeServerSeatStatus(
      [train("KORAIL", "unknown", "not_observed", {
        origin: "서울",
        destination: "부산",
        departure_at: "2030-07-30T12:00:00+09:00",
      })],
      ["KORAIL"],
      { KORAIL: { status: "success" } },
      [],
    );

    expect(summary.korailImportContext).toEqual({
      origin: "서울",
      destination: "부산",
      travelDate: "2030-07-30",
    });
  });
});

describe("ServerSeatStatusPanel", () => {
  it("shows retry only as a fallback for unresolved server results", async () => {
    const user = userEvent.setup();
    const onRetry = vi.fn().mockResolvedValue(undefined);
    render(<ServerSeatStatusPanel
      summary={{
        state: "partial",
        observedSeatCount: 2,
        unknownSeatCount: 2,
        retryableProviders: ["KORAIL"],
        korailImportContext: null,
      }}
      onRetry={onRetry}
    />);

    expect(screen.getByText("일부 좌석 상태를 확인하지 못했습니다")).toBeTruthy();
    const retry = screen.getByRole("button", { name: "서버에서 좌석 상태 다시 조회" });
    await user.click(retry);
    expect(onRetry).toHaveBeenCalledWith(["KORAIL"]);
  });

  it("does not show a manual action after automatic refresh succeeds", () => {
    render(<ServerSeatStatusPanel
      summary={{
        state: "complete",
        observedSeatCount: 4,
        unknownSeatCount: 0,
        retryableProviders: [],
        korailImportContext: null,
      }}
      onRetry={vi.fn()}
    />);

    expect(screen.getByText("좌석 상태 자동 반영 완료")).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("does not expose a retry while provider protection cooldown is active", () => {
    render(<ServerSeatStatusPanel
      summary={{
        state: "restricted",
        observedSeatCount: 0,
        unknownSeatCount: 2,
        retryableProviders: [],
        korailImportContext: null,
      }}
      onRetry={vi.fn()}
    />);

    expect(screen.getByText("공식 좌석 조회가 제한되었습니다")).toBeTruthy();
    expect(screen.getByText(/조회 대기 시간 동안 서버는 운영사에 다시 요청하지 않습니다/))
      .toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("explains an elapsed departure window without offering a retry", () => {
    render(<ServerSeatStatusPanel
      summary={{
        state: "elapsed",
        observedSeatCount: 0,
        unknownSeatCount: 2,
        retryableProviders: [],
        korailImportContext: null,
      }}
      onRetry={vi.fn()}
    />);

    expect(screen.getByText("선택한 출발 시간대가 지났습니다")).toBeTruthy();
    expect(screen.getByText(/이미 운행이 끝난 시간대라 현재 좌석 상태를 다시 조회하지 않습니다/))
      .toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("disables retry while its request is pending and ignores a second click", async () => {
    const user = userEvent.setup();
    let resolveRetry: (() => void) | undefined;
    const onRetry = vi.fn(() => new Promise<void>((resolve) => {
      resolveRetry = resolve;
    }));
    render(<ServerSeatStatusPanel
      summary={{
        state: "partial",
        observedSeatCount: 2,
        unknownSeatCount: 2,
        retryableProviders: ["KORAIL"],
        korailImportContext: null,
      }}
      onRetry={onRetry}
    />);

    const retry = screen.getByRole("button", { name: "서버에서 좌석 상태 다시 조회" });
    await user.dblClick(retry);

    expect(onRetry).toHaveBeenCalledTimes(1);
    const pendingRetry = screen.getByRole<HTMLButtonElement>(
      "button",
      { name: "좌석 상태 다시 조회 중" },
    );
    expect(pendingRetry.getAttribute("aria-busy")).toBe("true");
    expect(pendingRetry.disabled).toBe(true);

    resolveRetry?.();
    const completedRetry = await screen.findByRole<HTMLButtonElement>(
      "button",
      { name: "서버에서 좌석 상태 다시 조회" },
    );
    expect(completedRetry.disabled).toBe(false);
  });

});

function train(
  provider: string,
  status: string,
  provenanceKind = "official_provider",
  identity: Record<string, string> = {},
  unobservedReason?: string,
) {
  return {
    provider,
    ...identity,
    seat_classes: ["standard", "first"].map((seatClass) => ({
      seat_class: seatClass,
      status,
      provenance: { kind: provenanceKind, reason: unobservedReason },
    })),
  };
}
