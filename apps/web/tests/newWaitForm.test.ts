import { describe, expect, it } from "vitest";

import type { ProviderAccount, RailProvider } from "../src/api/providerAccounts";
import {
  createInitialNewWaitForm,
  nextWeekdayDate,
  seoulDateInput,
  selectNewWaitWeekday,
  setNewWaitTravelDate,
  swapNewWaitStations,
  toggleNewWaitProvider,
  type NewWaitForm,
} from "../src/features/new-wait/newWaitForm";

function providerAccount(
  provider: RailProvider,
  authenticated: boolean,
): ProviderAccount {
  return {
    provider,
    configured: authenticated,
    enabled: authenticated,
    loginMethod: authenticated ? "membership_number" : null,
    maskedLoginId: authenticated ? "12***34" : null,
    credentialVersion: authenticated ? 1 : 0,
    lastAuthStatus: authenticated ? "authenticated" : "not_checked",
    lastAuthenticatedAt: null,
    updatedAt: null,
  };
}

const authenticatedAccounts = [
  providerAccount("KORAIL", true),
  providerAccount("SRT", true),
];

function initialForm(overrides: Partial<NewWaitForm> = {}): NewWaitForm {
  return {
    ...createInitialNewWaitForm({
      demo: false,
      providerAccounts: [],
      demoOriginNodeId: null,
      demoDestinationNodeId: null,
      now: new Date("2026-08-04T03:00:00.000Z"),
    }),
    ...overrides,
  };
}

describe("new wait form model", () => {
  it.each([
    ["2026-08-04T14:59:00.000Z", 0, "2026-08-04"],
    ["2026-08-04T15:01:00.000Z", 0, "2026-08-05"],
    ["2026-08-04T15:01:00.000Z", 1, "2026-08-06"],
  ])("formats %s with offset %i at the Asia/Seoul boundary", (now, offset, expected) => {
    expect(seoulDateInput(new Date(now), offset)).toBe(expected);
  });

  it.each([
    {
      name: "live mode",
      demo: false,
      accounts: authenticatedAccounts,
      expected: {
        origin: "",
        origin_node_id: null,
        destination: "",
        destination_node_id: null,
        reservationPolicy: "reserve_once_before_payment",
      },
    },
    {
      name: "demo mode",
      demo: true,
      accounts: authenticatedAccounts,
      expected: {
        origin: "서울",
        origin_node_id: "demo:서울",
        destination: "부산",
        destination_node_id: "demo:부산",
        reservationPolicy: "notify_only",
      },
    },
  ])("creates the existing $name defaults", ({ demo, accounts, expected }) => {
    const form = createInitialNewWaitForm({
      demo,
      providerAccounts: accounts,
      demoOriginNodeId: "demo:서울",
      demoDestinationNodeId: "demo:부산",
      now: new Date("2026-08-04T03:00:00.000Z"),
    });

    expect(form).toMatchObject({
      provider: "KORAIL",
      providers: ["KORAIL"],
      date: "2026-08-05",
      time: "12:00",
      timeEnd: "18:00",
      selectedWeekdays: ["수"],
      passengers: "1",
      seat: "일반실",
      channels: ["web_push", "telegram"],
      ...expected,
    });
  });

  it("swaps station labels and catalog identities together", () => {
    const form = initialForm({
      origin: "서울",
      origin_node_id: "0001",
      destination: "부산",
      destination_node_id: "0020",
    });

    expect(swapNewWaitStations(form)).toMatchObject({
      origin: "부산",
      origin_node_id: "0020",
      destination: "서울",
      destination_node_id: "0001",
    });
  });

  it.each([
    ["2026-08-05", "수"],
    ["2026-08-09", "일"],
  ] as const)("synchronizes date %s with weekday %s", (date, weekday) => {
    expect(setNewWaitTravelDate(initialForm(), date)).toMatchObject({
      date,
      selectedWeekdays: [weekday],
    });
  });

  it.each([
    ["2026-08-01", "화", "2026-08-04"],
    ["2026-08-01", "월", "2026-08-10"],
    ["2026-08-06", "화", "2026-08-11"],
  ] as const)(
    "moves base date %s to the nearest %s without going before today",
    (baseDate, weekday, expected) => {
      expect(nextWeekdayDate(baseDate, weekday, "2026-08-04")).toBe(expected);
      expect(selectNewWaitWeekday(
        initialForm({ date: baseDate }),
        weekday,
        "2026-08-04",
      )).toMatchObject({ date: expected, selectedWeekdays: [weekday] });
    },
  );

  it.each([
    {
      name: "adds a provider and adopts the authenticated default",
      form: initialForm(),
      provider: "SRT" as const,
      accounts: authenticatedAccounts,
      manuallySelected: false,
      expectedProviders: ["KORAIL", "SRT"],
      expectedProvider: "KORAIL",
      expectedPolicy: "reserve_once_before_payment",
    },
    {
      name: "removes the primary provider and promotes the remaining provider",
      form: initialForm({ providers: ["KORAIL", "SRT"], reservationPolicy: "notify_only" }),
      provider: "KORAIL" as const,
      accounts: [],
      manuallySelected: false,
      expectedProviders: ["SRT"],
      expectedProvider: "SRT",
      expectedPolicy: "notify_only",
    },
    {
      name: "allows clearing the final provider and clears the legacy primary value",
      form: initialForm(),
      provider: "KORAIL" as const,
      accounts: authenticatedAccounts,
      manuallySelected: false,
      expectedProviders: [],
      expectedProvider: "",
      expectedPolicy: "notify_only",
    },
    {
      name: "preserves a manual notify-only choice",
      form: initialForm({ reservationPolicy: "notify_only" }),
      provider: "SRT" as const,
      accounts: authenticatedAccounts,
      manuallySelected: true,
      expectedProviders: ["KORAIL", "SRT"],
      expectedProvider: "KORAIL",
      expectedPolicy: "notify_only",
    },
    {
      name: "fails a manual reservation choice closed when an account is unavailable",
      form: initialForm({ reservationPolicy: "reserve_once_before_payment" }),
      provider: "SRT" as const,
      accounts: [providerAccount("KORAIL", true), providerAccount("SRT", false)],
      manuallySelected: true,
      expectedProviders: ["KORAIL", "SRT"],
      expectedProvider: "KORAIL",
      expectedPolicy: "notify_only",
    },
  ])("$name", ({
    form,
    provider,
    accounts,
    manuallySelected,
    expectedProviders,
    expectedProvider,
    expectedPolicy,
  }) => {
    expect(toggleNewWaitProvider(form, provider, {
      demo: false,
      providerAccounts: accounts,
      reservationPolicyManuallySelected: manuallySelected,
    })).toMatchObject({
      providers: expectedProviders,
      provider: expectedProvider,
      reservationPolicy: expectedPolicy,
    });
  });
});
