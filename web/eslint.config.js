// ESLint flat config — the STATIC layer of the testing trophy (foundation). It catches bug classes
// `tsc` can't: React hook misuse (rules-of-hooks), accessibility regressions, dead code. The ruleset
// is correctness-first and pragmatic for an existing codebase — real bugs are errors, style is a
// warning — and it tightens over time. CI runs `npm run lint` and fails on ERRORS only.
import tseslint from "typescript-eslint";
import reactHooks from "eslint-plugin-react-hooks";
import jsxA11y from "eslint-plugin-jsx-a11y";
import globals from "globals";

export default tseslint.config(
  {
    ignores: [
      "dist/**",
      "dist-*/**",
      "node_modules/**",
      "coverage/**",
      "playwright-report/**",
      "test-results/**",
      "**/*.snap",
      "vite.config.ts",
      "vitest.config.ts",
      "playwright.config.ts",
    ],
  },
  ...tseslint.configs.recommended,
  {
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      globals: { ...globals.browser, ...globals.node },
    },
    plugins: { "react-hooks": reactHooks, "jsx-a11y": jsxA11y },
    rules: {
      // --- correctness (ERROR): the bugs tsc misses ---
      "react-hooks/rules-of-hooks": "error",
      "no-debugger": "error",
      "no-cond-assign": ["error", "except-parens"],
      "no-unsafe-negation": "error",

      // --- advisory (WARN): real signal, but not worth blocking an existing codebase on day one ---
      "react-hooks/exhaustive-deps": "warn",
      "prefer-const": "warn",
      // The converted prototype uses `cond && sideEffect()` short-circuits and ternary statements
      // deliberately; flag them as advisory, not blocking.
      "@typescript-eslint/no-unused-expressions": [
        "warn",
        { allowShortCircuit: true, allowTernary: true },
      ],
      "jsx-a11y/alt-text": "warn",
      "jsx-a11y/anchor-has-content": "warn",
      "jsx-a11y/aria-props": "warn",
      "jsx-a11y/aria-role": "warn",
      "jsx-a11y/role-has-required-aria-props": "warn",
      "@typescript-eslint/no-unused-vars": [
        "warn",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_", caughtErrors: "none" },
      ],

      // --- intentional patterns in the converted prototype (OFF) ---
      "@typescript-eslint/no-explicit-any": "off",
      "@typescript-eslint/no-empty-function": "off",
      "@typescript-eslint/no-non-null-assertion": "off",
      "@typescript-eslint/ban-ts-comment": "off",
      "@typescript-eslint/no-this-alias": "off",
      "no-empty": ["warn", { allowEmptyCatch: true }],
    },
  },
  {
    // src/screens/** is the converted marketing/demo PROTOTYPE — it catalogues many demo
    // components that aren't all rendered in real mode (1600+ intentional unused-var hits). Silence
    // unused-vars there so the lint signal stays ACTIONABLE on the real wired app (src/api,
    // src/auth, src/signup, src/dashboard). The real app keeps the full ruleset.
    files: ["src/screens/**/*.{ts,tsx}"],
    rules: { "@typescript-eslint/no-unused-vars": "off" },
  },
  {
    // The auth core is plain JS (no TS project); lint it as a browser script.
    files: ["src/auth/**/*.js", "test/**/*.mjs"],
    languageOptions: { globals: { ...globals.browser, ...globals.node } },
    rules: { "@typescript-eslint/no-unused-vars": "off" },
  },
);
