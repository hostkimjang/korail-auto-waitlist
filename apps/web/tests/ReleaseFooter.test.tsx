import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  APP_RELEASE,
  CHANGELOG_URL,
  formatReleaseDate,
} from "../src/shared/lib/appRelease";
import { ReleaseFooter } from "../src/shared/ui/ReleaseFooter";

describe("ReleaseFooter", () => {
  it("현재 릴리스 버전과 업데이트 날짜를 요일과 함께 보여 준다", () => {
    const { container } = render(
      <ReleaseFooter
        release={{ version: "1.2.3", releasedOn: "2026-09-18" }}
        changelogUrl="https://example.test/CHANGELOG.md"
      />,
    );

    const footer = container.querySelector("footer.app-footer");
    expect(footer).not.toBeNull();
    expect(screen.getByText("레일웨잇 v1.2.3")).not.toBeNull();
    expect(screen.getByText("2026-09-18 (금) 업데이트")).not.toBeNull();
  });

  it("업데이트 노트를 새 탭에서 안전하게 연다", () => {
    render(<ReleaseFooter changelogUrl="https://example.test/CHANGELOG.md" />);

    const link = screen.getByRole("link", { name: "업데이트 노트" });
    expect(link.getAttribute("href")).toBe("https://example.test/CHANGELOG.md");
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toBe("noopener noreferrer");
  });

  it("기본값으로 배포된 릴리스와 저장소 업데이트 노트를 사용한다", () => {
    render(<ReleaseFooter />);

    expect(screen.getByText(`레일웨잇 v${APP_RELEASE.version}`)).not.toBeNull();
    expect(
      screen.getByText(`${formatReleaseDate(APP_RELEASE.releasedOn)} 업데이트`),
    ).not.toBeNull();
    expect(screen.getByRole("link", { name: "업데이트 노트" }).getAttribute("href")).toBe(
      CHANGELOG_URL,
    );
  });

  it("날짜 형식이 깨지면 요일을 지어내지 않는다", () => {
    render(<ReleaseFooter release={{ version: "9.9.9", releasedOn: "2026-02-31" }} />);

    expect(screen.getByText("2026-02-31 업데이트")).not.toBeNull();
  });
});
