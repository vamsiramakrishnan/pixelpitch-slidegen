"""Independent workstation consumer for the shared native deck pipeline."""

from __future__ import annotations

import asyncio
import logging
import uuid

from dotenv import load_dotenv

from app.a2a_jobs import configured_jobs
from app.mcp_worker import generate, run_job

logger = logging.getLogger(__name__)


async def generate_a2a(job, store, worker):
    return await generate(job, store, worker, persist_previews=True)


async def work() -> None:
    store, worker = configured_jobs(), uuid.uuid4().hex
    while True:
        try:
            job = await asyncio.to_thread(store.claim, worker)
            if job:
                logger.info("deck_job_started job=%s", job["id"])
                await run_job(job, store, worker, pipeline=generate_a2a)
                saved = await asyncio.to_thread(
                    store.snapshot, job["workspace"], job["id"]
                )
                logger.info(
                    "deck_job_finished job=%s status=%s", job["id"], saved["status"]
                )
            else:
                await asyncio.sleep(0.25)
        except Exception:
            logger.exception("The A2A worker could not access its queue")
            await asyncio.sleep(2)


if __name__ == "__main__":
    import argparse
    import os

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    args = parser.parse_args()
    load_dotenv()
    os.environ["SLIDEGEN_A2A_JOB_DB"] = args.db
    logging.basicConfig(level=logging.INFO)
    asyncio.run(work())
