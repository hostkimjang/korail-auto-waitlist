/* global document, HTMLElement, window */

const motion = Object.freeze({
  cursorDurationMs: 460,
  cursorSettleMs: 80,
  clickPulseMs: 360,
  pageTransitionMs: 320,
  zoomInMs: 480,
  zoomOutMs: 540,
  easing: "cubic-bezier(0.22, 1, 0.36, 1)",
});

const wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

async function locatorCenter(locator) {
  const box = await locator.boundingBox();
  if (!box) throw new Error("데모 동작 대상을 화면에서 찾지 못했습니다.");
  return {
    x: box.x + box.width / 2,
    y: box.y + box.height / 2,
  };
}

export async function smoothScrollTo(page, targetY, options = {}) {
  if (options.hideCursor) {
    await page.evaluate(() => {
      const cursor = document.querySelector("#railwait-demo-cursor");
      if (cursor instanceof HTMLElement) cursor.style.opacity = "0";
    });
  }

  await page.evaluate(
    async ({ durationMs, targetY }) => {
      if (document.querySelector("#railwait-demo-transition")) {
        throw new Error("화면 전환이 끝나기 전에 스크롤할 수 없습니다.");
      }
      const scroller = document.scrollingElement;
      if (!scroller) throw new Error("문서 스크롤 영역을 찾지 못했습니다.");

      const maximum = Math.max(0, scroller.scrollHeight - window.innerHeight);
      const destination = Math.min(Math.max(targetY, 0), maximum);
      const start = window.scrollY;
      const distance = destination - start;
      const duration =
        durationMs ?? Math.min(Math.max(420 + Math.abs(distance) * 0.22, 480), 720);

      if (Math.abs(distance) < 1) {
        await new Promise((resolve) =>
          window.requestAnimationFrame(() => window.requestAnimationFrame(resolve)),
        );
        return;
      }

      await new Promise((resolve) => {
        const startedAt = window.performance.now();
        const step = (now) => {
          const progress = Math.min((now - startedAt) / duration, 1);
          const eased =
            progress < 0.5
              ? 4 * progress ** 3
              : 1 - ((-2 * progress + 2) ** 3) / 2;
          window.scrollTo(0, start + distance * eased);
          if (progress < 1) window.requestAnimationFrame(step);
          else {
            window.scrollTo(0, destination);
            window.requestAnimationFrame(() => window.requestAnimationFrame(resolve));
          }
        };
        window.requestAnimationFrame(step);
      });
    },
    { durationMs: options.durationMs, targetY },
  );

  if (options.settleMs) await wait(options.settleMs);
}

export async function smoothScrollLocatorIntoView(page, locator, options = {}) {
  const target = await locator.evaluate(
    (element, { align, force }) => {
      const rect = element.getBoundingClientRect();
      const safeTop = window.innerHeight * 0.16;
      const safeBottom = window.innerHeight * 0.84;
      if (!force && rect.top >= safeTop && rect.bottom <= safeBottom) return window.scrollY;

      const maximum = Math.max(
        0,
        (document.scrollingElement?.scrollHeight ?? document.documentElement.scrollHeight) -
          window.innerHeight,
      );
      const centered =
        window.scrollY + rect.top + rect.height / 2 - window.innerHeight * align;
      return Math.min(Math.max(centered, 0), maximum);
    },
    { align: options.align ?? 0.46, force: options.force ?? false },
  );
  await smoothScrollTo(page, target, options);
}

export async function installDemoCaptureMotion(page) {
  await page.addStyleTag({
    content: `
      #railwait-demo-effects {
        position: fixed;
        inset: 0;
        overflow: hidden;
        pointer-events: none;
        z-index: 2147483646;
      }

      #railwait-demo-cursor {
        position: absolute;
        top: 0;
        left: 0;
        width: 34px;
        height: 42px;
        opacity: 0;
        transform: translate3d(1120px, 720px, 0);
        transform-origin: 4px 4px;
        filter: drop-shadow(0 3px 5px rgb(11 35 69 / 0.28));
        will-change: transform, opacity;
      }

      #railwait-demo-disclaimer {
        position: absolute;
        top: 14px;
        right: 18px;
        padding: 7px 11px;
        border: 1px solid rgb(255 255 255 / 0.5);
        border-radius: 999px;
        background: rgb(8 47 82 / 0.88);
        box-shadow: 0 6px 18px rgb(8 47 82 / 0.18);
        color: #ffffff;
        font: 700 12px/1.2 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        letter-spacing: -0.01em;
        backdrop-filter: blur(8px);
      }

      #railwait-demo-cursor svg {
        display: block;
        width: 100%;
        height: 100%;
        transform-origin: 4px 4px;
        transition: transform 120ms ease;
      }

      #railwait-demo-cursor.is-pressed svg {
        transform: scale(0.88);
      }

      .railwait-demo-ring {
        position: absolute;
        width: 18px;
        height: 18px;
        margin: -9px 0 0 -9px;
        border: 3px solid rgb(13 148 136 / 0.9);
        border-radius: 999px;
        box-shadow: 0 0 0 2px rgb(255 255 255 / 0.9);
        animation: railwait-demo-pulse 360ms cubic-bezier(0.22, 1, 0.36, 1) forwards;
        will-change: transform, opacity;
      }

      .railwait-demo-ring.is-delayed {
        animation-delay: 70ms;
        border-color: rgb(14 116 144 / 0.66);
      }

      #railwait-demo-transition {
        position: fixed;
        inset: 0;
        width: 100vw;
        height: 100vh;
        object-fit: fill;
        pointer-events: none;
        transform-origin: 0 0;
        will-change: transform, opacity;
        z-index: 2147483645;
      }

      @keyframes railwait-demo-pulse {
        0% { opacity: 0.94; transform: scale(0.45); }
        100% { opacity: 0; transform: scale(2.15); }
      }
    `,
  });

  await page.evaluate(() => {
    document.querySelector("#railwait-demo-effects")?.remove();
    const effects = document.createElement("div");
    effects.id = "railwait-demo-effects";
    effects.setAttribute("aria-hidden", "true");
    effects.innerHTML = `
      <div id="railwait-demo-disclaimer">연출 데모 · 실제 예약 아님</div>
      <div id="railwait-demo-cursor">
        <svg viewBox="0 0 34 42" aria-hidden="true">
          <path
            d="M4 3.2v29.4l7.7-7.4 6.4 13.3 5.2-2.6-6.2-12.8h11.2z"
            fill="#ffffff"
            stroke="#0b2345"
            stroke-linejoin="round"
            stroke-width="2.2"
          />
        </svg>
      </div>
    `;
    document.body.append(effects);
  });
}

async function moveCursor(page, point, durationMs = motion.cursorDurationMs) {
  await page.evaluate(
    ({ durationMs, easing, x, y }) => {
      const cursor = document.querySelector("#railwait-demo-cursor");
      if (!(cursor instanceof HTMLElement)) throw new Error("데모 커서가 설치되지 않았습니다.");

      const startX = Number(cursor.dataset.x ?? 1120);
      const startY = Number(cursor.dataset.y ?? 720);
      const bend = Math.min(34, Math.hypot(x - startX, y - startY) * 0.06);
      const midpointX = startX + (x - startX) * 0.52 - Math.sign(y - startY || 1) * bend;
      const midpointY = startY + (y - startY) * 0.52 + Math.sign(x - startX || 1) * bend;
      cursor.getAnimations().forEach((animation) => animation.cancel());
      cursor.style.opacity = "1";
      cursor.animate(
        [
          { transform: `translate3d(${startX}px, ${startY}px, 0)` },
          { offset: 0.52, transform: `translate3d(${midpointX}px, ${midpointY}px, 0)` },
          { transform: `translate3d(${x}px, ${y}px, 0)` },
        ],
        { duration: durationMs, easing, fill: "forwards" },
      );
      cursor.dataset.x = String(x);
      cursor.dataset.y = String(y);
      cursor.style.transform = `translate3d(${x}px, ${y}px, 0)`;
    },
    { ...point, durationMs, easing: motion.easing },
  );
  await wait(durationMs + motion.cursorSettleMs);
}

async function beginClickPulse(page, point) {
  await page.evaluate(({ x, y }) => {
    const effects = document.querySelector("#railwait-demo-effects");
    const cursor = document.querySelector("#railwait-demo-cursor");
    if (!(effects instanceof HTMLElement) || !(cursor instanceof HTMLElement)) return;
    effects.querySelectorAll(".railwait-demo-ring").forEach((ring) => ring.remove());
    for (const delayed of [false, true]) {
      const ring = document.createElement("span");
      ring.className = `railwait-demo-ring${delayed ? " is-delayed" : ""}`;
      ring.style.left = `${x}px`;
      ring.style.top = `${y}px`;
      effects.append(ring);
    }
    cursor.classList.add("is-pressed");
  }, point);
}

async function endClickPulse(page) {
  await page.evaluate(() => {
    document
      .querySelectorAll("#railwait-demo-effects .railwait-demo-ring")
      .forEach((ring) => ring.remove());
    document.querySelector("#railwait-demo-cursor")?.classList.remove("is-pressed");
  });
}

async function captureCleanScreenshot(page) {
  await page.evaluate(() => {
    const effects = document.querySelector("#railwait-demo-effects");
    if (effects instanceof HTMLElement) effects.style.visibility = "hidden";
  });
  let screenshot;
  try {
    screenshot = await page.screenshot({ animations: "allow", type: "png" });
  } finally {
    await page.evaluate(() => {
      const effects = document.querySelector("#railwait-demo-effects");
      if (effects instanceof HTMLElement) effects.style.visibility = "visible";
    });
  }
  return screenshot;
}

async function createPageTransitionSnapshot(page) {
  const screenshot = await captureCleanScreenshot(page);
  await page.evaluate(async (image) => {
    document.querySelector("#railwait-demo-transition")?.remove();
    const transition = document.createElement("img");
    transition.id = "railwait-demo-transition";
    transition.alt = "";
    transition.src = `data:image/png;base64,${image}`;
    document.body.append(transition);
    await transition.decode();
  }, screenshot.toString("base64"));
}

async function revealPageTransition(page) {
  await page.evaluate(
    ({ durationMs, easing }) =>
      new Promise((resolve) => {
        const transition = document.querySelector("#railwait-demo-transition");
        if (!(transition instanceof HTMLElement)) {
          resolve();
          return;
        }
        let completed = false;
        const finish = () => {
          if (completed) return;
          completed = true;
          transition.remove();
          resolve();
        };
        transition.addEventListener("transitionend", finish, { once: true });
        window.setTimeout(finish, durationMs + 120);
        transition.getBoundingClientRect();
        transition.style.transition = `opacity ${durationMs}ms ease, transform ${durationMs}ms ${easing}`;
        transition.style.opacity = "0";
        transition.style.transform = "translate3d(0, -8px, 0) scale(1.004)";
      }),
    { durationMs: motion.pageTransitionMs, easing: motion.easing },
  );
}

export async function transitionWithDemoMotion(page, action, options = {}) {
  await createPageTransitionSnapshot(page);
  await page.evaluate(() => {
    const cursor = document.querySelector("#railwait-demo-cursor");
    if (cursor instanceof HTMLElement) cursor.style.opacity = "0";
  });
  await action();
  await wait(options.beforeRevealMs ?? 120);
  await revealPageTransition(page);
  if (options.resultHoldMs) await wait(options.resultHoldMs);
}

async function focusLiveRegion(page, point, options) {
  await page.evaluate(
    ({ easing, point, zoomInMs, zoomScale }) => {
      const surface = document.querySelector(".main-content");
      if (!(surface instanceof HTMLElement)) throw new Error("데모 화면을 찾지 못했습니다.");
      const rect = surface.getBoundingClientRect();
      surface.style.transformOrigin = `${point.x - rect.left}px ${point.y - rect.top}px`;
      surface.style.transform = "scale(1)";
      surface.style.transition = "none";
      surface.style.willChange = "transform";
      surface.getBoundingClientRect();
      surface.style.transition = `transform ${zoomInMs}ms ${easing}`;
      surface.style.transform = `scale(${zoomScale})`;
    },
    {
      easing: motion.easing,
      point,
      zoomInMs: motion.zoomInMs,
      zoomScale: options.zoomScale,
    },
  );
  await wait(motion.zoomInMs + options.holdMs);
  await page.evaluate(
    ({ easing, zoomOutMs }) =>
      new Promise((resolve) => {
        const surface = document.querySelector(".main-content");
        if (!(surface instanceof HTMLElement)) {
          resolve();
          return;
        }
        let completed = false;
        const finish = () => {
          if (completed) return;
          completed = true;
          surface.style.removeProperty("transform");
          surface.style.removeProperty("transform-origin");
          surface.style.removeProperty("transition");
          surface.style.removeProperty("will-change");
          resolve();
        };
        surface.addEventListener("transitionend", (event) => {
          if (event.propertyName === "transform") finish();
        });
        window.setTimeout(finish, zoomOutMs + 120);
        surface.style.transition = `transform ${zoomOutMs}ms ${easing}`;
        surface.style.transform = "scale(1)";
      }),
    { easing: motion.easing, zoomOutMs: motion.zoomOutMs },
  );
}

async function restoreCursorPosition(page, point, hidden = false) {
  await page.evaluate(({ hidden, x, y }) => {
    const cursor = document.querySelector("#railwait-demo-cursor");
    if (!(cursor instanceof HTMLElement)) return;
    cursor.getAnimations().forEach((animation) => animation.cancel());
    cursor.style.transition = "none";
    cursor.style.transform = `translate3d(${x}px, ${y}px, 0)`;
    cursor.style.opacity = hidden ? "0" : "1";
    cursor.dataset.x = String(x);
    cursor.dataset.y = String(y);
  }, { ...point, hidden });
}

export async function clickWithDemoMotion(page, locator, options = {}) {
  if (options.scroll !== false) {
    await smoothScrollLocatorIntoView(page, locator, {
      align: options.scrollAlign,
      settleMs: options.scrollSettleMs ?? 100,
    });
  }
  const point = await locatorCenter(locator);
  await moveCursor(page, point, options.cursorDurationMs);

  if (options.pageTransition) await createPageTransitionSnapshot(page);

  await beginClickPulse(page, point);
  await locator.click();
  await wait(motion.clickPulseMs);
  await endClickPulse(page);
  await wait(options.beforeRevealMs ?? 120);

  if (options.pageTransition) await revealPageTransition(page);
  if (options.resultHoldMs) await wait(options.resultHoldMs);
}

export async function focusWithDemoMotion(page, locator, options = {}) {
  if (options.scroll !== false) {
    await smoothScrollLocatorIntoView(page, locator, {
      align: options.scrollAlign,
      settleMs: options.scrollSettleMs ?? 120,
    });
  }
  const point = await locatorCenter(locator);
  await moveCursor(page, point, options.cursorDurationMs);
  await page.evaluate(() => {
    const cursor = document.querySelector("#railwait-demo-cursor");
    if (cursor instanceof HTMLElement) cursor.style.opacity = "0";
  });
  await focusLiveRegion(page, point, {
    holdMs: options.holdMs ?? 900,
    zoomScale: options.zoomScale ?? 1.16,
  });
  await restoreCursorPosition(page, point, true);
}

export async function hideDemoCursor(page) {
  await page.evaluate(() => {
    const cursor = document.querySelector("#railwait-demo-cursor");
    if (cursor instanceof HTMLElement) cursor.style.opacity = "0";
  });
  await wait(180);
}
