// Cube configuration (Build Guide Phase 3, Step 21).
// Wires the tenant security context so every query is force-scoped to the JWT's tenant_id.
// securityContext arrives ONLY from checkAuth below: it verifies the short-lived HS256 token the
// API/tool plane mints from the verified Cognito claim (agents/tools/cube_client.py) against
// CUBEJS_API_SECRET, then stamps { tenant_id } from the verified payload. This is defense-in-depth
// OVER Postgres RLS.
const { queryRewrite, contextToAppId, checkAuth } = require('./security');

module.exports = {
  // Verify the per-request tenant JWT ourselves; reject unsigned/expired/bad-signature tokens.
  checkAuth,
  // Keep compile/cache resources separate per tenant.
  contextToAppId,
  // Force a tenant_id filter onto every query; throw if no/forged tenant.
  queryRewrite,
};
