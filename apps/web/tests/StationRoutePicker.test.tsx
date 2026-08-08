import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useState } from "react";

import { StationRoutePicker } from "../src/features/new-wait/StationRoutePicker";

const stations = [
  { name: "서울", nodeId: "N-SEOUL", cityName: "서울" },
  { name: "수서", nodeId: "N-SUSEO", cityName: "서울" },
  { name: "대전", nodeId: "N-DAEJEON", cityName: "대전" },
  { name: "부산", nodeId: "N-BUSAN", cityName: "부산" },
] as const;

class TestVisualViewport extends EventTarget {
  height = 432;
  width = 768;
  offsetTop = 18;
  offsetLeft = 7;
}

function useDialogLayout(matches: boolean): void {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches,
    media: query,
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(() => true),
  }));
}

function RouteHarness() {
  const [origin, setOrigin] = useState({ name: "", nodeId: null as string | null });
  const [destination, setDestination] = useState({ name: "", nodeId: null as string | null });

  return (
    <div className="app-shell" data-testid="app-shell">
      <StationRoutePicker
        origin={origin}
        destination={destination}
        originError={origin.nodeId ? "" : "출발역을 제공된 역 목록에서 선택해 주세요."}
        destinationError={destination.nodeId ? "" : "도착역을 제공된 역 목록에서 선택해 주세요."}
        stations={stations}
        onOriginChange={setOrigin}
        onDestinationChange={setDestination}
        onSwap={() => {
          setOrigin(destination);
          setDestination(origin);
        }}
      />
    </div>
  );
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  document.documentElement.style.overflow = "";
  document.documentElement.style.overscrollBehavior = "";
  document.body.style.overflow = "";
  document.body.style.overscrollBehavior = "";
  document.body.style.position = "";
  document.body.style.top = "";
  document.body.style.right = "";
  document.body.style.left = "";
  document.body.style.width = "";
});

describe("StationRoutePicker", () => {
  it("keeps the desktop anchored comboboxes when the touch dialog layout is not active", () => {
    useDialogLayout(false);
    render(<RouteHarness />);

    expect(screen.getByRole("combobox", { name: "출발역" })).toBeTruthy();
    expect(screen.getByRole("combobox", { name: "도착역" })).toBeTruthy();
    expect(screen.queryByRole("dialog", { name: "여정 역 선택" })).toBeNull();
  });

  it("uses the visual viewport and completes origin then destination in one modal flow", async () => {
    useDialogLayout(true);
    const viewport = new TestVisualViewport();
    vi.stubGlobal("visualViewport", viewport);
    const user = userEvent.setup();
    render(<RouteHarness />);

    const originTrigger = screen.getByRole("button", { name: "출발역 역 이름 또는 지역 검색" });
    await user.click(originTrigger);

    const dialog = screen.getByRole("dialog", { name: "여정 역 선택" });
    const layer = dialog.closest(".station-route-dialog-layer");
    const shell = screen.getByTestId("app-shell");
    expect(layer).toBeInstanceOf(HTMLDivElement);
    expect(layer?.getAttribute("style")).toContain("--station-dialog-height: 432px");
    expect(layer?.getAttribute("style")).toContain("--station-dialog-top: 18px");
    expect(layer?.getAttribute("style")).toContain("--station-dialog-width: 768px");
    expect(layer?.getAttribute("style")).toContain("--station-dialog-left: 7px");
    expect(shell.inert).toBe(true);
    expect(shell.getAttribute("aria-hidden")).toBe("true");
    expect(document.documentElement.style.overflow).toBe("hidden");
    expect(document.body.style.overflow).toBe("hidden");
    expect(document.body.style.position).toBe("fixed");

    const originSearch = within(dialog).getByRole("combobox", { name: "출발역 검색" });
    await waitFor(() => expect(document.activeElement).toBe(
      within(dialog).getByRole("button", { name: "역 선택 닫기" }),
    ));
    await user.click(within(dialog).getByRole("button", { name: "서울" }));
    const originList = within(dialog).getByRole("listbox", { name: "출발역 검색 가능한 역" });
    expect(within(originList).queryByRole("option", { name: /^부산/ })).toBeNull();

    await user.type(originSearch, "수서");
    await user.click(within(originList).getByRole("option", { name: /^수서/ }));

    expect(screen.getByRole("dialog", { name: "여정 역 선택" })).toBe(dialog);
    const destinationSearch = within(dialog).getByRole("combobox", { name: "도착역 검색" });
    expect(document.activeElement).toBe(destinationSearch);
    expect(within(dialog).getByRole("button", { name: /출발역 수서/ })).toBeTruthy();

    await user.type(destinationSearch, "부산");
    const destinationList = within(dialog).getByRole("listbox", { name: "도착역 검색 가능한 역" });
    await user.click(within(destinationList).getByRole("option", { name: /^부산/ }));

    await waitFor(() => expect(screen.queryByRole("dialog", { name: "여정 역 선택" })).toBeNull());
    const destinationTrigger = screen.getByRole("button", { name: "도착역 부산" });
    expect(screen.getByRole("button", { name: "출발역 수서" })).toBeTruthy();
    expect(shell.inert).toBe(false);
    expect(shell.hasAttribute("aria-hidden")).toBe(false);
    expect(document.documentElement.style.overflow).toBe("");
    expect(document.body.style.overflow).toBe("");
    expect(document.body.style.position).toBe("");
    await waitFor(() => expect(document.activeElement).toBe(destinationTrigger));
  });

  it("pins and restores the root scroll position while the route dialog is open", async () => {
    useDialogLayout(true);
    vi.spyOn(window, "scrollX", "get").mockReturnValue(7);
    vi.spyOn(window, "scrollY", "get").mockReturnValue(480);
    const scrollTo = vi.spyOn(window, "scrollTo").mockImplementation(() => undefined);
    document.documentElement.style.overflow = "auto";
    document.documentElement.style.overscrollBehavior = "contain";
    document.body.style.position = "relative";
    document.body.style.top = "3px";
    document.body.style.left = "2px";
    render(<RouteHarness />);

    await userEvent.setup().click(
      screen.getByRole("button", { name: "출발역 역 이름 또는 지역 검색" }),
    );

    expect(document.documentElement.style.overflow).toBe("hidden");
    expect(document.documentElement.style.overscrollBehavior).toBe("none");
    expect(document.body.style.position).toBe("fixed");
    expect(document.body.style.top).toBe("-480px");
    expect(document.body.style.left).toBe("-7px");
    expect(document.body.style.width).toBe("100%");

    await userEvent.setup().click(screen.getByRole("button", { name: "역 선택 닫기" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "여정 역 선택" })).toBeNull());
    expect(document.documentElement.style.overflow).toBe("auto");
    expect(document.documentElement.style.overscrollBehavior).toBe("contain");
    expect(document.body.style.position).toBe("relative");
    expect(document.body.style.top).toBe("3px");
    expect(document.body.style.left).toBe("2px");
    expect(document.body.style.width).toBe("");
    expect(scrollTo).toHaveBeenCalledWith(7, 480);
  });

  it("traps focus, restores the trigger, and ignores Enter while Korean IME is composing", async () => {
    useDialogLayout(true);
    const user = userEvent.setup();
    render(<RouteHarness />);

    const originTrigger = screen.getByRole("button", { name: "출발역 역 이름 또는 지역 검색" });
    await user.click(originTrigger);
    const dialog = screen.getByRole("dialog", { name: "여정 역 선택" });
    const search = within(dialog).getByRole("combobox", { name: "출발역 검색" });
    fireEvent.keyDown(search, { key: "Enter", isComposing: true });
    expect(within(dialog).getByRole("combobox", { name: "출발역 검색" })).toBe(search);
    expect(within(dialog).getByRole("button", { name: /출발역 선택하세요/ })).toBeTruthy();

    const close = within(dialog).getByRole("button", { name: "역 선택 닫기" });
    const regionButtons = within(dialog).getByRole("navigation", { name: "지역 선택" }).querySelectorAll("button");
    const lastRegion = regionButtons.item(regionButtons.length - 1);
    close.focus();
    await user.tab({ shift: true });
    expect(document.activeElement).toBe(lastRegion);

    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "여정 역 선택" })).toBeNull());
    await waitFor(() => expect(document.activeElement).toBe(originTrigger));
    expect(screen.getByRole("alert").textContent).toContain("출발역을 제공된 역 목록에서 선택");
  });

  it("keeps search focus when clearing and scrolls the keyboard-active option into view", async () => {
    useDialogLayout(true);
    const scrollIntoView = vi.fn();
    const user = userEvent.setup();
    render(<RouteHarness />);

    await user.click(screen.getByRole("button", { name: "출발역 역 이름 또는 지역 검색" }));
    const dialog = screen.getByRole("dialog", { name: "여정 역 선택" });
    const search = within(dialog).getByRole("combobox", { name: "출발역 검색" });
    await user.click(search);
    await user.type(search, "서");

    const clear = within(dialog).getByRole("button", { name: "역 검색어 지우기" });
    fireEvent.pointerDown(clear);
    await user.click(clear);
    expect((search as HTMLInputElement).value).toBe("");
    expect(document.activeElement).toBe(search);

    const options = within(dialog).getAllByRole("option");
    Object.defineProperty(options[1], "scrollIntoView", { value: scrollIntoView });
    fireEvent.keyDown(search, { key: "ArrowDown" });
    await waitFor(() => expect(scrollIntoView).toHaveBeenCalledWith({ block: "nearest" }));
  });
});
