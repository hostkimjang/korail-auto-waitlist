import type { ESLint } from "eslint";

export interface WarningFingerprint {
  file: string;
  rule: string;
  line: number;
  column: number;
  lineHash: string;
}

export const LINT_TARGETS: readonly string[];

export function compareWarningBaseline(
  actual: readonly WarningFingerprint[],
  expected: readonly WarningFingerprint[],
): { unexpected: string[]; stale: string[] };

export function warningFingerprints(
  results: readonly ESLint.LintResult[],
  rootDirectory?: string,
): WarningFingerprint[];
