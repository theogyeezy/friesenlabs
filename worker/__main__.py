"""Worker container entrypoint: `python -m worker`.

Runs the self-hosted tool-execution worker against the Managed Agents environment queue. Requires
UPLIFT_ENV_ID + UPLIFT_ENV_KEY (the environment key only — never the org API key). Live Anthropic;
BLOCKED: needs Nick.
"""
import asyncio

from worker.worker import run

if __name__ == "__main__":
    asyncio.run(run())
