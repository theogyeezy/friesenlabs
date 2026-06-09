// Contacts cube — matches db/schema.sql `contacts`.
cube('Contacts', {
  sql_table: 'contacts',

  joins: {
    Companies: { relationship: 'many_to_one', sql: `${CUBE}.company_id = ${Companies}.id` },
  },

  measures: {
    count: { type: 'count' },
  },

  dimensions: {
    id: { sql: 'id', type: 'string', primary_key: true },
    name: { sql: 'name', type: 'string' },
    email: { sql: 'email', type: 'string' },
    phone: { sql: 'phone', type: 'string' },
    created_at: { sql: 'created_at', type: 'time' },
    tenant_id: { sql: 'tenant_id', type: 'string', shown: false },
  },
});
