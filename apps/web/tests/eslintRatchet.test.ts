import { describe, expect, it } from "vitest";
import { ESLint } from "eslint";

import {
  LINT_TARGETS,
  compareWarningBaseline,
  warningFingerprints,
} from "../scripts/check-eslint-ratchet.mjs";

type ViolationCase = readonly [name: string, code: string, filePath: string, ruleId: string];

const eslint = new ESLint();
const violationCases: ReadonlyArray<ViolationCase> = [
  ["undefined JSX", "export function Broken() { return <MissingWidget />; }", "src/Broken.jsx", "react/jsx-no-undef"],
  ["test mjs", "const unused = 1;", "tests/broken.test.mjs", "no-unused-vars"],
  ["E2E TSX", "export function Broken() { return <MissingWidget />; }", "e2e/broken.spec.tsx", "react/jsx-no-undef"],
  ["browser Node global", "export const broken = Buffer.from('x');", "src/broken.ts", "no-restricted-globals"],
  ["Node script", "export const broken = MissingScriptValue;", "scripts/broken.mjs", "no-undef"],
  ["Worker", "export default MissingWorkerBinding;", "worker/broken.js", "no-undef"],
];

async function lintProbe(code: string, filePath: string): Promise<ESLint.LintResult> {
  const [result] = await eslint.lintText(code, { filePath });
  if (result === undefined) {
    throw new Error(`ESLint returned no result for ${filePath}`);
  }
  return result;
}

describe("ESLint warning ratchet", () => {
  it("covers every maintained web runtime boundary", () => {
    expect(LINT_TARGETS).toEqual(["src", "tests", "e2e", "scripts", "worker"]);
  });

  it("fails when a legacy file gains a new warning or a baseline becomes stale", async () => {
    const result = await lintProbe(`
      import { useEffect, useState } from "react";
      export function App() {
        const [value, setValue] = useState(0);
        useEffect(() => { setValue(1); }, []);
        return value;
      }
    `, "src/features/auth/useAuthState.ts");
    const actual = warningFingerprints([result]);

    expect(actual).toHaveLength(1);
    expect(compareWarningBaseline(actual, []).unexpected).toHaveLength(1);
    expect(compareWarningBaseline([], actual).stale).toHaveLength(1);
    expect(compareWarningBaseline([...actual, ...actual], actual).unexpected).toHaveLength(1);
  }, 30_000);

  it.each(violationCases)("blocks %s violations in the configured scope", async (_name, code, filePath, ruleId) => {
    const result = await lintProbe(code, filePath);

    expect(result.messages).toEqual(expect.arrayContaining([
      expect.objectContaining({ ruleId, severity: 2 }),
    ]));
  }, 30_000);
});
