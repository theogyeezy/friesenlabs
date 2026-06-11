// Cube configuration (Build Guide Phase 3, Step 21).
// Wires the tenant security context so every query is force-scoped to the JWT's tenant_id.
// securityContext arrives ONLY from checkAuth below: it verifies the short-lived HS256 token the
// API/tool plane mints from the verified Cognito claim (agents/tools/cube_client.py) against
// CUBEJS_API_SECRET, then stamps { tenant_id } from the verified payload. This is defense-in-depth
// OVER Postgres RLS.
const { queryRewrite, contextToAppId, checkAuth, driverFactory } = require('./security');

module.exports = {
  // Verify the per-request tenant JWT ourselves; reject unsigned/expired/bad-signature tokens.
  checkAuth,
  // Keep compile/cache resources separate per tenant.
  contextToAppId,
  // Force a tenant_id filter onto every query; throw if no/forged tenant.
  queryRewrite,
  // #177: per-tenant Postgres driver — every pooled connection runs a parameterized
  // set_config('app.current_tenant', <verified tenant>, false) so the FORCE'd RLS policies
  // (crm_app is a non-owner role) actually return the tenant's rows. Without this, RLS blanks
  // every governed query and queryRewrite has nothing left to filter.
  driverFactory,
};
