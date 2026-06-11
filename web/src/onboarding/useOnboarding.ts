// Shared first-run state hook — one source of truth for the per-tenant
// onboarding_state (GET /onboarding) + the mutations (PUT /onboarding,
// POST /onboarding/load-sample). Every onboarding surface (the first-run
// checklist, the empty-state CTAs) reads through this so they stay consistent.
//
// HONEST DEGRADATION: a brand-new deploy whose API predates the routes (404) or
// an unconfigured data plane must NOT blank the app. We treat a load failure as
// "no first-run state to show" (rollout=true) — the checklist simply does not
// render, and the empty-state CTAs still work locally (the load-sample call is
// what surfaces an error to the user, never the initial GET).

import React from "react";
import { ApiError, defaultClient } from "../api/client";
import type {
  ApiClient,
  LoadSampleResponse,
  OnboardingPutBody,
  OnboardingState,
} from "../api/client";

const { useState, useEffect, useCallback, useRef } = React;

export interface UseOnboarding {
  state: OnboardingState | null;
  /** true while the first GET is in flight (suppresses a flash of the checklist). */
  loading: boolean;
  /** true when the API doesn't serve /onboarding yet (404) — degrade silently. */
  rollout: boolean;
  /** Persist a partial update (a step done / dismiss). Optimistic + reconciled. */
  update: (body: OnboardingPutBody) => Promise<void>;
  /** One-click idempotent demo-fixture load; resolves with the loaded counts. */
  loadSample: () => Promise<LoadSampleResponse>;
  /** Re-fetch from the server (after a load-sample, surfaces refresh). */
  refresh: () => Promise<void>;
}

export function useOnboarding(client?: ApiClient): UseOnboarding {
  const api = client ?? defaultClient();
  const [state, setState] = useState<OnboardingState | null>(null);
  const [loading, setLoading] = useState(true);
  const [rollout, setRollout] = useState(false);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const refresh = useCallback(async () => {
    try {
      const s = await api.getOnboarding();
      if (!alive.current) return;
      setState(s);
      setRollout(false);
    } catch (e) {
      if (!alive.current) return;
      // 404 = the API image predates the routes; anything else = transient.
      // Either way the first-run UI degrades to "nothing to show", never an error wall.
      if (e instanceof ApiError && e.status === 404) setRollout(true);
      setState(null);
    } finally {
      if (alive.current) setLoading(false);
    }
  }, [api]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const update = useCallback(
    async (body: OnboardingPutBody) => {
      const next = await api.putOnboarding(body);
      if (alive.current) setState(next);
    },
    [api],
  );

  const loadSample = useCallback(async (): Promise<LoadSampleResponse> => {
    const res = await api.loadSampleData();
    // The route returns the updated onboarding state; reflect it immediately so
    // the checklist + flags update without a second round-trip.
    if (alive.current && res.onboarding) setState(res.onboarding);
    return res;
  }, [api]);

  return { state, loading, rollout, update, loadSample, refresh };
}

export default useOnboarding;
