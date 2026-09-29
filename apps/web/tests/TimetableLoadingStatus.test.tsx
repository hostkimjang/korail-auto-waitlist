import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { TimetableLoadingStatus } from "../src/features/new-wait/TimetableLoadingStatus";

describe("TimetableLoadingStatus", () => {
  it("shows elapsed time measured by the app when queue detection includes it", () => {
    render(<TimetableLoadingStatus
      loadingProviders={["KORAIL", "SRT"]}
      korailProgress={{
        state: "official_queue",
        queue: { elapsedWaitSeconds: 185 },
      }}
    />);

    const status = screen.getByRole("status");
    expect(status.textContent).toContain("코레일 서비스 접속 대기 중입니다.");
    expect(status.textContent).toContain("앱에서 대기 감지 후3분 5초 경과");
  });

  it("shows the initial zero-second elapsed time", () => {
    render(<TimetableLoadingStatus
      loadingProviders={["KORAIL"]}
      korailProgress={{ state: "official_queue", queue: { elapsedWaitSeconds: 0 } }}
    />);

    expect(screen.getByRole("status").textContent).toContain("앱에서 대기 감지 후0분 0초 경과");
  });

  it("uses the queue message without inventing an elapsed time", () => {
    render(<TimetableLoadingStatus
      loadingProviders={["KORAIL"]}
      korailProgress={{ state: "official_queue" }}
    />);

    expect(screen.getByRole("status").textContent).toContain("공식 대기열에서 연결을 기다리고");
    expect(screen.queryByText("앱에서 대기 감지 후")).toBeNull();
  });

  it("does not show stale KORAIL queue details during another provider lookup", () => {
    render(<TimetableLoadingStatus
      loadingProviders={["SRT"]}
      korailProgress={{ state: "official_queue", queue: { elapsedWaitSeconds: 42 } }}
    />);

    expect(screen.getByRole("status").textContent).toContain("SRT 공식 시간표를 조회하고 있습니다.");
    expect(screen.queryByText("앱에서 대기 감지 후")).toBeNull();
  });
});
