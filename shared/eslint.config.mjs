// Canonical ESLint config for all consumer TypeScript repos.
// Per-repo eslint.config.mjs should import and spread this:
//   import sharedConfig from "./.shared/eslint.config.mjs";
//   export default [...sharedConfig, { /* repo-specific overrides */ }];

import { defineConfig } from "eslint/config";
import globals from "globals";
import js from "@eslint/js";
import tseslint from "typescript-eslint";

export default defineConfig([
  { ignores: [".history/**", "dist/**", "build/**", "node_modules/**"] },
  {
    files: ["**/*.{js,mjs,cjs,ts,jsx,tsx}"],
    languageOptions: { globals: globals.browser },
    plugins: { js },
    extends: ["js/recommended"],
  },
  tseslint.configs.recommended,
  {
    rules: {
      "no-empty": "error",
      "@typescript-eslint/no-explicit-any": "off",
      "@typescript-eslint/no-unused-vars": [
        "warn",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
    },
  },
]);
