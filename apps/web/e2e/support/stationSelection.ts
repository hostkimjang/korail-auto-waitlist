import type { Locator, Page } from "@playwright/test";

export type StationLabel = "출발역" | "도착역";

type SelectStationOptions = {
  onDialogReady?: (dialog: Locator) => Promise<void>;
};

export async function selectStation(
  page: Page,
  label: StationLabel,
  name: string,
  options: SelectStationOptions = {},
): Promise<void> {
  const inlineCombobox = page.getByRole("combobox", { name: label });
  if (await inlineCombobox.count()) {
    await inlineCombobox.fill(name);
    await page.getByRole("option", { name: new RegExp(`^${name}`) }).click();
    return;
  }

  const dialog = page.getByRole("dialog", { name: /역 선택$/ });
  if (!(await dialog.isVisible())) {
    const trigger = page.getByRole("button", { name: new RegExp(`^${label}\\s`) });
    await trigger.click();
    await dialog.waitFor({ state: "visible" });
  }

  await options.onDialogReady?.(dialog);
  const dialogCombobox = dialog.getByRole("combobox", { name: `${label} 검색` });
  await dialogCombobox.fill(name);
  await dialog.getByRole("option", { name: new RegExp(`^${name}`) }).click();

  if (label === "도착역") {
    await dialog.waitFor({ state: "hidden" });
  }
}
