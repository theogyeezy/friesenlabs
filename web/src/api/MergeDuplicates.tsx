// Merge duplicates panel (CRM-depth #16), wired to the control-plane API via
// ApiClient. Surfaces clusters of likely-duplicate contacts/companies from GET
// /{entity}/duplicates and merges a loser INTO a chosen winner via POST
// /{entity}/merge (re-points the loser's deals/activities/tasks, backfills the
// winner's empty fields, soft-archives the loser — reversible, never a delete).
// Everything is honest: clusters come straight from the API, the merge is a
// direct user write (never Greenlight, nothing leaves the system), and a 404
// from the route renders a calm "rolling out" state, not an error wall.

import React from "react";
import {
  ApiClient,
  ApiError,
  defaultClient,
  friendlyErrorMessage,
  type DuplicateCluster,
} from "./client";
import { Spinner } from "./Spinner";

const { useState, useEffect, useCallback } = React;

const card: React.CSSProperties = {
  border: "1px solid var(--line, #e3ddd3)",
  background: "var(--surface, #fff)",
  borderRadius: 14,
  padding: "16px 18px",
  marginBottom: 14,
};

const primaryBtn: React.CSSProperties = {
  padding: "7px 14px",
  borderRadius: 10,
  border: "none",
  background: "var(--accent, #2a2622)",
  color: "#fff",
  fontSize: 13,
  fontWeight: 650,
  cursor: "pointer",
};

const ghostBtn: React.CSSProperties = {
  padding: "7px 14px",
  borderRadius: 10,
  border: "1px solid var(--line, #e3ddd3)",
  background: "transparent",
  color: "var(--ink, #2a2622)",
  fontSize: 13,
  fontWeight: 650,
  cursor: "pointer",
};

type Entity = "contacts" | "companies";

function memberLabel(m: Record<string, unknown>): string {
  const name = (m.name as string) || "";
  const detail = (m.email as string) || (m.domain as string) || (m.phone as string) || "";
  return detail ? `${name || "Unnamed"} · ${detail}` : name || "Unnamed";
}

function reasonLabel(reason: string): string {
  if (reason === "email") return "same email";
  if (reason === "domain") return "same domain";
  if (reason === "name") return "same name";
  return reason;
}

export interface MergeDuplicatesProps {
  client?: ApiClient;
  /** Called after a successful merge so the parent directory can refresh. */
  onMerged?: () => void;
  /** Close the panel (the parent owns the open/closed state). */
  onClose?: () => void;
}

export function MergeDuplicates({ client, onMerged, onClose }: MergeDuplicatesProps) {
  const api = client ?? defaultClient();
  const [entity, setEntity] = useState<Entity>("contacts");
  const [clusters, setClusters] = useState<DuplicateCluster[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [rollout, setRollout] = useState(false);
  // Chosen winner id per cluster key (defaults to the first/oldest member).
  const [winners, setWinners] = useState<Record<string, string>>({});
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [merged, setMerged] = useState<string | null>(null); // last merge summary

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    setRollout(false);
    // NOTE: the merge success message is deliberately NOT cleared here — a merge
    // triggers a reload, and the user should still see "Merged N…" afterwards. It's
    // cleared on an entity switch instead (selectEntity).
    try {
      const res = await api.findDuplicates(entity);
      setClusters(res.clusters);
      // Default each cluster's winner to its first member.
      const defaults: Record<string, string> = {};
      for (const c of res.clusters) {
        if (c.members[0]) defaults[c.key] = c.members[0].id;
      }
      setWinners(defaults);
    } catch (e) {
      setClusters([]);
      if (e instanceof ApiError && e.status === 404) {
        setRollout(true);
      } else {
        setError(friendlyErrorMessage(e, "Couldn't scan for duplicates. Please try again."));
      }
    } finally {
      setLoading(false);
    }
  }, [api, entity]);

  useEffect(() => {
    void load();
  }, [load]);

  const doMerge = useCallback(
    async (cluster: DuplicateCluster) => {
      const winnerId = winners[cluster.key] ?? cluster.members[0]?.id;
      if (!winnerId) return;
      const losers = cluster.members.filter((m) => m.id !== winnerId);
      if (losers.length === 0) return;
      setBusyKey(cluster.key);
      setError(null);
      try {
        // Merge each loser into the winner, one at a time (each is its own atomic txn).
        for (const loser of losers) {
          await api.merge(entity, { winner_id: winnerId, loser_id: loser.id });
        }
        setMerged(`Merged ${losers.length} ${entity === "contacts" ? "contact" : "company"}${losers.length > 1 ? "s" : ""} into the kept record.`);
        onMerged?.();
        await load();
      } catch (e) {
        setError(friendlyErrorMessage(e, "Couldn't merge those records. Please try again."));
      } finally {
        setBusyKey(null);
      }
    },
    [api, entity, winners, onMerged, load],
  );

  return (
    <div data-testid="merge-panel" style={{ maxWidth: 640 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
        <h3 style={{ margin: 0, fontSize: 17, fontWeight: 700, color: "var(--ink, #2a2622)" }}>
          Find &amp; merge duplicates
        </h3>
        {onClose ? (
          <button data-testid="merge-close" onClick={onClose} style={ghostBtn}>Close</button>
        ) : null}
      </div>

      {/* Entity toggle */}
      <div style={{ display: "flex", gap: 6, marginBottom: 14 }}>
        {(["contacts", "companies"] as Entity[]).map((e) => (
          <button
            key={e}
            data-testid={`merge-entity-${e}`}
            onClick={() => { setMerged(null); setEntity(e); }}
            aria-pressed={entity === e}
            style={{
              ...ghostBtn,
              background: entity === e ? "var(--accent, #2a2622)" : "transparent",
              color: entity === e ? "#fff" : "var(--ink, #2a2622)",
              borderColor: entity === e ? "var(--accent, #2a2622)" : "var(--line, #e3ddd3)",
            }}
          >
            {e === "contacts" ? "Contacts" : "Companies"}
          </button>
        ))}
      </div>

      {merged ? (
        <div data-testid="merge-done" style={{ ...card, color: "var(--ink, #2a2622)", fontSize: 13 }}>
          {merged}
        </div>
      ) : null}

      {loading ? (
        <div data-testid="merge-loading" style={{ ...card, textAlign: "center" }}>
          <Spinner /> <span style={{ marginLeft: 8, color: "var(--ink-3, #8a8278)" }}>Scanning for duplicates…</span>
        </div>
      ) : rollout ? (
        <div data-testid="merge-rollout" style={{ ...card, fontSize: 13 }}>
          <div style={{ fontWeight: 700, marginBottom: 4 }}>Merge isn't rolled out yet</div>
          <div style={{ color: "var(--ink-3, #8a8278)" }}>
            This deployment's API doesn't serve duplicate detection yet. It'll appear once the rollout completes.
          </div>
          <button data-testid="merge-refresh" onClick={() => void load()} style={{ ...ghostBtn, marginTop: 10 }}>
            Refresh
          </button>
        </div>
      ) : error ? (
        <div data-testid="merge-error" style={{ ...card, color: "var(--danger, #b4453a)", fontSize: 13 }}>
          {error}
          <button data-testid="merge-retry" onClick={() => void load()} style={{ ...ghostBtn, marginTop: 10 }}>
            Try again
          </button>
        </div>
      ) : clusters.length === 0 ? (
        <div data-testid="merge-empty" style={{ ...card, textAlign: "center", color: "var(--ink-3, #8a8278)" }}>
          <div style={{ fontWeight: 650, color: "var(--ink, #2a2622)", marginBottom: 4 }}>
            No duplicate {entity} found
          </div>
          <div style={{ fontSize: 13 }}>Your {entity} look clean. Nothing to merge.</div>
        </div>
      ) : (
        <div data-testid="merge-clusters">
          {clusters.map((c) => {
            const winnerId = winners[c.key] ?? c.members[0]?.id;
            const rowBusy = busyKey === c.key;
            return (
              <div key={c.key} data-testid="merge-cluster" data-cluster-key={c.key} style={card}>
                <div data-testid="merge-cluster-reason" style={{ fontSize: 12, fontWeight: 650, color: "var(--ink-3, #8a8278)", marginBottom: 8 }}>
                  {c.members.length} records · {reasonLabel(c.reason)}
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 12 }}>
                  {c.members.map((m) => (
                    <label
                      key={m.id}
                      data-testid="merge-member"
                      data-member-id={m.id}
                      style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 13.5, cursor: "pointer" }}
                    >
                      <input
                        data-testid="merge-winner-radio"
                        type="radio"
                        name={`winner-${c.key}`}
                        checked={winnerId === m.id}
                        onChange={() => setWinners((w) => ({ ...w, [c.key]: m.id }))}
                      />
                      <span>{memberLabel(m)}</span>
                      {winnerId === m.id ? (
                        <span style={{ fontSize: 11, fontWeight: 700, color: "var(--accent, #2a2622)" }}>KEEP</span>
                      ) : null}
                    </label>
                  ))}
                </div>
                <button
                  data-testid="merge-btn"
                  onClick={() => void doMerge(c)}
                  disabled={rowBusy}
                  style={primaryBtn}
                >
                  {rowBusy ? "Merging…" : `Merge ${c.members.length - 1} into the kept record`}
                </button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

export default MergeDuplicates;
