// Vitest setup for the jsdom component layer. Adds jest-dom matchers (toBeVisible,
// toHaveTextContent, …) and stubs the handful of browser APIs jsdom does not implement that our
// components touch on mount, so a render never crashes on an unimplemented API instead of failing
// on the behavior under test.
import "@testing-library/jest-dom/vitest";
import { afterEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";

// Unmount React trees between tests so each test starts from a clean DOM.
afterEach(() => cleanup());

// matchMedia — used by reduced-motion / responsive checks. jsdom omits it.
if (!window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }) as unknown as MediaQueryList;
}

// Observers jsdom lacks — components that watch element size/visibility expect them to exist.
class _NoopObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
  takeRecords() {
    return [];
  }
}
vi.stubGlobal("ResizeObserver", _NoopObserver);
vi.stubGlobal("IntersectionObserver", _NoopObserver);

// scrollTo / scrollIntoView are no-ops in jsdom; define them so calls don't throw.
window.scrollTo = window.scrollTo || (() => {});
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {};
}
