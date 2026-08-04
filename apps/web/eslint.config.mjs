import js from "@eslint/js";
import babelParser from "@babel/eslint-parser";
import react from "eslint-plugin-react";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";

const codeFiles = [
  "src/**/*.{js,jsx,mjs,ts,tsx}",
  "tests/**/*.{js,jsx,mjs,ts,tsx}",
  "e2e/**/*.{js,jsx,mjs,ts,tsx}",
  "scripts/**/*.{js,jsx,mjs,ts,tsx}",
  "worker/**/*.{js,jsx,mjs,ts,tsx}",
];
const legacyEffectFiles = [
  "src/features/auth/useAuthState.ts",
  "src/features/new-wait/CalendarPicker.tsx",
  "src/features/new-wait/StationCombobox.tsx",
  "src/features/new-wait/StepThreeRefreshControl.tsx",
  "src/features/new-wait/StepThreeTimeRange.tsx",
  "src/features/settings/KorailBrowserPairingPanel.tsx",
  "src/features/settings/TimetableRefreshSettings.tsx",
  "src/features/settings/useOperationsSummary.ts",
  "src/features/settings/useSeatStatusSources.ts",
  "src/hooks/usePaymentDeadlineClock.ts",
];
const legacyDependencyFiles = [
  "src/features/settings/KorailBrowserPairingPanel.tsx",
];

export default [
  {
    ignores: ["dist/**", "node_modules/**", "output/**"],
  },
  {
    files: codeFiles,
    languageOptions: {
      parser: babelParser,
      parserOptions: {
        requireConfigFile: false,
        babelOptions: {
          babelrc: false,
          configFile: false,
          parserOpts: {
            plugins: ["typescript", "jsx"],
          },
        },
      },
    },
    plugins: {
      react,
      "react-hooks": reactHooks,
    },
    rules: {
      ...js.configs.recommended.rules,
      ...reactHooks.configs.flat.recommended.rules,
      "react-hooks/exhaustive-deps": "error",
      "react/jsx-no-undef": "error",
      "react/jsx-uses-vars": "error",
    },
  },
  {
    files: ["src/**/*.{js,jsx,mjs,ts,tsx}"],
    languageOptions: {
      globals: globals.browser,
    },
    rules: {
      "no-restricted-globals": [
        "error",
        { name: "Buffer", message: "Browser source must not depend on Node.js globals." },
        { name: "process", message: "Browser source must not depend on Node.js globals." },
        { name: "require", message: "Browser source must use ESM browser imports." },
        { name: "module", message: "Browser source must not depend on CommonJS globals." },
        { name: "exports", message: "Browser source must not depend on CommonJS globals." },
      ],
      "no-restricted-imports": ["error", { patterns: ["node:*"] }],
    },
  },
  {
    files: ["tests/**/*.{js,jsx,mjs,ts,tsx}"],
    languageOptions: {
      globals: {
        ...globals.browser,
        ...globals.node,
        ...globals.vitest,
      },
    },
  },
  {
    files: ["e2e/**/*.{js,jsx,mjs,ts,tsx}"],
    languageOptions: {
      globals: {
        ...globals.browser,
        ...globals.node,
      },
    },
  },
  {
    files: ["scripts/**/*.{js,jsx,mjs,ts,tsx}"],
    languageOptions: {
      globals: globals.node,
    },
  },
  {
    files: ["worker/**/*.{js,jsx,mjs,ts,tsx}"],
    languageOptions: {
      globals: globals.worker,
    },
    rules: {
      "no-restricted-globals": [
        "error",
        { name: "Buffer", message: "Worker source must use Web Platform APIs." },
        { name: "process", message: "Worker source must not depend on Node.js globals." },
        { name: "require", message: "Worker source must use ESM and Web Platform APIs." },
      ],
      "no-restricted-imports": ["error", { patterns: ["node:*"] }],
    },
  },
  {
    files: ["**/*.{ts,tsx}"],
    rules: {
      "no-undef": "off",
      "no-unused-vars": "off",
    },
  },
  {
    // Existing effect-state debt stays visible while new files fail these rules immediately.
    files: legacyEffectFiles,
    rules: {
      "react-hooks/refs": "warn",
      "react-hooks/set-state-in-effect": "warn",
    },
  },
  {
    // Missing dependencies remain visible in the two legacy orchestrators while new files fail.
    files: legacyDependencyFiles,
    rules: {
      "react-hooks/exhaustive-deps": "warn",
    },
  },
];
