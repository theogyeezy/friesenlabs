# Provisioning Lambda image (REQ-005): wraps signup/lambda_handler.handler — one idempotent
# Provisioner step per SFN Task invocation. arm64 to match the api image toolchain.
# Build:  docker build --platform linux/arm64 -f provisioning-lambda.Dockerfile -t uplift-provisioning:<sha> .
FROM public.ecr.aws/lambda/python:3.13

COPY requirements-api.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements-api.txt

# The handler's lazy api.prod_deps import pulls these packages at cold start.
COPY signup/ ${LAMBDA_TASK_ROOT}/signup/
COPY api/    ${LAMBDA_TASK_ROOT}/api/
COPY shared/ ${LAMBDA_TASK_ROOT}/shared/
COPY agents/ ${LAMBDA_TASK_ROOT}/agents/
COPY conv/   ${LAMBDA_TASK_ROOT}/conv/
COPY db/     ${LAMBDA_TASK_ROOT}/db/

CMD ["signup.lambda_handler.handler"]
