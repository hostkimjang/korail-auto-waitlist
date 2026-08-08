import { useEffect } from "react";

type InlineStyleSnapshot = {
  overflow: string;
  overscrollBehavior: string;
};

type BodyInlineStyleSnapshot = InlineStyleSnapshot & {
  position: string;
  top: string;
  right: string;
  left: string;
  width: string;
};

type DocumentScrollLockState = {
  count: number;
  scrollX: number;
  scrollY: number;
  root: InlineStyleSnapshot;
  body: BodyInlineStyleSnapshot;
};

let activeLock: DocumentScrollLockState | null = null;

function acquireDocumentScrollLock(): () => void {
  const root = document.documentElement;
  const body = document.body;

  if (!activeLock) {
    activeLock = {
      count: 0,
      scrollX: window.scrollX,
      scrollY: window.scrollY,
      root: {
        overflow: root.style.overflow,
        overscrollBehavior: root.style.overscrollBehavior,
      },
      body: {
        overflow: body.style.overflow,
        overscrollBehavior: body.style.overscrollBehavior,
        position: body.style.position,
        top: body.style.top,
        right: body.style.right,
        left: body.style.left,
        width: body.style.width,
      },
    };

    root.style.overflow = "hidden";
    root.style.overscrollBehavior = "none";
    body.style.overflow = "hidden";
    body.style.overscrollBehavior = "none";
    body.style.position = "fixed";
    body.style.top = `-${activeLock.scrollY}px`;
    body.style.right = "0";
    body.style.left = `-${activeLock.scrollX}px`;
    body.style.width = "100%";
  }

  activeLock.count += 1;
  let released = false;

  return () => {
    if (released || !activeLock) return;
    released = true;
    activeLock.count -= 1;
    if (activeLock.count > 0) return;

    const releasedLock = activeLock;
    activeLock = null;

    root.style.overflow = releasedLock.root.overflow;
    root.style.overscrollBehavior = releasedLock.root.overscrollBehavior;
    body.style.overflow = releasedLock.body.overflow;
    body.style.overscrollBehavior = releasedLock.body.overscrollBehavior;
    body.style.position = releasedLock.body.position;
    body.style.top = releasedLock.body.top;
    body.style.right = releasedLock.body.right;
    body.style.left = releasedLock.body.left;
    body.style.width = releasedLock.body.width;
    window.scrollTo(releasedLock.scrollX, releasedLock.scrollY);
  };
}

export function useDocumentScrollLock(locked: boolean): void {
  useEffect(() => {
    if (!locked) return undefined;
    return acquireDocumentScrollLock();
  }, [locked]);
}
