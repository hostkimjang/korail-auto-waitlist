import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const styles = readFileSync(resolve(process.cwd(), "src/styles.css"), "utf8");

describe("responsive layout CSS contracts", () => {
  it("reflows active watches by actual container width and isolates the policy row", () => {
    expect(styles).toContain("container-name: active-watch-list");
    expect(styles).toContain("@container active-watch-list (max-width: 1080px)");
    expect(styles).toMatch(/\.row-actions\s*\{[\s\S]*?grid-template-areas:[\s\S]*?"policy controls"[\s\S]*?"booking booking"/);
    expect(styles).toMatch(/\.watch-policy-control\s*\{[\s\S]*?grid-template-columns:\s*minmax\(0, 1fr\) 44px/);
    expect(styles).toMatch(/\.watch-policy-control\s*\{[\s\S]*?white-space:\s*normal/);
    expect(styles).toMatch(/\.watch-policy-label\s*\{[\s\S]*?overflow-wrap:\s*anywhere/);
    expect(styles).toMatch(/@container active-watch-list \(max-width: 760px\)[\s\S]*?"state state"[\s\S]*?"actions actions"/);
    expect(styles).toMatch(/@container active-watch-list \(max-width: 520px\)[\s\S]*?"policy"[\s\S]*?"booking"[\s\S]*?"controls"/);
  });

  it("uses one bounded notification surface without the removed second fixed offset", () => {
    expect(styles).toContain(".notification-center");
    expect(styles).toContain("max-height: min(70dvh, 560px)");
    expect(styles).not.toContain(".seat-found-alert");
    expect(styles).not.toContain("+ 184px");
    expect(styles).not.toContain("+ 238px");
  });

  it("keeps the notification switch target at least 44px tall", () => {
    expect(styles).toMatch(/\.switch\s*\{[\s\S]*?min-width:\s*46px[\s\S]*?min-height:\s*44px/);
  });
});
