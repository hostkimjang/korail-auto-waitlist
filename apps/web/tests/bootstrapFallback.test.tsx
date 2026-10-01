import { readFileSync } from "node:fs";

import { render, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

const indexHtml = readFileSync("index.html", "utf8");

function bootstrapDocument(): Document {
  return new DOMParser().parseFromString(indexHtml, "text/html");
}

describe("앱 시작 전 연결 안내", () => {
  it("외부 JS와 CSS가 없어도 읽을 수 있는 안내와 같은 주소의 재접속 링크를 남긴다", () => {
    const page = bootstrapDocument();
    page.querySelectorAll('script, link[rel="stylesheet"]').forEach((asset) => asset.remove());
    const container = document.createElement("div");
    container.innerHTML = page.body.innerHTML;
    document.body.appendChild(container);
    try {
      const content = within(container);
      expect(content.getByRole("main", { name: "레일웨잇 화면을 불러오는 중입니다" })).toBeDefined();
      expect(content.getByText(/서버가 중지됐거나 연결이 끊겼을 수 있습니다/)).toBeDefined();
      const retry = content.getByText("다시 불러오기");
      expect(retry.tagName).toBe("A");
      expect(retry.getAttribute("href")).toBe("");
      expect(retry.getAttribute("onclick")).toBeNull();
      expect(retry.getAttribute("target")).toBeNull();
    } finally {
      container.remove();
    }
  });

  it("외부 스타일에 의존하지 않고 키보드 초점과 44px 행동 영역을 제공한다", () => {
    const page = bootstrapDocument();
    const styles = Array.from(page.querySelectorAll("style"), (style) => style.textContent).join("\n");

    expect(styles).toContain("#app-bootstrap");
    expect(styles).toContain("min-height: 44px");
    expect(styles).toContain("#app-bootstrap a:focus-visible");
    expect(styles).not.toMatch(/@import|url\(/);
  });

  it("정상 React 렌더가 같은 root의 기본 안내를 교체한다", () => {
    const page = bootstrapDocument();
    const initialRoot = page.getElementById("root");
    if (initialRoot === null) throw new Error("앱 시작 안내를 담은 root가 없습니다.");
    const root = document.importNode(initialRoot, true);

    render(<main aria-label="레일웨잇">정상 화면</main>, { container: root });

    expect(within(root).getByRole("main", { name: "레일웨잇" })).toBeDefined();
    expect(root.querySelector("#app-bootstrap")).toBeNull();
    expect(within(root).queryByText("다시 불러오기")).toBeNull();
  });
});
