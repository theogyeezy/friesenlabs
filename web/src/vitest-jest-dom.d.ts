// Makes the @testing-library/jest-dom matchers (toBeVisible, toHaveTextContent, …) visible to
// `tsc` in the component tests. The runtime registration is in test/setup.ts; this file is inside
// `src` (which tsconfig compiles) so the type augmentation of vitest's Assertion is in scope.
import "@testing-library/jest-dom/vitest";
