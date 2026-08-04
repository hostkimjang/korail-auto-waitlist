/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_DEMO_MODE?: "true" | "false";
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
