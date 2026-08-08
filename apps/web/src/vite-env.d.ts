/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_DEMO_CAPTURE_SCENARIO?: "reservation-lifecycle";
  readonly VITE_DEMO_MODE?: "true" | "false";
  readonly VITE_KORAIL_BOOKING_DEEPLINK_ENABLED?: "true" | "false";
  readonly VITE_KORAIL_BOOKING_VALIDATED_VERSION?: string;
  readonly VITE_KORAIL_TICKET_DEEPLINK_ENABLED?: "true" | "false";
  readonly VITE_KORAIL_TICKET_VALIDATED_VERSION?: string;
  readonly VITE_SRT_MAIN_DEEPLINK_ENABLED?: "true" | "false";
  readonly VITE_SRT_MAIN_VALIDATED_VERSION?: string;
  readonly VITE_SRT_TICKET_DEEPLINK_ENABLED?: "true" | "false";
  readonly VITE_SRT_TICKET_VALIDATED_VERSION?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
