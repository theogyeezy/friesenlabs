"""Worker container entrypoint: `python -m worker`.

Runs the self-hosted tool-execution worker against the Managed Agents environment queue. Requires
UPLIFT_ENV_ID + UPLIFT_ENV_KEY (the environment key only — never the org API key). Live Anthropic;
BLOCKED: needs Nick.
"""
import asyncio
import logging

from worker.worker import run

if __name__ == "__main__":
    # INFO so the SDK's operational trail (poller claims, tool executions, runner lifecycle)
    # reaches CloudWatch — without a handler the live worker was undiagnosable (#161).
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(run())
