// ai.tsx — Managed AI helper (simulated, typed stub).
//
// Production will wire window.claude.complete to the Managed runtime seam. For
// this prototype it stays fully simulated: no network, no secrets. The helper
// returns short, business-flavored text so the UI feels alive. We never surface
// the underlying model name in user-facing copy (say "Managed").

type ClaudeHelper = { complete: (prompt: string) => Promise<string> };

// Install the simulated window.claude helper — MOCK BUILDS ONLY. The outer
// gate is BUILD-TIME (Vite statically replaces import.meta.env.VITE_API_MOCK),
// so real-mode production bundles contain neither the stub installation nor
// the canned replies: a deployed app must never answer "as the model" from a
// hard-coded script, and a console-poking visitor must not find a fake
// window.claude seam. Production wires window.claude.complete to the Managed
// runtime seam instead.
if (import.meta.env.VITE_API_MOCK !== "0" && import.meta.env.VITE_API_MOCK !== "false") {
  // A tiny deterministic-ish simulated completion. It reflects a few words
  // from the prompt back so responses feel contextual, without any real call.
  const simulatedComplete = (prompt: string): Promise<string> => {
    const p = String(prompt || "");
    const lower = p.toLowerCase();
    let reply: string;
    if (lower.includes("pipeline") || lower.includes("deal")) {
      reply = "Your pipeline looks healthy. I would focus on the top open deals first, the agents have already drafted next steps for each.";
    } else if (lower.includes("approve") || lower.includes("greenlight")) {
      reply = "There are a few actions waiting on your sign-off. Most are routine follow-ups, so you can clear them quickly from Greenlight.";
    } else if (lower.includes("agent")) {
      reply = "Your agents are online and working. I can hand any task to one of them and surface the result here.";
    } else {
      reply = "Here is what I would do next, prioritize the highest-value open work, then let the agents handle the routine follow-ups.";
    }
    // small async tick to mimic a real call
    return new Promise((resolve) => setTimeout(() => resolve(reply), 220));
  };
  // Install the simulated helper if nothing else has.
  if (typeof window !== "undefined" && !window.claude) {
    (window as any).claude = { complete: simulatedComplete } as ClaudeHelper;
  }
}

// real LLM helper (window.claude) with graceful fallback + business context
async function askClaude(prompt: string, fallback?: string): Promise<string> {
  try {
    if (window.claude && window.claude.complete) {
      const out = await window.claude.complete(prompt);
      if (out && String(out).trim()) return String(out).trim();
    }
  } catch (e) {}
  return fallback || "I couldn't reach the model just now, mind trying again?";
}

function bizContext(): string {
  const s: any = window.FLStore ? window.FLStore.getState() : {};
  const deals = s.deals || [];
  const open = deals.filter((d: any) => d.stage !== "won");
  const pipeline = open.reduce((t: number, d: any) => t + d.value, 0);
  const won = deals.filter((d: any) => d.stage === "won").reduce((t: number, d: any) => t + d.value, 0);
  const pending = (s.greenlight || []).filter((i: any) => i.status === "pending").length;
  const agents = Object.values(s.agents || {}).map((a: any) => `${a.name} (${a.role})`).join(", ");
  const top = deals.slice().sort((a: any, b: any) => b.value - a.value).slice(0, 5).map((d: any) => `${d.co} $${d.value.toLocaleString()} [${d.stage}]`).join("; ");
  return [
    "You are the assistant inside the Uplift agentic operations app, used by a small-business owner named Jordan.",
    "Be warm, concise and specific. Answer in 1 to 3 short sentences, plain text (no markdown headers).",
    "LIVE workspace data you can reference:",
    `- Open pipeline: $${pipeline.toLocaleString()} across ${open.length} deals`,
    `- Revenue won: $${won.toLocaleString()}`,
    `- Approvals pending in Greenlight: ${pending}`,
    `- Agents: ${agents}`,
    `- Top deals: ${top}`,
  ].join("\n");
}

(window as any).askClaude = askClaude;
(window as any).bizContext = bizContext;

export { askClaude, bizContext };
