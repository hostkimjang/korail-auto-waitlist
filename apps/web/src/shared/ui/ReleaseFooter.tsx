import { ArrowSquareOut } from "@phosphor-icons/react";
import type { ReactElement } from "react";

import {
  APP_RELEASE,
  CHANGELOG_URL,
  formatReleaseDate,
  type AppRelease,
} from "../lib/appRelease";

export interface ReleaseFooterProps {
  release?: AppRelease;
  changelogUrl?: string;
}

/**
 * 지금 열려 있는 화면이 어느 배포인지 확인할 수 있도록 릴리스 버전과 업데이트 날짜를 보여 준다.
 * 날짜에는 요일을 함께 표시해 "언제 올라간 버전인지"를 바로 읽을 수 있게 한다.
 */
export function ReleaseFooter({
  release = APP_RELEASE,
  changelogUrl = CHANGELOG_URL,
}: ReleaseFooterProps = {}): ReactElement {
  return (
    <footer className="app-footer">
      <p className="app-footer-release">
        <span className="app-footer-version">레일웨잇 v{release.version}</span>
        <span className="app-footer-updated">{formatReleaseDate(release.releasedOn)} 업데이트</span>
      </p>
      <a
        className="app-footer-link"
        href={changelogUrl}
        target="_blank"
        rel="noopener noreferrer"
      >
        업데이트 노트
        <ArrowSquareOut size={15} aria-hidden="true" />
      </a>
    </footer>
  );
}
