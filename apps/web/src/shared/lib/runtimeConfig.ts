export function resolveDemoMode(dev: boolean, configuredValue: unknown): boolean {
  return dev && configuredValue !== "false";
}

export function resolveDemoCaptureReservationLifecycle(
  dev: boolean,
  demo: boolean,
  configuredScenario: unknown,
): boolean {
  return dev && demo && configuredScenario === "reservation-lifecycle";
}

export const DEMO_MODE = resolveDemoMode(
  import.meta.env.DEV,
  import.meta.env.VITE_DEMO_MODE,
);

export const DEMO_CAPTURE_RESERVATION_LIFECYCLE = resolveDemoCaptureReservationLifecycle(
  import.meta.env.DEV,
  DEMO_MODE,
  import.meta.env.VITE_DEMO_CAPTURE_SCENARIO,
);
