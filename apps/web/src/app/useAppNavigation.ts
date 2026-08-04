import { useCallback, useState } from "react";

import type { SettingsSection } from "../features/settings/SettingsPage";

export type AppView = "home" | "new" | "reservations" | "settings";

export type AppNavigate = (
  view: AppView,
  settingsSection?: SettingsSection,
) => void;

export interface AppNavigationController {
  activeView: AppView;
  settingsInitialSection: SettingsSection;
  settingsActiveSection: SettingsSection;
  navigate: AppNavigate;
  onSettingsSectionChange: (section: SettingsSection) => void;
}

export function useAppNavigation(): AppNavigationController {
  const [activeView, setActiveView] = useState<AppView>("home");
  const [settingsInitialSection, setSettingsInitialSection] = useState<SettingsSection>(
    "notifications",
  );
  const [settingsActiveSection, setSettingsActiveSection] = useState<SettingsSection>(
    "notifications",
  );

  const navigate = useCallback<AppNavigate>((view, settingsSection) => {
    setActiveView(view);
    if (view === "settings") {
      const nextSettingsSection = settingsSection ?? "notifications";
      setSettingsInitialSection(nextSettingsSection);
      setSettingsActiveSection(nextSettingsSection);
    }
    window.scrollTo({ top: 0, behavior: "smooth" });
  }, []);

  const onSettingsSectionChange = useCallback((section: SettingsSection): void => {
    setSettingsActiveSection(section);
  }, []);

  return {
    activeView,
    settingsInitialSection,
    settingsActiveSection,
    navigate,
    onSettingsSectionChange,
  };
}
