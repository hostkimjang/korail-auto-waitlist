import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import {
  APP_RELEASE,
  CHANGELOG_URL,
  formatReleaseDate,
  formatReleaseLabel,
  koreanWeekdayLabel,
} from "../src/shared/lib/appRelease";

const REPOSITORY_ROOT = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../../..",
);
const CHANGELOG_PATH = path.join(REPOSITORY_ROOT, "CHANGELOG.md");
const RELEASE_HEADING = /^## (\d+\.\d+\.\d+) — (\d{4}-\d{2}-\d{2}) \((일|월|화|수|목|금|토)\)$/;

interface ChangelogEntry {
  version: string;
  releasedOn: string;
  weekday: string;
}

function changelogEntries(): ChangelogEntry[] {
  const headings = readFileSync(CHANGELOG_PATH, "utf8")
    .split(/\r?\n/)
    .filter((line) => line.startsWith("## "));
  return headings.map((heading) => {
    const match = RELEASE_HEADING.exec(heading);
    if (match === null) {
      throw new Error(
        `CHANGELOG.md 항목 제목은 "## 1.0.0 — 2026-09-18 (금)" 형식이어야 합니다: ${heading}`,
      );
    }
    return {
      version: match[1] ?? "",
      releasedOn: match[2] ?? "",
      weekday: match[3] ?? "",
    };
  });
}

describe("릴리스 표기", () => {
  it("한국어 요일을 실제 달력 날짜에서만 계산한다", () => {
    expect(koreanWeekdayLabel("2026-09-18")).toBe("금");
    expect(koreanWeekdayLabel("2026-09-20")).toBe("일");
    expect(koreanWeekdayLabel("2024-02-29")).toBe("목");
    expect(koreanWeekdayLabel("2026-02-31")).toBeNull();
    expect(koreanWeekdayLabel("2026-9-18")).toBeNull();
    expect(koreanWeekdayLabel("2026-09-18T00:00:00Z")).toBeNull();
    expect(koreanWeekdayLabel("")).toBeNull();
  });

  it("요일을 계산할 수 없으면 지어내지 않고 날짜 원문만 보여 준다", () => {
    expect(formatReleaseDate("2026-09-18")).toBe("2026-09-18 (금)");
    expect(formatReleaseDate("2026-02-31")).toBe("2026-02-31");
    expect(formatReleaseDate("날짜 미상")).toBe("날짜 미상");
  });

  it("한 줄 표기에 기본값으로 현재 릴리스를 사용한다", () => {
    expect(formatReleaseLabel()).toBe(
      `v${APP_RELEASE.version} · ${formatReleaseDate(APP_RELEASE.releasedOn)}`,
    );
    expect(formatReleaseLabel({ version: "2.1.0", releasedOn: "2026-01-02" })).toBe(
      "v2.1.0 · 2026-01-02 (금)",
    );
  });

  it("업데이트 노트 링크가 저장소의 CHANGELOG를 가리킨다", () => {
    expect(CHANGELOG_URL).toBe(
      "https://github.com/hostkimjang/korail-auto-waitlist/blob/main/CHANGELOG.md",
    );
  });
});

describe("CHANGELOG와 화면 버전 일치", () => {
  const entries = changelogEntries();

  it("모든 항목이 형식과 실제 요일을 지킨다", () => {
    expect(entries.length).toBeGreaterThan(0);
    for (const entry of entries) {
      expect(entry.weekday, `${entry.version} 항목의 요일 표기`).toBe(
        koreanWeekdayLabel(entry.releasedOn),
      );
    }
  });

  it("최신 항목을 맨 위에 둔다", () => {
    const releaseDates = entries.map((entry) => entry.releasedOn);
    expect(releaseDates).toEqual([...releaseDates].sort().reverse());
  });

  it("푸터에 표시할 릴리스가 CHANGELOG 맨 위 항목과 같다", () => {
    const latest = entries[0];
    expect(
      { version: latest?.version, releasedOn: latest?.releasedOn },
      "버전을 올릴 때는 CHANGELOG.md와 src/shared/lib/appRelease.ts를 함께 갱신하세요.",
    ).toEqual({ version: APP_RELEASE.version, releasedOn: APP_RELEASE.releasedOn });
  });
});
