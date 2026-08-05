import { mkdir, readFile, rm } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "@playwright/test";

const sourcePath = fileURLToPath(new URL("./railwait-marketing.html", import.meta.url));
const iconPath = fileURLToPath(new URL("../public/icons/app-icon-512.png", import.meta.url));
const outputDirectory = fileURLToPath(new URL("../../../output/marketing-video/", import.meta.url));
const rawVideoPath = join(outputDirectory, "railwait-intro.webm");

async function capture() {
  await rm(outputDirectory, { recursive: true, force: true });
  await mkdir(outputDirectory, { recursive: true });

  const [template, icon] = await Promise.all([
    readFile(sourcePath, "utf8"),
    readFile(iconPath),
  ]);
  const iconDataUrl = `data:image/png;base64,${icon.toString("base64")}`;
  const html = template.replaceAll("{{APP_ICON_DATA_URL}}", iconDataUrl);

  const browser = await chromium.launch({ headless: true });
  try {
    const context = await browser.newContext({
      locale: "ko-KR",
      timezoneId: "Asia/Seoul",
      viewport: { width: 1280, height: 720 },
      recordVideo: { dir: outputDirectory, size: { width: 1280, height: 720 } },
      reducedMotion: "no-preference",
    });
    const errors = [];
    context.on("page", (page) => {
      page.on("console", (message) => {
        if (message.type() === "error") errors.push(message.text());
      });
      page.on("pageerror", (error) => errors.push(error.message));
    });

    const page = await context.newPage();
    const video = page.video();
    await page.setContent(html, { waitUntil: "load" });
    await page.evaluate("document.fonts.ready");
    await page.waitForTimeout(21_500);

    if (errors.length > 0) {
      throw new Error(`마케팅 애니메이션 렌더링 오류: ${errors.join(" | ")}`);
    }

    await context.close();
    if (!video) throw new Error("Playwright video recorder가 생성되지 않았습니다.");
    await video.saveAs(rawVideoPath);
  } finally {
    await browser.close();
  }

  console.log(`마케팅 애니메이션 원본: ${rawVideoPath}`);
}

await capture();
