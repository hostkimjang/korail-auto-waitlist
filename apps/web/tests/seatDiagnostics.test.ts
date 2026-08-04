import { describe, expect, it } from "vitest";
import {
  normalizeSeatObservationReason,
  seatObservationReasonMeta,
} from "../src/domain/seatDiagnostics";

describe("seat observation diagnostics", () => {
  it.each([
    ["source_not_configured", "서버 좌석 조회 미설정"],
    ["provider_access_restricted", "조회 제한"],
    ["unsupported_route", "구간 미지원"],
    ["passenger_count_not_supported", "인원 미지원"],
    ["departure_window_elapsed", "출발 시간 경과"],
    ["no_exact_match", "일치 정보 없음"],
    ["source_unavailable", "조회 지연"],
    ["public_api_not_available", "정보 미제공"],
  ])("maps %s to a concise user-facing cause", (reason, label) => {
    expect(seatObservationReasonMeta(reason).label).toBe(label);
  });

  it("fails closed for an unknown backend reason", () => {
    expect(normalizeSeatObservationReason("unexpected_internal_detail"))
      .toBe("public_api_not_available");
  });

  it("directs an unconfigured KORAIL source to the server Chromium adapter", () => {
    const meta = seatObservationReasonMeta("source_not_configured");

    expect(meta.helper).toBe(
      "서버의 KORAIL Chromium adapter 설정을 확인한 뒤 다시 조회해 주세요.",
    );
    expect(meta.helper).not.toContain("확장");
  });
});
