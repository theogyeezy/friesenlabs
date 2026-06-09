# Cube semantic layer image (Fargate). Build + push to ECR uplift-cube.
# Uses the official Cube image with our model + the tenant security context baked in.
FROM cubejs/cube:latest

WORKDIR /cube/conf

# Cube config (the tenant security context wiring) + the model files.
COPY cube.js ./cube.js
COPY security.js ./security.js
COPY model/ ./model/

# Cube reads CUBEJS_* env (DB host/creds via Secrets Manager, Redis driver) at runtime — see
# infra/modules/cube. The crm_app non-owner role is what it connects with, so RLS applies.
EXPOSE 4000
