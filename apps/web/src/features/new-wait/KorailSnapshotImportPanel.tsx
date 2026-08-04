import { ArrowSquareOut, ArrowsClockwise, CheckCircle, WarningCircle } from "@phosphor-icons/react";
import { useState } from "react";

import {
  requestKorailCompanionImport,
  type KorailCompanionFailureCode,
} from "./korailCompanionBridge";

const KORAIL_SEARCH_URL = "https://www.korail.com/ticket/search/general";

interface KorailSnapshotImportPanelProps {
  enabled: boolean;
  origin: string;
  destination: string;
  travelDate: string;
  busy: boolean;
  onImported: () => Promise<void>;
}

type PanelState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "success"; message: string }
  | { kind: "refresh_error"; message: string }
  | { kind: "error"; message: string; showOfficialLink: boolean };

export function KorailSnapshotImportPanel({
  enabled,
  origin,
  destination,
  travelDate,
  busy,
  onImported,
}: KorailSnapshotImportPanelProps) {
  const [state, setState] = useState<PanelState>({ kind: "idle" });

  if (!enabled) {
    return null;
  }

  async function importSnapshot(): Promise<void> {
    setState({ kind: "loading" });
    const result = await requestKorailCompanionImport();
    if (!result.ok) {
      setState(failureState(result.code));
      return;
    }
    if (
      normalizeStation(result.origin) !== normalizeStation(origin) ||
      normalizeStation(result.destination) !== normalizeStation(destination) ||
      result.travel_date !== travelDate
    ) {
      setState({
        kind: "error",
        message: `열린 공식 결과는 ${result.origin} → ${result.destination}, ${result.travel_date}입니다. 현재 여정 ${origin} → ${destination}, ${travelDate}와 같은 결과를 열어 주세요.`,
        showOfficialLink: true,
      });
      return;
    }
    try {
      await onImported();
      setState({
        kind: "success",
        message: `${result.train_count}개 열차의 일반실·특실 상태를 현재 목록에 반영했습니다.`,
      });
    } catch {
      setState({
        kind: "refresh_error",
        message: "공식 좌석 결과는 전송했지만 현재 목록을 다시 불러오지 못했습니다.",
      });
    }
  }

  async function retryRefresh(): Promise<void> {
    setState({ kind: "loading" });
    try {
      await onImported();
      setState({ kind: "success", message: "저장된 공식 좌석 상태를 현재 목록에 반영했습니다." });
    } catch {
      setState({
        kind: "refresh_error",
        message: "현재 목록을 다시 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.",
      });
    }
  }

  const isLoading = state.kind === "loading";
  return (
    <section className="korail-import-panel" aria-labelledby="korail-import-title">
      <div className="korail-import-copy">
        <strong id="korail-import-title">KORAIL 좌석 상태 가져오기</strong>
        <span>공식 결과 탭에 보이는 예매 가능·매진·예약대기 상태만 한 번 읽어 현재 열차 카드에 반영합니다.</span>
      </div>
      <div className="korail-import-actions">
        <button
          type="button"
          className="button button-primary compact"
          disabled={busy || isLoading}
          aria-busy={isLoading}
          onClick={() => void (state.kind === "refresh_error" ? retryRefresh() : importSnapshot())}
        >
          <ArrowsClockwise aria-hidden="true" />
          {isLoading
            ? "좌석 상태 가져오는 중…"
            : state.kind === "refresh_error"
              ? "현재 목록 다시 조회"
              : "공식 좌석 상태 가져오기"}
        </button>
        <button
          type="button"
          className="button button-outline compact"
          onClick={() => window.open(KORAIL_SEARCH_URL, "_blank", "noopener,noreferrer")}
        >
          공식 조회 열기<ArrowSquareOut aria-hidden="true" />
        </button>
      </div>
      {state.kind === "success" && (
        <p className="korail-import-feedback is-success" role="status">
          <CheckCircle weight="fill" aria-hidden="true" />{state.message}
        </p>
      )}
      {state.kind === "error" && (
        <p className="korail-import-feedback is-error" role="alert">
          <WarningCircle weight="fill" aria-hidden="true" />{state.message}
          {state.showOfficialLink ? " 공식 조회 탭을 맞춘 뒤 다시 가져오세요." : ""}
        </p>
      )}
      {state.kind === "refresh_error" && (
        <p className="korail-import-feedback is-error" role="alert">
          <WarningCircle weight="fill" aria-hidden="true" />{state.message}
        </p>
      )}
    </section>
  );
}

function normalizeStation(value: string): string {
  return value.replace(/\s+/g, "").trim();
}

function failureState(code: KorailCompanionFailureCode): PanelState {
  switch (code) {
    case "bridge_not_paired":
    case "bridge_reconnect_required":
      return { kind: "error", message: "설정의 KORAIL 연결에서 이 브라우저를 먼저 연결해 주세요.", showOfficialLink: false };
    case "official_tab_missing":
      return { kind: "error", message: "열려 있는 KORAIL 승차권 검색 결과 탭을 찾지 못했습니다.", showOfficialLink: true };
    case "multiple_official_tabs":
      return { kind: "error", message: "KORAIL 검색 결과 탭이 여러 개입니다. 현재 여정 탭 하나만 남겨 주세요.", showOfficialLink: false };
    case "blocked":
      return { kind: "error", message: "공식 화면에 접근 제한 안내가 있어 결과를 가져오지 않았습니다.", showOfficialLink: false };
    case "passenger_unverified":
      return { kind: "error", message: "공식 화면에서 성인 1명 조회 결과인지 확인할 수 없습니다.", showOfficialLink: true };
    case "parse_failed":
      return { kind: "error", message: "공식 결과의 경로·날짜·좌석 상태를 모두 읽지 못했습니다.", showOfficialLink: true };
    case "unsupported_page":
      return { kind: "error", message: "KORAIL 승차권 검색 결과 목록을 연 뒤 다시 시도해 주세요.", showOfficialLink: true };
    case "snapshot_rejected":
      return { kind: "error", message: "서비스가 좌석 결과를 받지 않았습니다. 잠시 후 한 번만 다시 시도해 주세요.", showOfficialLink: false };
    case "request_origin_mismatch":
      return { kind: "error", message: "연결된 서비스 주소와 현재 페이지 주소가 다릅니다. 설정에서 다시 연결해 주세요.", showOfficialLink: false };
    case "extension_unavailable":
      return { kind: "error", message: "KORAIL 결과 가져오기 확장을 찾지 못했습니다. 확장을 설치하거나 새로고침해 주세요.", showOfficialLink: false };
  }
}
