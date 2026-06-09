// Companies cube — matches db/schema.sql `companies`.
cube('Companies', {
  sql_table: 'companies',

  measures: {
    count: { type: 'count' },
  },

  dimensions: {
    id: { sql: 'id', type: 'string', primary_key: true },
    name: { sql: 'name', type: 'string' },
    domain: { sql: 'domain', type: 'string' },
    created_at: { sql: 'created_at', type: 'time' },
    tenant_id: { sql: 'tenant_id', type: 'string', shown: false },
  },
});
