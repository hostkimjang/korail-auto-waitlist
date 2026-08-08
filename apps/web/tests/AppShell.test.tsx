import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AppShell } from "../src/app/AppShell";
import type { AppView } from "../src/app/useAppNavigation";

const navigationItems: ReadonlyArray<{ label: string; view: AppView }> = [
  { label: "홈", view: "home" },
  { label: "새 대기", view: "new" },
  { label: "내 예약", view: "reservations" },
  { label: "설정", view: "settings" },
];

function requiredElement(element: Element | null, description: string): Element {
  if (element === null) {
    throw new Error(`${description} 요소를 찾지 못했습니다.`);
  }
  return element;
}

describe("AppShell", () => {
  it("keeps the shell, main content, navigation, and overlay in their accessibility order", () => {
    const { container } = render(
      <AppShell activeView="home" onNavigate={vi.fn()} overlay={(
        <section className="shell-overlay" data-testid="shell-overlay">알림 오버레이</section>
      )}>
        <section className="page-content" data-testid="page-content">화면 콘텐츠</section>
      </AppShell>,
    );

    const shell = requiredElement(container.firstElementChild, "앱 셸");
    expect(shell.tagName).toBe("DIV");
    expect(shell.className).toBe("app-shell");
    expect(Array.from(shell.children).map((child) => child.tagName)).toEqual([
      "ASIDE",
      "MAIN",
      "NAV",
      "SECTION",
    ]);
    expect(Array.from(shell.children).map((child) => child.className)).toEqual([
      "sidebar",
      "main-content",
      "bottom-nav",
      "shell-overlay",
    ]);

    const sidebar = requiredElement(shell.children.item(0), "사이드바");
    expect(Array.from(sidebar.children).map((child) => child.className)).toEqual([
      "brand",
      "side-nav",
    ]);
    expect(screen.queryByText("Tailscale 보호됨")).toBeNull();

    const main = requiredElement(shell.children.item(1), "메인 콘텐츠");
    expect(Array.from(main.children).map((child) => child.className)).toEqual([
      "mobile-header",
      "page-content",
    ]);
    expect(main.children.item(1)).toBe(screen.getByTestId("page-content"));
    expect(shell.children.item(3)).toBe(screen.getByTestId("shell-overlay"));
    expect(screen.getAllByLabelText("레일웨잇")).toHaveLength(2);
  });

  it("renders the same four ordered destinations in both named navigation regions", () => {
    render(
      <AppShell activeView="reservations" onNavigate={vi.fn()}>
        <div>화면 콘텐츠</div>
      </AppShell>,
    );

    const desktopNavigation = screen.getByRole("navigation", { name: "주 메뉴" });
    const mobileNavigation = screen.getByRole("navigation", { name: "모바일 주 메뉴" });
    const expectedLabels = navigationItems.map(({ label }) => label);
    const desktopButtons = within(desktopNavigation).getAllByRole("button");
    const mobileButtons = within(mobileNavigation).getAllByRole("button");

    expect(desktopButtons.map((button) => button.textContent)).toEqual(expectedLabels);
    expect(mobileButtons.map((button) => button.textContent)).toEqual(expectedLabels);
    expect(desktopButtons.map((button) => button.className)).toEqual([
      "nav-item",
      "nav-item",
      "nav-item is-active",
      "nav-item",
    ]);
    expect(mobileButtons.map((button) => button.className)).toEqual([
      "bottom-item",
      "bottom-item",
      "bottom-item is-active",
      "bottom-item",
    ]);
    expect(desktopButtons.every((button) => button.getAttribute("type") === "button")).toBe(true);
    expect(mobileButtons.every((button) => button.getAttribute("type") === "button")).toBe(true);
  });

  it("forwards every desktop and mobile destination while the notification button stays inert", () => {
    const onNavigate = vi.fn<(view: AppView) => void>();
    render(
      <AppShell activeView="home" onNavigate={onNavigate}>
        <div data-testid="current-page">현재 화면</div>
      </AppShell>,
    );

    const desktopNavigation = screen.getByRole("navigation", { name: "주 메뉴" });
    const mobileNavigation = screen.getByRole("navigation", { name: "모바일 주 메뉴" });

    for (const { label } of navigationItems) {
      fireEvent.click(within(desktopNavigation).getByRole("button", { name: label }));
    }
    for (const { label } of navigationItems) {
      fireEvent.click(within(mobileNavigation).getByRole("button", { name: label }));
    }

    expect(onNavigate.mock.calls.map(([view]) => view)).toEqual([
      "home",
      "new",
      "reservations",
      "settings",
      "home",
      "new",
      "reservations",
      "settings",
    ]);

    const notificationButton = screen.getByRole("button", { name: "알림" });
    expect(notificationButton.className).toBe("icon-button");
    expect(notificationButton.getAttribute("type")).toBe("button");
    fireEvent.click(notificationButton);

    expect(onNavigate).toHaveBeenCalledTimes(8);
    expect(screen.getByTestId("current-page").textContent).toBe("현재 화면");
  });
});
