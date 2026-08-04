export function resolveDemoMode(dev: boolean, configuredValue: unknown): boolean {
  return dev && configuredValue !== "false";
}

export const DEMO_MODE = resolveDemoMode(
  import.meta.env.DEV,
  import.meta.env.VITE_DEMO_MODE,
);
