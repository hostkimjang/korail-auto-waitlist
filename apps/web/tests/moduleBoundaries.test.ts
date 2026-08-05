import { existsSync, readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const TEST_DIRECTORY = path.dirname(fileURLToPath(import.meta.url));
const SOURCE_DIRECTORY = path.resolve(TEST_DIRECTORY, "../src");
const SOURCE_EXTENSIONS = new Set([".js", ".jsx", ".ts", ".tsx"]);

interface ImportEdge {
  importer: string;
  imported: string;
  specifier: string;
}

interface AllowedViolation {
  edge: string;
  removalReason: string;
}

const ALLOWED_VIOLATIONS: Readonly<Record<string, readonly AllowedViolation[]>> = {};

function sourcePath(filePath: string): string {
  return path.relative(SOURCE_DIRECTORY, filePath).split(path.sep).join("/");
}

function sourceFiles(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true })
    .flatMap((entry) => {
      const entryPath = path.join(directory, entry.name);
      if (entry.isDirectory()) {
        return sourceFiles(entryPath);
      }
      return SOURCE_EXTENSIONS.has(path.extname(entry.name)) ? [entryPath] : [];
    })
    .sort();
}

function importedModuleSpecifiers(filePath: string): string[] {
  const source = readFileSync(filePath, "utf8");
  const specifiers: string[] = [];
  const patterns = [
    /(?:^|\r?\n)\s*import\s+(?:(?:type\s+)?[^;]*?\s+from\s+)?["']([^"']+)["']\s*;?/g,
    /(?:^|\r?\n)\s*export\s+(?:type\s+)?(?:\*|\{)[^;]*?\s+from\s+["']([^"']+)["']\s*;?/g,
  ];
  for (const pattern of patterns) {
    for (const match of source.matchAll(pattern)) {
      const specifier = match[1];
      if (specifier !== undefined) {
        specifiers.push(specifier);
      }
    }
  }
  return specifiers;
}

function importEdges(): ImportEdge[] {
  return sourceFiles(SOURCE_DIRECTORY).flatMap((filePath) => {
    const importer = sourcePath(filePath);
    return importedModuleSpecifiers(filePath).map((specifier) => ({
      importer,
      imported: specifier.startsWith(".")
        ? sourcePath(path.resolve(path.dirname(filePath), specifier))
        : specifier,
      specifier,
    }));
  });
}

function featureName(sourceModule: string): string | null {
  const segments = sourceModule.split("/");
  return segments[0] === "features" ? (segments[1] ?? null) : null;
}

function edgeName(edge: ImportEdge): string {
  return `${edge.importer} -> ${edge.imported}`;
}

function isReactImport(specifier: string): boolean {
  return specifier === "react"
    || specifier.startsWith("react/")
    || specifier === "react-dom"
    || specifier.startsWith("react-dom/");
}

function isNetworkImport(edge: ImportEdge): boolean {
  const networkPackages = new Set(["axios", "cross-fetch", "ky", "node-fetch", "undici"]);
  return edge.imported.startsWith("api/")
    || networkPackages.has(edge.specifier);
}

function assertRatchet(rule: string, violations: ImportEdge[]): void {
  const allowed = ALLOWED_VIOLATIONS[rule] ?? [];
  const allowedEdges = new Set(allowed.map(({ edge }) => edge));
  const actualEdges = violations.map(edgeName).sort();
  const newViolations = actualEdges.filter((edge) => !allowedEdges.has(edge));
  const staleAllowlist = allowed
    .filter(({ edge }) => !actualEdges.includes(edge))
    .map(({ edge, removalReason }) => `${edge} (${removalReason})`);

  expect(
    newViolations,
    `새 모듈 경계 위반입니다. 의존 방향을 바로잡거나 실제 공용 계약을 중립 계층으로 이동하세요.`,
  ).toEqual([]);
  expect(
    staleAllowlist,
    `기존 위반이 제거되었습니다. 해당 allowlist 항목도 삭제해 경계를 강화하세요.`,
  ).toEqual([]);
}

describe("module dependency boundaries", () => {
  const edges = importEdges();

  it("keeps watch value contracts in one canonical domain owner", () => {
    const declarationPattern = /\btype\s+(WatchObservationMode|WatchProvider|WatchSeatClass|WatchStatus)\s*=/g;
    const declarations = sourceFiles(SOURCE_DIRECTORY).flatMap((filePath) => (
      [...readFileSync(filePath, "utf8").matchAll(declarationPattern)].map((match) => (
        `${sourcePath(filePath)}:${match[1]}`
      ))
    ));

    expect(declarations).toEqual([
      "domain/watch.ts:WatchSeatClass",
      "domain/watch.ts:WatchProvider",
      "domain/watch.ts:WatchStatus",
      "domain/watch.ts:WatchObservationMode",
    ]);
  });

  it("keeps watch projection contracts in one application read-model owner", () => {
    const declarationPattern = /\bexport\s+(?:interface|type)\s+(MappedWatchCandidate|SeatFoundObservation|ReservationCandidateContext|WatchReadModel|MappedWatch|ProjectedWatch)\b/g;
    const declarations = sourceFiles(SOURCE_DIRECTORY).flatMap((filePath) => (
      [...readFileSync(filePath, "utf8").matchAll(declarationPattern)].map((match) => (
        `${sourcePath(filePath)}:${match[1]}`
      ))
    ));

    expect(declarations).toEqual([
      "api/watchProjection.ts:MappedWatchCandidate",
      "api/watchProjection.ts:SeatFoundObservation",
      "api/watchProjection.ts:ReservationCandidateContext",
      "api/watchProjection.ts:WatchReadModel",
      "api/watchProjection.ts:MappedWatch",
      "api/watchProjection.ts:ProjectedWatch",
    ]);

    const projection = readFileSync(
      path.join(SOURCE_DIRECTORY, "api/watchProjection.ts"),
      "utf8",
    );
    expect(projection).toMatch(/type ProjectedWatch = WatchReadModel & MappedWatch/);
  });

  it("keeps Home active-watch presentation contracts in the feature owner", () => {
    const declarationPattern = /\bexport\s+(?:type|interface)\s+(RailAccountAuthStatus|SeatFoundObservationContext|ActiveWatch|ActiveWatchRowPresentation)\b/g;
    const declarations = sourceFiles(SOURCE_DIRECTORY).flatMap((filePath) => (
      [...readFileSync(filePath, "utf8").matchAll(declarationPattern)].map((match) => (
        `${sourcePath(filePath)}:${match[1]}`
      ))
    ));

    expect(declarations).toEqual([
      "features/home/activeWatchViewModel.ts:RailAccountAuthStatus",
      "features/home/activeWatchViewModel.ts:SeatFoundObservationContext",
      "features/home/activeWatchViewModel.ts:ActiveWatch",
      "features/home/activeWatchViewModel.ts:ActiveWatchRowPresentation",
    ]);
  });

  it("retains the ActiveWatchList WatchStatus compatibility export", () => {
    const activeWatchList = readFileSync(
      path.join(SOURCE_DIRECTORY, "features/home/ActiveWatchList.tsx"),
      "utf8",
    );

    expect(activeWatchList).toContain(
      'export type { WatchStatus } from "../../domain/watch";',
    );
  });

  it("keeps the Home payment-required ViewModel in one feature owner", () => {
    const declarationPattern = /\bexport\s+interface\s+(PaymentRequiredViewModel)\b/g;
    const declarations = sourceFiles(SOURCE_DIRECTORY).flatMap((filePath) => (
      [...readFileSync(filePath, "utf8").matchAll(declarationPattern)].map((match) => (
        `${sourcePath(filePath)}:${match[1]}`
      ))
    ));
    const paymentRequiredSection = readFileSync(
      path.join(SOURCE_DIRECTORY, "features/home/PaymentRequiredSection.tsx"),
      "utf8",
    );
    const homePage = readFileSync(
      path.join(SOURCE_DIRECTORY, "features/home/HomePage.tsx"),
      "utf8",
    );

    expect(declarations).toEqual([
      "features/home/paymentRequiredViewModel.ts:PaymentRequiredViewModel",
    ]);
    expect(paymentRequiredSection).not.toMatch(/payment_deadline|official_booking_url/);
    expect(homePage).not.toMatch(/payment_deadline|official_booking_url/);
    expect(paymentRequiredSection).toMatch(
      /LegacyPaymentRequiredWatch\s+as\s+PaymentRequiredWatch/,
    );
    expect(homePage).toMatch(/\bexport interface HomeCompatibilityProps\b/);
    expect(edges.map(edgeName)).toContain(
      "features/home/paymentRequiredViewModel.ts -> api/watchProjection",
    );
  });

  it("keeps the Reservations ViewModel in one feature owner", () => {
    const declarationPattern = /\bexport\s+interface\s+(ReservationWatchViewModel)\b/g;
    const declarations = sourceFiles(SOURCE_DIRECTORY).flatMap((filePath) => (
      [...readFileSync(filePath, "utf8").matchAll(declarationPattern)].map((match) => (
        `${sourcePath(filePath)}:${match[1]}`
      ))
    ));
    const reservationList = readFileSync(
      path.join(SOURCE_DIRECTORY, "features/reservations/ReservationList.tsx"),
      "utf8",
    );
    const reservationSummary = readFileSync(
      path.join(SOURCE_DIRECTORY, "features/reservations/ReservationSummary.tsx"),
      "utf8",
    );
    const reservationsPage = readFileSync(
      path.join(SOURCE_DIRECTORY, "features/reservations/ReservationsPage.tsx"),
      "utf8",
    );

    expect(declarations).toEqual([
      "features/reservations/reservationViewModel.ts:ReservationWatchViewModel",
    ]);
    expect(reservationList).not.toMatch(/payment_deadline|official_booking_url/);
    expect(reservationSummary).not.toMatch(/payment_deadline|official_booking_url/);
    expect(reservationsPage).not.toMatch(/payment_deadline|official_booking_url/);
    expect(reservationList).toMatch(
      /LegacyReservationListWatch\s+as\s+ReservationListWatch/,
    );
    expect(edges.map(edgeName)).toContain(
      "features/reservations/reservationViewModel.ts -> api/watchProjection",
    );
  });

  it("keeps the app watch lifecycle snapshot in one typed feature owner", () => {
    const declarationPattern = /\bexport\s+interface\s+(WatchLifecycleSnapshot)\b/g;
    const declarations = sourceFiles(SOURCE_DIRECTORY).flatMap((filePath) => (
      [...readFileSync(filePath, "utf8").matchAll(declarationPattern)].map((match) => (
        `${sourcePath(filePath)}:${match[1]}`
      ))
    ));
    const watchSnapshots = readFileSync(
      path.join(SOURCE_DIRECTORY, "features/app/watchSnapshots.ts"),
      "utf8",
    );
    const liveReservationNotice = readFileSync(
      path.join(SOURCE_DIRECTORY, "features/app/liveReservationNotice.ts"),
      "utf8",
    );

    expect(declarations).toEqual([
      "features/app/watchLifecycleSnapshot.ts:WatchLifecycleSnapshot",
    ]);
    expect(watchSnapshots).not.toMatch(/watch\.(?:payment_deadline|updated_at)/);
    expect(watchSnapshots).not.toMatch(
      /latestReservationAttempt\?:\s*unknown|reservationCandidateContexts\?:\s*unknown|seatFoundObservation\?:\s*unknown/,
    );
    expect(watchSnapshots).toContain(
      "export type { LegacyWatchSnapshot as WatchSnapshot }",
    );
    expect(liveReservationNotice).not.toMatch(/isRecord\(watch\./);
    expect(liveReservationNotice).not.toMatch(/eventInstant\(attempt\?/);
    expect(edges.map(edgeName)).toContain(
      "features/app/watchLifecycleSnapshot.ts -> api/watchProjection",
    );
  });

  it("keeps watch DTO parsing upstream of projection and CRUD", () => {
    const watchEdges = edges
      .filter(({ importer }) => [
        "api/watchReadDto.ts",
        "api/watchProjection.ts",
        "api/watches.ts",
      ].includes(importer))
      .map(edgeName);

    expect(watchEdges).toContain("api/watchProjection.ts -> api/watchReadDto");
    expect(watchEdges).toContain("api/watches.ts -> api/watchProjection");
    expect(watchEdges).not.toContain("api/watchReadDto.ts -> api/watchProjection");
    expect(watchEdges).not.toContain("api/watchProjection.ts -> api/watches");
    expect(watchEdges).not.toContain("api/watchProjection.ts -> api/client");
  });

  it("does not restore the deleted legacy API barrel", () => {
    expect(existsSync(path.join(SOURCE_DIRECTORY, "api.js"))).toBe(false);
  });

  it("keeps api independent from features", () => {
    assertRatchet(
      "api-must-not-import-features",
      edges.filter(({ importer, imported }) => (
        importer.startsWith("api/") && imported.startsWith("features/")
      )),
    );
  });

  it("keeps normalized seat domain consumers independent from the API mapper", () => {
    expect(
      edges
        .filter(({ importer, imported }) => (
          importer.startsWith("features/") && imported === "api/seatClasses"
        ))
        .map(edgeName)
        .sort(),
    ).toEqual([]);
  });

  it("keeps domain and shared/lib independent from React, network, and features", () => {
    const boundarySources = edges.filter(({ importer }) => (
      importer.startsWith("domain/") || importer.startsWith("shared/lib/")
    ));
    const violations = boundarySources.filter((edge) => (
      isReactImport(edge.specifier)
      || isNetworkImport(edge)
      || edge.imported.startsWith("features/")
    ));

    expect(violations.map(edgeName).sort()).toEqual([]);
  });

  it("keeps shared modules independent from feature internals", () => {
    assertRatchet(
      "shared-must-not-import-features",
      edges.filter(({ importer, imported }) => (
        importer.startsWith("shared/") && imported.startsWith("features/")
      )),
    );
  });

  it("keeps features independent from app composition", () => {
    assertRatchet(
      "features-must-not-import-app",
      edges.filter(({ importer, imported }) => (
        importer.startsWith("features/") && imported.startsWith("app/")
      )),
    );
  });

  it("does not add imports between feature internals", () => {
    assertRatchet(
      "features-must-not-import-other-features",
      edges.filter((edge) => {
        const importerFeature = featureName(edge.importer);
        const importedFeature = featureName(edge.imported);
        return importerFeature !== null
          && importedFeature !== null
          && importerFeature !== importedFeature;
      }),
    );
  });
});
