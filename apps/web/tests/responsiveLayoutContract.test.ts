import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const styleImports = [
  "./styles/tokens.css",
  "./styles/base.css",
  "./styles/shell.css",
  "./styles/features.css",
  "./styles/operations.css",
  "./styles/app-surfaces.css",
  "./features/reservations/reservations.css",
  "./features/new-wait/reservationPolicyControl.css",
  "./features/settings/timetableRefreshSettings.css",
  "./features/official-handoff/officialHandoff.css",
  "./features/new-wait/officialSeatConfirmation.css",
  "./styles/responsive.css",
] as const;
const readStyleFile = (styleImport: (typeof styleImports)[number]): string =>
  readFileSync(resolve(process.cwd(), "src", styleImport.slice(2)), "utf8");
const tokensStyles = readStyleFile("./styles/tokens.css");
const baseStyles = readStyleFile("./styles/base.css");
const shellStyles = readStyleFile("./styles/shell.css");
const featureStyles = readStyleFile("./styles/features.css");
const operationsStyles = readStyleFile("./styles/operations.css");
const appSurfaceStyles = readStyleFile("./styles/app-surfaces.css");
const reservationStyles = readStyleFile("./features/reservations/reservations.css");
const reservationPolicyControlStyles = readStyleFile(
  "./features/new-wait/reservationPolicyControl.css",
);
const timetableRefreshSettingsStyles = readStyleFile(
  "./features/settings/timetableRefreshSettings.css",
);
const officialHandoffStyles = readStyleFile(
  "./features/official-handoff/officialHandoff.css",
);
const officialSeatConfirmationStyles = readStyleFile(
  "./features/new-wait/officialSeatConfirmation.css",
);
const responsiveStyles = readStyleFile("./styles/responsive.css");
const styles = [
  tokensStyles,
  baseStyles,
  shellStyles,
  featureStyles,
  operationsStyles,
  appSurfaceStyles,
  reservationStyles,
  reservationPolicyControlStyles,
  timetableRefreshSettingsStyles,
  officialHandoffStyles,
  officialSeatConfirmationStyles,
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
  it("loads the twelve style boundaries in their cascade order", () => {
    const entryStyles = readFileSync(resolve(process.cwd(), "src/styles.css"), "utf8");
    const expectedImports = `${styleImports
      .map((styleImport) => `@import "${styleImport}";`)
      .join("\n")}\n`;

    expect(entryStyles).toBe(expectedImports);
  });

  it("keeps global and official confirmation rules in their owners", () => {
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
    expect(featureStyles).toContain(".official-handoff-note");
    expect(featureStyles).not.toContain(".reservation-summary");
    expect(featureStyles).not.toContain(".reservation-payment-deadline");
    expect(featureStyles).not.toContain(".official-handoff-layer");
    expect(featureStyles).not.toContain(".official-confirmation-");
    expect(featureStyles).not.toContain(".refresh-preference-");
    expect(featureStyles).not.toContain(".reservation-policy-");
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
    expect(appSurfaceStyles).toContain("animation: toast-step-spin 900ms linear infinite");
    const toastReducedMotion = extractCssBlock(
      appSurfaceStyles,
      "@media (prefers-reduced-motion: reduce)",
    );
    expect(toastReducedMotion.body).toContain(".toast-step-spinner");
    expect(toastReducedMotion.body).toContain("animation: none");
    const toastIn = extractCssBlock(appSurfaceStyles, "@keyframes toast-in");
    expect(toastIn.body).toContain("from { opacity: 0; transform: translateY(10px); }");
    expect(appSurfaceStyles.slice(toastIn.end).trim()).toBe("");

    expect(reservationStyles.trimStart()).toMatch(/^\.reservation-summary\s*\{/);
    expect(reservationStyles).toContain(".reservation-payment-deadline");
    const mobileReservations = extractCssBlock(
      reservationStyles,
      "@media (max-width: 760px)",
    );
    expect(mobileReservations.body).toContain(".reservation-item > .button");
    expect(reservationStyles.slice(mobileReservations.end).trim()).toBe("");

    expect(reservationPolicyControlStyles.trimStart())
      .toMatch(/^\.reservation-policy-control\s*\{/);
    const reservationPolicyHeaders = [
      ".reservation-policy-control {",
      ".reservation-policy-control legend {",
      ".reservation-policy-option {",
      ".reservation-policy-option > svg:first-child {",
      ".reservation-policy-option span {",
      ".reservation-policy-option strong {",
      ".reservation-policy-option small {",
      ".reservation-policy-option.is-selected {",
      ".reservation-policy-option:disabled {",
      ".reservation-policy-control > p {",
      ".reservation-policy-control > .reservation-policy-warning {",
      "@media (max-width: 760px)",
    ] as const;
    let previousReservationPolicyHeaderIndex = -1;
    for (const header of reservationPolicyHeaders) {
      const headerIndex = reservationPolicyControlStyles.indexOf(
        header,
        previousReservationPolicyHeaderIndex + 1,
      );
      expect(headerIndex, `${header} must preserve its original relative order`).toBeGreaterThan(
        previousReservationPolicyHeaderIndex,
      );
      previousReservationPolicyHeaderIndex = headerIndex;
    }
    const mobileReservationPolicy = extractCssBlock(
      reservationPolicyControlStyles,
      "@media (max-width: 760px)",
    );
    expect(mobileReservationPolicy.body).toContain(".reservation-policy-control legend,");
    expect(mobileReservationPolicy.body).toContain(".reservation-policy-option");
    expect(reservationPolicyControlStyles.slice(mobileReservationPolicy.end).trim()).toBe("");

    expect(timetableRefreshSettingsStyles.trimStart())
      .toMatch(/^\.refresh-preference-card\s*\{/);
    expect(timetableRefreshSettingsStyles).toContain(".refresh-preference-success");
    const refreshPreferenceHeaders = [
      ".refresh-preference-card {",
      ".refresh-preference-heading {",
      ".refresh-preference-heading > svg {",
      ".refresh-preference-heading > div {",
      ".refresh-preference-heading span,",
      ".refresh-preference-fields {",
      ".refresh-preference-fields > label,",
      ".refresh-preference-fields > label > span:first-child,",
      ".refresh-preference-fields small {",
      ".refresh-preference-live > strong {",
      ".refresh-preference-input {",
      ".refresh-preference-input input {",
      ".refresh-preference-input input:focus-visible {",
      ".refresh-preference-input:has(input:disabled) {",
      ".refresh-preference-input em {",
      ".refresh-preference-safety {",
      ".refresh-preference-safety > svg {",
      ".refresh-preference-safety p {",
      ".refresh-preference-actions {",
      ".refresh-preference-actions > span {",
      ".refresh-preference-actions > span svg {",
      ".refresh-preference-error,",
      ".refresh-preference-error {",
      ".refresh-preference-success {",
      "@media (max-width: 760px)",
    ] as const;
    let previousHeaderIndex = -1;
    for (const header of refreshPreferenceHeaders) {
      const headerIndex = timetableRefreshSettingsStyles.indexOf(header, previousHeaderIndex + 1);
      expect(headerIndex, `${header} must preserve its original relative order`).toBeGreaterThan(
        previousHeaderIndex,
      );
      previousHeaderIndex = headerIndex;
    }
    const mobileRefreshPreferences = extractCssBlock(
      timetableRefreshSettingsStyles,
      "@media (max-width: 760px)",
    );
    expect(mobileRefreshPreferences.body).toContain(".refresh-preference-card");
    expect(mobileRefreshPreferences.body).toContain(".refresh-preference-actions > .button");
    expect(timetableRefreshSettingsStyles.slice(mobileRefreshPreferences.end).trim()).toBe("");

    expect(officialHandoffStyles.trimStart()).toMatch(/^\.official-handoff-layer\s*\{/);
    expect(officialHandoffStyles).toContain(".official-handoff-copy-error");
    const mobileHandoff = extractCssBlock(
      officialHandoffStyles,
      "@media (max-width: 760px)",
    );
    expect(mobileHandoff.body).toContain(".official-handoff-actions");
    expect(officialHandoffStyles.slice(mobileHandoff.end).trim()).toBe("");

    expect(officialSeatConfirmationStyles.trimStart())
      .toMatch(/^\.official-confirmation-trigger\s*\{/);
    expect(officialSeatConfirmationStyles).toContain(".official-confirmation-layer");
    const mobileConfirmation = extractCssBlock(
      officialSeatConfirmationStyles,
      "@media (max-width: 760px)",
    );
    expect(mobileConfirmation.body).toContain(".official-confirmation-actions");
    const narrowConfirmation = extractCssBlock(
      officialSeatConfirmationStyles,
      "@media (max-width: 340px)",
    );
    expect(narrowConfirmation.body).toContain("grid-template-columns: 1fr");
    expect(officialSeatConfirmationStyles.slice(narrowConfirmation.end).trim()).toBe("");

    expect(responsiveStyles.trimStart()).toMatch(/^@media \(max-width: 980px\)/);
    expect(responsiveStyles).toContain(".official-handoff-note");
    expect(responsiveStyles).not.toContain(".reservation-summary");
    expect(responsiveStyles).not.toContain(".reservation-payment-deadline");
    expect(responsiveStyles).not.toContain(".official-handoff-layer");
    expect(responsiveStyles).not.toContain(".official-confirmation-");
    expect(responsiveStyles).not.toContain(".refresh-preference-");
    expect(responsiveStyles).not.toContain(".reservation-policy-");
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

  it("wraps long operational labels and keeps the tablet trailing action clear", () => {
    const operationalStatus = extractCssBlock(
      featureStyles,
      ".watch-state > span.watch-operational",
    ).body;
    expect(operationalStatus).toMatch(/max-width:\s*100%\s*;/);
    expect(operationalStatus).toMatch(/overflow-wrap:\s*anywhere\s*;/);
    expect(operationalStatus).toMatch(/white-space:\s*normal\s*;/);

    const portraitTablet = extractCssBlock(
      responsiveStyles,
      "@media (min-width: 761px) and (max-width: 980px)",
    ).body;
    const trailingAction = extractCssBlock(portraitTablet, ".button-new-wide").body;
    expect(trailingAction).toMatch(/margin-top:\s*12px\s*;/);
  });

  it("uses one bounded notification surface without the removed second fixed offset", () => {
    const notificationCenter = extractCssBlock(styles, ".notification-center").body;
    expect(notificationCenter).toMatch(/width:\s*min\(560px, calc\(100vw - 48px\)\)\s*;/);
    expect(notificationCenter).toContain("env(safe-area-inset-top)");
    const notificationBody = extractCssBlock(styles, ".notification-center-body").body;
    expect(notificationBody).toMatch(/max-height:\s*min\(70dvh, 560px\)\s*;/);
    const peekDetail = extractCssBlock(styles, ".notification-center-peek-detail,").body;
    expect(peekDetail).toMatch(/min-width:\s*44px\s*;/);
    expect(peekDetail).toMatch(/min-height:\s*44px\s*;/);
    const narrow = extractCssBlock(
      responsiveStyles,
      "@media (max-width: 340px)",
      responsiveStyles.lastIndexOf("@media (max-width: 340px)"),
    ).body;
    const narrowPeek = extractCssBlock(narrow, ".notification-center-peek").body;
    expect(narrowPeek).toContain("grid-template-columns: minmax(0, 1fr) 44px");
    expect(styles).not.toContain(".seat-found-alert");
    expect(styles).not.toContain("+ 184px");
    expect(styles).not.toContain("+ 238px");
  });

  it("stacks the calendar above notifications and below blocking provider dialogs", () => {
    const notificationCenter = extractCssBlock(appSurfaceStyles, ".notification-center").body;
    const calendarScrim = extractCssBlock(featureStyles, ".date-field > .popover-scrim").body;
    const calendarDialog = extractCssBlock(
      featureStyles,
      ".journey-popover.calendar-popover",
    ).body;
    const officialHandoff = extractCssBlock(officialHandoffStyles, ".official-handoff-layer").body;
    const officialConfirmation = extractCssBlock(
      officialSeatConfirmationStyles,
      ".official-confirmation-layer",
    ).body;

    expect(notificationCenter).toMatch(/z-index:\s*102\s*;/);
    expect(calendarScrim).toMatch(/z-index:\s*109\s*;/);
    expect(calendarDialog).toMatch(/z-index:\s*110\s*;/);
    expect(officialHandoff).toMatch(/z-index:\s*120\s*;/);
    expect(officialConfirmation).toMatch(/z-index:\s*125\s*;/);
  });

  it("keeps the notification switch target at least 44px tall", () => {
    const switchRule = extractCssBlock(styles, ".switch").body;
    expect(switchRule).toMatch(/min-width:\s*46px\s*;/);
    expect(switchRule).toMatch(/min-height:\s*44px\s*;/);
  });
});
