// Activities cube — matches db/schema.sql `activities` (calls/emails/notes/meetings).
cube('Activities', {
  sql_table: 'activities',

  joins: {
    Deals: { relationship: 'many_to_one', sql: `${CUBE}.deal_id = ${Deals}.id` },
    Contacts: { relationship: 'many_to_one', sql: `${CUBE}.contact_id = ${Contacts}.id` },
  },

  measures: {
    count: { type: 'count' },
  },

  dimensions: {
    id: { sql: 'id', type: 'string', primary_key: true },
    kind: { sql: 'kind', type: 'string' },
    occurred_at: { sql: 'occurred_at', type: 'time' },
    tenant_id: { sql: 'tenant_id', type: 'string', shown: false },
  },
});
