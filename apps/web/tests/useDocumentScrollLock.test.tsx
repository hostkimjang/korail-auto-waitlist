import { render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useDocumentScrollLock } from "../src/hooks/useDocumentScrollLock";

function LockParticipant({ locked }: { locked: boolean }) {
  useDocumentScrollLock(locked);
  return null;
}

function resetInlineStyles(): void {
  document.documentElement.style.overflow = "";
  document.documentElement.style.overscrollBehavior = "";
  document.body.style.overflow = "";
  document.body.style.overscrollBehavior = "";
  document.body.style.position = "";
  document.body.style.top = "";
  document.body.style.right = "";
  document.body.style.left = "";
  document.body.style.width = "";
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.mocked(window.scrollTo).mockClear();
  resetInlineStyles();
});

describe("useDocumentScrollLock", () => {
  it("pins the document and restores every inline style and scroll coordinate", () => {
    vi.spyOn(window, "scrollX", "get").mockReturnValue(9);
    vi.spyOn(window, "scrollY", "get").mockReturnValue(640);
    const scrollTo = vi.spyOn(window, "scrollTo").mockImplementation(() => undefined);
    document.documentElement.style.overflow = "auto";
    document.documentElement.style.overscrollBehavior = "contain";
    document.body.style.overflow = "scroll";
    document.body.style.overscrollBehavior = "auto";
    document.body.style.position = "relative";
    document.body.style.top = "3px";
    document.body.style.right = "4px";
    document.body.style.left = "5px";
    document.body.style.width = "calc(100% - 8px)";

    const view = render(<LockParticipant locked />);

    expect(document.documentElement.style.overflow).toBe("hidden");
    expect(document.documentElement.style.overscrollBehavior).toBe("none");
    expect(document.body.style.overflow).toBe("hidden");
    expect(document.body.style.overscrollBehavior).toBe("none");
    expect(document.body.style.position).toBe("fixed");
    expect(document.body.style.top).toBe("-640px");
    expect(document.body.style.right).toBe("0px");
    expect(document.body.style.left).toBe("-9px");
    expect(document.body.style.width).toBe("100%");

    view.rerender(<LockParticipant locked={false} />);

    expect(document.documentElement.style.overflow).toBe("auto");
    expect(document.documentElement.style.overscrollBehavior).toBe("contain");
    expect(document.body.style.overflow).toBe("scroll");
    expect(document.body.style.overscrollBehavior).toBe("auto");
    expect(document.body.style.position).toBe("relative");
    expect(document.body.style.top).toBe("3px");
    expect(document.body.style.right).toBe("4px");
    expect(document.body.style.left).toBe("5px");
    expect(document.body.style.width).toBe("calc(100% - 8px)");
    expect(scrollTo).toHaveBeenCalledTimes(1);
    expect(scrollTo).toHaveBeenCalledWith(9, 640);
  });

  it("keeps the document locked until the final nested participant releases it", () => {
    const scrollTo = vi.spyOn(window, "scrollTo").mockImplementation(() => undefined);
    const view = render(
      <>
        <LockParticipant locked />
        <LockParticipant locked />
      </>,
    );

    view.rerender(
      <>
        <LockParticipant locked={false} />
        <LockParticipant locked />
      </>,
    );

    expect(document.documentElement.style.overflow).toBe("hidden");
    expect(document.body.style.position).toBe("fixed");
    expect(scrollTo).not.toHaveBeenCalled();

    view.rerender(
      <>
        <LockParticipant locked={false} />
        <LockParticipant locked={false} />
      </>,
    );

    expect(document.documentElement.style.overflow).toBe("");
    expect(document.body.style.position).toBe("");
    expect(scrollTo).toHaveBeenCalledTimes(1);
  });
});
