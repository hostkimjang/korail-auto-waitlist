#!/usr/bin/env node
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { ESLint } from "eslint";

export const LINT_TARGETS = ["src", "tests", "e2e", "scripts", "worker"];

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const projectDirectory = path.resolve(scriptDirectory, "..");
const baselinePath = path.join(projectDirectory, "eslint-warning-baseline.json");

function slashPath(value) {
  return value.split(path.sep).join("/");
}

function fingerprintKey(fingerprint) {
  return [
    fingerprint.file,
    fingerprint.rule,
    fingerprint.line,
    fingerprint.column,
    fingerprint.lineHash,
  ].join("|");
}

function countedFingerprints(fingerprints) {
  const counts = new Map();
  for (const fingerprint of fingerprints) {
    const key = fingerprintKey(fingerprint);
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return counts;
}

export function compareWarningBaseline(actual, expected) {
  const actualCounts = countedFingerprints(actual);
  const expectedCounts = countedFingerprints(expected);
  const unexpected = [];
  const stale = [];

  for (const [key, count] of actualCounts) {
    const surplus = count - (expectedCounts.get(key) ?? 0);
    if (surplus > 0) unexpected.push(...Array(surplus).fill(key));
  }
  for (const [key, count] of expectedCounts) {
    const surplus = count - (actualCounts.get(key) ?? 0);
    if (surplus > 0) stale.push(...Array(surplus).fill(key));
  }

  return { unexpected: unexpected.sort(), stale: stale.sort() };
}

export function warningFingerprints(results, rootDirectory = projectDirectory) {
  const fingerprints = [];
  for (const result of results) {
    const relativePath = slashPath(path.relative(rootDirectory, result.filePath));
    const sourceLines = (result.source ?? "").split(/\r?\n/);
    for (const message of result.messages) {
      if (message.severity !== 1) continue;
      const sourceLine = message.line === undefined ? "" : (sourceLines[message.line - 1] ?? "");
      fingerprints.push({
        file: relativePath,
        rule: message.ruleId ?? "unknown-rule",
        line: message.line ?? 0,
        column: message.column ?? 0,
        lineHash: createHash("sha256").update(sourceLine).digest("hex"),
      });
    }
  }
  return fingerprints;
}

function validateBaseline(value) {
  if (!Array.isArray(value)) throw new Error("ESLint warning baseline must be an array.");
  for (const [index, entry] of value.entries()) {
    if (
      typeof entry !== "object"
      || entry === null
      || typeof entry.file !== "string"
      || path.isAbsolute(entry.file)
      || entry.file.split("/").includes("..")
      || typeof entry.rule !== "string"
      || !Number.isInteger(entry.line)
      || !Number.isInteger(entry.column)
      || !/^[0-9a-f]{64}$/.test(entry.lineHash)
    ) {
      throw new Error(`Invalid ESLint warning baseline entry at index ${index}.`);
    }
  }
  return value;
}

function printLintErrors(results) {
  for (const result of results) {
    const relativePath = slashPath(path.relative(projectDirectory, result.filePath));
    for (const message of result.messages.filter((item) => item.severity === 2)) {
      const summary = message.message.split("\n", 1)[0];
      console.error(`${relativePath}:${message.line ?? 0}:${message.column ?? 0} ${message.ruleId ?? "parse-error"} ${summary}`);
    }
  }
}

async function main() {
  const baseline = validateBaseline(JSON.parse(await readFile(baselinePath, "utf8")));
  const eslint = new ESLint({ cwd: projectDirectory });
  const results = await eslint.lintFiles(LINT_TARGETS);
  const errorCount = results.reduce((total, result) => total + result.errorCount, 0);
  if (errorCount > 0) {
    printLintErrors(results);
    console.error(`ESLint error ${errorCount}개를 수정해야 합니다.`);
    return 1;
  }

  const actual = warningFingerprints(results);
  const { unexpected, stale } = compareWarningBaseline(actual, baseline);
  if (unexpected.length > 0 || stale.length > 0) {
    if (unexpected.length > 0) {
      console.error("새 ESLint warning은 baseline에 추가하지 말고 원인을 수정하세요:");
      for (const fingerprint of unexpected) console.error(`  + ${fingerprint}`);
    }
    if (stale.length > 0) {
      console.error("해결됐거나 위치가 달라진 stale ESLint warning baseline을 제거하세요:");
      for (const fingerprint of stale) console.error(`  - ${fingerprint}`);
    }
    return 1;
  }

  console.log(`ESLint ratchet 통과: 오류 0개, 고정된 legacy warning ${actual.length}개.`);
  return 0;
}

const invokedPath = process.argv[1] ? path.resolve(process.argv[1]) : "";
if (invokedPath === fileURLToPath(import.meta.url)) {
  main().then(
    (exitCode) => { process.exitCode = exitCode; },
    (error) => {
      console.error(error instanceof Error ? error.message : String(error));
      process.exitCode = 2;
    },
  );
}
