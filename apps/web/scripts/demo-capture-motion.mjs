/* global document, HTMLElement, window */

const motion = Object.freeze({
  cursorDurationMs: 460,
  cursorSettleMs: 80,
  clickPulseMs: 360,
  zoomInMs: 480,
  zoomOutMs: 540,
  easing: "cubic-bezier(0.22, 1, 0.36, 1)",
});

const wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

async function locatorCenter(locator) {
  await locator.scrollIntoViewIfNeeded();
  const box = await locator.boundingBox();
  if (!box) throw new Error("데모 동작 대상을 화면에서 찾지 못했습니다.");
  return {
    x: box.x + box.width / 2,
    y: box.y + box.height / 2,
  };
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

      #railwait-demo-camera {
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

async function createCameraSnapshot(page, point, zoomScale) {
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

  return page.evaluate(
    async ({ easing, image, point, zoomInMs, zoomScale }) => {
      const clampValue = (value, minimum, maximum) =>
        Math.min(Math.max(value, minimum), maximum);
      document.querySelector("#railwait-demo-camera")?.remove();
      const camera = document.createElement("img");
      camera.id = "railwait-demo-camera";
      camera.alt = "";
      camera.src = `data:image/png;base64,${image}`;
      document.body.append(camera);
      await camera.decode();

      const viewportWidth = window.innerWidth;
      const viewportHeight = window.innerHeight;
      const desiredX = clampValue(point.x, viewportWidth * 0.34, viewportWidth * 0.66);
      const desiredY = clampValue(point.y, viewportHeight * 0.3, viewportHeight * 0.7);
      const minimumX = viewportWidth - viewportWidth * zoomScale;
      const minimumY = viewportHeight - viewportHeight * zoomScale;
      const translateX = clampValue(desiredX - point.x * zoomScale, minimumX, 0);
      const translateY = clampValue(desiredY - point.y * zoomScale, minimumY, 0);
      const cursorX = point.x * zoomScale + translateX;
      const cursorY = point.y * zoomScale + translateY;
      const zoomTransform = `translate3d(${translateX}px, ${translateY}px, 0) scale(${zoomScale})`;

      camera.style.transform = "translate3d(0, 0, 0) scale(1)";
      camera.style.opacity = "1";
      camera.getBoundingClientRect();
      camera.style.transition = `transform ${zoomInMs}ms ${easing}`;
      camera.style.transform = zoomTransform;

      const cursor = document.querySelector("#railwait-demo-cursor");
      if (cursor instanceof HTMLElement) {
        cursor.style.transition = `transform ${zoomInMs}ms ${easing}`;
        cursor.style.transform = `translate3d(${cursorX}px, ${cursorY}px, 0)`;
        cursor.dataset.x = String(cursorX);
        cursor.dataset.y = String(cursorY);
      }
      return { x: cursorX, y: cursorY };
    },
    {
      easing: motion.easing,
      image: screenshot.toString("base64"),
      point,
      zoomInMs: motion.zoomInMs,
      zoomScale,
    },
  );
}

async function revealCameraResult(page) {
  await page.evaluate(({ easing, zoomOutMs }) => {
    const camera = document.querySelector("#railwait-demo-camera");
    if (!(camera instanceof HTMLElement)) return;
    camera.style.transition = `transform ${zoomOutMs}ms ${easing}, opacity ${zoomOutMs}ms ease`;
    camera.style.transform = "translate3d(0, 0, 0) scale(1)";
    camera.style.opacity = "0";
  }, { easing: motion.easing, zoomOutMs: motion.zoomOutMs });
  await wait(motion.zoomOutMs);
  await page.evaluate(() => document.querySelector("#railwait-demo-camera")?.remove());
}

export async function clickWithDemoMotion(page, locator, options = {}) {
  const point = await locatorCenter(locator);
  await moveCursor(page, point, options.cursorDurationMs);

  let visiblePoint = point;
  if (options.zoomScale) {
    visiblePoint = await createCameraSnapshot(page, point, options.zoomScale);
    await wait(motion.zoomInMs);
  }

  await beginClickPulse(page, visiblePoint);
  await locator.click();
  await wait(motion.clickPulseMs);
  await endClickPulse(page);
  await wait(options.beforeRevealMs ?? 120);

  if (options.zoomScale) await revealCameraResult(page);
  if (options.resultHoldMs) await wait(options.resultHoldMs);
}

export async function focusWithDemoMotion(page, locator, options = {}) {
  const point = await locatorCenter(locator);
  await moveCursor(page, point, options.cursorDurationMs);
  await page.evaluate(() => {
    const cursor = document.querySelector("#railwait-demo-cursor");
    if (cursor instanceof HTMLElement) cursor.style.opacity = "0";
  });
  await createCameraSnapshot(page, point, options.zoomScale ?? 1.16);
  await wait(motion.zoomInMs + (options.holdMs ?? 900));
  await revealCameraResult(page);
}

export async function hideDemoCursor(page) {
  await page.evaluate(() => {
    const cursor = document.querySelector("#railwait-demo-cursor");
    if (cursor instanceof HTMLElement) cursor.style.opacity = "0";
  });
  await wait(180);
}
