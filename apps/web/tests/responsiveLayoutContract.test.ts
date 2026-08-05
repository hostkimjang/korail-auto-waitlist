import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const styleFileNames = [
  "tokens.css",
  "base.css",
  "shell.css",
  "features.css",
  "operations.css",
  "app-surfaces.css",
  "responsive.css",
] as const;
const styleDirectory = resolve(process.cwd(), "src/styles");
const readStyleFile = (fileName: (typeof styleFileNames)[number]): string =>
  readFileSync(resolve(styleDirectory, fileName), "utf8");
const tokensStyles = readStyleFile("tokens.css");
const baseStyles = readStyleFile("base.css");
const shellStyles = readStyleFile("shell.css");
const featureStyles = readStyleFile("features.css");
const operationsStyles = readStyleFile("operations.css");
const appSurfaceStyles = readStyleFile("app-surfaces.css");
const responsiveStyles = readStyleFile("responsive.css");
const styles = [
  tokensStyles,
  baseStyles,
  shellStyles,
  featureStyles,
  operationsStyles,
  appSurfaceStyles,
  responsiveStyles,
].join("");

interface CssBlock {
  body: string;
  end: number;
}

function extractCssBlock(source: string, header: string, fromIndex = 0): CssBlock {
  const headerIndex = source.indexOf(header, fromIndex);
  if (headerIndex < 0) throw new Error(`CSS header not found: ${header}`);

  const openingBrace = source.indexOf("{", headerIndex + header.length);
  if (openingBrace < 0) throw new Error(`CSS block has no opening brace: ${header}`);

  let depth = 0;
  let quote: '"' | "'" | null = null;
  let inComment = false;

  for (let index = openingBrace; index < source.length; index += 1) {
    const current = source[index];
    const next = source[index + 1];

    if (inComment) {
      if (current === "*" && next === "/") {
        inComment = false;
        index += 1;
      }
      continue;
    }

    if (quote !== null) {
      if (current === "\\") {
        index += 1;
      } else if (current === quote) {
        quote = null;
      }
      continue;
    }

    if (current === "/" && next === "*") {
      inComment = true;
      index += 1;
    } else if (current === '"' || current === "'") {
      quote = current;
    } else if (current === "{") {
      depth += 1;
    } else if (current === "}") {
      depth -= 1;
      if (depth === 0) {
        return { body: source.slice(openingBrace + 1, index), end: index + 1 };
      }
    }
  }

  throw new Error(`CSS block is not closed: ${header}`);
}

describe("global CSS structure", () => {
  it("loads the seven style boundaries in their cascade order", () => {
    const entryStyles = readFileSync(resolve(process.cwd(), "src/styles.css"), "utf8");
    const expectedImports = `${styleFileNames
      .map((fileName) => `@import "./styles/${fileName}";`)
      .join("\n")}\n`;

    expect(entryStyles).toBe(expectedImports);
  });

  it("keeps tokens, base, shell, feature, operations, app surface, and responsive rules in their owners", () => {
    expect(tokensStyles.trimStart()).toMatch(/^:root\s*\{/);
    expect(tokensStyles).toContain("--shadow:");

    expect(baseStyles.trimStart()).toMatch(/^\*\s*\{/);
    expect(baseStyles).toContain("button:focus-visible");

    expect(shellStyles.trimStart()).toMatch(/^\.app-shell\s*\{/);
    expect(shellStyles).toContain(".icon-button.danger:hover");

    expect(featureStyles.trimStart()).toMatch(/^\.payment-hero\s*\{/);
    expect(featureStyles).toContain("container-name: train-results");
    expect(extractCssBlock(featureStyles, "@container train-results (min-width: 920px)").body)
      .toContain(".train-result-card");
    expect(featureStyles.trimEnd()).toMatch(/\.system-grid strong\s*\{[\s\S]*color:\s*#17776f;[\s\S]*\}$/);

    expect(operationsStyles.trimStart()).toMatch(/^\.operations-dashboard\s*\{/);
    expect(operationsStyles).toContain(".operations-event-list");
    const reducedMotionHeader = "@media (prefers-reduced-motion: reduce)";
    const operationsReducedMotion = extractCssBlock(
      operationsStyles,
      reducedMotionHeader,
      operationsStyles.lastIndexOf(reducedMotionHeader),
    );
    expect(operationsReducedMotion.body).toContain(".operations-skeleton");
    expect(operationsStyles.slice(operationsReducedMotion.end).trim()).toBe("");

    expect(appSurfaceStyles.trimStart()).toMatch(/^\.toast\s*\{/);
    expect(appSurfaceStyles).toContain(".notification-center");
    expect(appSurfaceStyles).toContain(".auth-page");
    const toastStepSpin = extractCssBlock(appSurfaceStyles, "@keyframes toast-step-spin");
    expect(toastStepSpin.body).toContain("transform: rotate(360deg)");
    const toastIn = extractCssBlock(appSurfaceStyles, "@keyframes toast-in");
    expect(toastIn.body).toContain("from { opacity: 0; transform: translateY(10px); }");
    expect(appSurfaceStyles.slice(toastIn.end).trim()).toBe("");

    expect(responsiveStyles.trimStart()).toMatch(/^@media \(max-width: 980px\)/);
    const reducedMotion = extractCssBlock(responsiveStyles, "@media (prefers-reduced-motion: reduce)");
    expect(reducedMotion.body).toContain("animation-duration: 0.01ms !important");
    expect(responsiveStyles.slice(reducedMotion.end).trim()).toBe("");
  });
});

describe("responsive layout CSS contracts", () => {
  it("reflows active watches by actual container width and isolates the policy row", () => {
    expect(styles).toContain("container-name: active-watch-list");
    const container1080 = extractCssBlock(
      styles,
      "@container active-watch-list (max-width: 1080px)",
    ).body;
    const rowActions = extractCssBlock(container1080, ".row-actions").body;
    expect(rowActions).toContain('"policy controls"');
    expect(rowActions).toContain('"booking booking"');

    const policyControl = extractCssBlock(container1080, ".watch-policy-control").body;
    expect(policyControl).toMatch(/grid-template-columns:\s*minmax\(0, 1fr\) 44px\s*;/);
    expect(policyControl).toMatch(/white-space:\s*normal\s*;/);

    const policyLabel = extractCssBlock(container1080, ".watch-policy-label").body;
    expect(policyLabel).toMatch(/overflow-wrap:\s*anywhere\s*;/);

    const container760 = extractCssBlock(
      styles,
      "@container active-watch-list (max-width: 760px)",
    ).body;
    const watchRow760 = extractCssBlock(container760, ".watch-row").body;
    expect(watchRow760).toContain('"state state"');
    expect(watchRow760).toContain('"actions actions"');

    const container520 = extractCssBlock(
      styles,
      "@container active-watch-list (max-width: 520px)",
    ).body;
    const rowActions520 = extractCssBlock(container520, ".row-actions").body;
    expect(rowActions520).toContain('"policy"');
    expect(rowActions520).toContain('"booking"');
    expect(rowActions520).toContain('"controls"');
  });

  it("uses one bounded notification surface without the removed second fixed offset", () => {
    const notificationCenter = extractCssBlock(styles, ".notification-center").body;
    expect(notificationCenter).toMatch(/width:\s*min\(560px, calc\(100vw - 48px\)\)\s*;/);
    const notificationBody = extractCssBlock(styles, ".notification-center-body").body;
    expect(notificationBody).toMatch(/max-height:\s*min\(70dvh, 560px\)\s*;/);
    expect(styles).not.toContain(".seat-found-alert");
    expect(styles).not.toContain("+ 184px");
    expect(styles).not.toContain("+ 238px");
  });

  it("keeps the notification switch target at least 44px tall", () => {
    const switchRule = extractCssBlock(styles, ".switch").body;
    expect(switchRule).toMatch(/min-width:\s*46px\s*;/);
    expect(switchRule).toMatch(/min-height:\s*44px\s*;/);
  });
});
