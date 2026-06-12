// Cloudflare Turnstile seam for the signup form — dependency-free by design.
//
// The backend already reads an `x-captcha-token` request header on POST /signup
// (api/signup_routes.py -> CaptchaVerifier); this module is the matching client
// seam. It is OFF unless the build carries a VITE_TURNSTILE_SITE_KEY:
//
//   * Site key ABSENT (today's prod): TurnstileWidget renders null, no external
//     script is ever loaded, no header is sent — zero behavior change.
//   * Site key PRESENT: the official api.js is lazy-loaded from
//     https://challenges.cloudflare.com ONLY when the widget mounts (i.e. on the
//     signup view), the widget renders, and the issued token is handed to the
//     caller to send as `x-captcha-token` on the signup-start request. A
//     missing/invalid token surfaces as the server's 400 detail through the
//     normal friendlyErrorMessage path.
//
// No npm package: the script tag + window callback IS the integration (matches
// Cloudflare's documented explicit-render flow and keeps the bundle unchanged).

import React from "react";

const { useEffect, useRef } = React;

const TURNSTILE_SRC = "https://challenges.cloudflare.com/turnstile/v0/api.js";
const ONLOAD_CB = "__flTurnstileOnload";

/** The minimal surface of the Turnstile API we use (explicit render mode). */
interface TurnstileApi {
  render(
    el: HTMLElement,
    params: {
      sitekey: string;
      callback: (token: string) => void;
      "expired-callback"?: () => void;
      "error-callback"?: () => void;
    },
  ): string;
  remove(widgetId: string): void;
}

declare global {
  interface Window {
    turnstile?: TurnstileApi;
    [ONLOAD_CB]?: () => void;
  }
}

/** The Turnstile site key, or "" when the seam is disabled (a PUBLIC identifier, not a secret). */
export function turnstileSiteKey(): string {
  const env =
    (import.meta as unknown as { env?: Record<string, string | undefined> }).env ?? {};
  return env.VITE_TURNSTILE_SITE_KEY ?? "";
}

/** True when this build should render the captcha and send the token header. */
export function isTurnstileEnabled(): boolean {
  return turnstileSiteKey() !== "";
}

// One shared loader promise: the script is injected at most once per page.
let loaderPromise: Promise<TurnstileApi> | null = null;

/** Lazily load the official Turnstile script (explicit render mode). Never called
 *  when the site key is absent — the widget component is the only caller. */
export function loadTurnstile(): Promise<TurnstileApi> {
  if (loaderPromise) return loaderPromise;
  loaderPromise = new Promise<TurnstileApi>((resolve, reject) => {
    if (window.turnstile) {
      resolve(window.turnstile);
      return;
    }
    window[ONLOAD_CB] = () => {
      if (window.turnstile) resolve(window.turnstile);
      else reject(new Error("turnstile script loaded without an API"));
    };
    const script = document.createElement("script");
    script.src = `${TURNSTILE_SRC}?render=explicit&onload=${ONLOAD_CB}`;
    script.async = true;
    script.onerror = () => {
      loaderPromise = null; // allow a retry on the next mount
      reject(new Error("turnstile script failed to load"));
    };
    document.head.appendChild(script);
  });
  return loaderPromise;
}

export interface TurnstileWidgetProps {
  /** Receives the issued token; null when it expires or errors (send no header then). */
  onToken: (token: string | null) => void;
}

/**
 * The signup captcha widget. Renders null (and loads nothing) when the env var
 * is absent. With a site key it lazy-loads api.js on mount, renders the widget,
 * and reports tokens upward; the parent attaches the latest token as the
 * `x-captcha-token` header on the signup-start call.
 */
export function TurnstileWidget({ onToken }: TurnstileWidgetProps) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const siteKey = turnstileSiteKey();

  useEffect(() => {
    if (!siteKey || !hostRef.current) return;
    let cancelled = false;
    let widgetId: string | null = null;

    loadTurnstile()
      .then((ts) => {
        if (cancelled || !hostRef.current) return;
        widgetId = ts.render(hostRef.current, {
          sitekey: siteKey,
          callback: (token: string) => onToken(token),
          "expired-callback": () => onToken(null),
          "error-callback": () => onToken(null),
        });
      })
      .catch(() => {
        // Script blocked/failed: leave the token null. The server stays the
        // arbiter — its 400 detail surfaces through friendlyErrorMessage.
        if (!cancelled) onToken(null);
      });

    return () => {
      cancelled = true;
      if (widgetId !== null) {
        try {
          window.turnstile?.remove(widgetId);
        } catch {
          // Best-effort cleanup only.
        }
      }
    };
  }, [siteKey, onToken]);

  if (!siteKey) return null;
  return <div ref={hostRef} data-testid="turnstile-widget" style={{ marginTop: 16 }} />;
}
