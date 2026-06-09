// Deals cube — pipeline metrics in business language (Build Guide Phase 3, Step 20).
// Measures/dimensions match db/schema.sql `deals`. tenant_id is present-but-hidden so the
// security context (queryRewrite) can force-filter on it without exposing it as a dimension.
cube('Deals', {
  sql_table: 'deals',

  joins: {
    Companies: { relationship: 'many_to_one', sql: `${CUBE}.company_id = ${Companies}.id` },
    Contacts: { relationship: 'many_to_one', sql: `${CUBE}.contact_id = ${Contacts}.id` },
  },

  measures: {
    count: { type: 'count' },
    pipeline_value: { sql: 'amount', type: 'sum', format: 'currency' },
    avg_deal_size: { sql: 'amount', type: 'avg', format: 'currency' },
  },

  dimensions: {
    id: { sql: 'id', type: 'string', primary_key: true },
    title: { sql: 'title', type: 'string' },
    stage: { sql: 'stage', type: 'string' },
    currency: { sql: 'currency', type: 'string' },
    created_at: { sql: 'created_at', type: 'time' },
    tenant_id: { sql: 'tenant_id', type: 'string', shown: false },
  },
});
