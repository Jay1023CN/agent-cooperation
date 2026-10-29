"""Bounded waits and continuous handoff over the existing durable local store."""

from __future__ import annotations

import asyncio
import math
import time

from .handoff_store import HandoffError, HandoffStore

MAX_WAIT = 25


def wait_seconds(value: float) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not 0 <= value <= MAX_WAIT
    ):
        raise HandoffError(f"wait_seconds must be between 0 and {MAX_WAIT}")
    return value


class HandoffChannel:
    def __init__(self, store: HandoffStore):
        self.store = store

    async def next_task(self, consumer_id: str, request_id: str, seconds: float = MAX_WAIT) -> dict:
        seconds = wait_seconds(seconds)
        deadline = time.monotonic() + seconds
        registration = asyncio.create_task(asyncio.to_thread(self.store.begin_wait, consumer_id, request_id, seconds))
        try:
            # A cancelled await must not leave a late background registration behind the cleanup.
            await asyncio.shield(registration)
            while True:
                claimed = await asyncio.to_thread(self.store.claim_next, consumer_id, request_id)
                if claimed is not None:
                    if claimed["state"] == "claimed":
                        inbox = await asyncio.to_thread(
                            self.store.read_messages, "desktop", claimed["lineage"], 0, 50, claimed["handle"]
                        )
                        claimed.update(
                            state=inbox["state"],
                            messages=inbox["messages"],
                            next_after=inbox["next_after"],
                            has_more=inbox["has_more"],
                        )
                    return claimed
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return {"state": "idle", "consumer_id": consumer_id, "request_id": request_id}
                await asyncio.sleep(min(0.2, remaining))
        finally:
            try:
                await registration
            finally:
                await asyncio.to_thread(self.store.end_wait, consumer_id, request_id)

    async def wait_for_update(
        self,
        role: str,
        lineage: str,
        after: int = 0,
        known_state: str = "claimed",
        seconds: float = MAX_WAIT,
        handle: str | None = None,
    ) -> dict:
        seconds = wait_seconds(seconds)
        if known_state not in ("pending", "claimed", "submitted", "cancelled", "expired"):
            raise HandoffError("Invalid known_state")
        deadline = time.monotonic() + seconds
        while True:
            update = await asyncio.to_thread(self.store.read_messages, role, lineage, after, 50, handle)
            if update["messages"] or update["state"] != known_state:
                return {**update, "timed_out": False}
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return {**update, "timed_out": True}
            await asyncio.sleep(min(0.2, remaining))

    async def complete_and_wait(
        self,
        lineage: str,
        handle: str,
        summary: str,
        evidence: list[str],
        consumer_id: str,
        request_id: str,
        seconds: float = MAX_WAIT,
    ) -> dict:
        wait_seconds(seconds)
        self.store._identifier(request_id, "request_id")
        if (
            not isinstance(summary, str)
            or not summary.strip()
            or not evidence
            or not all(isinstance(item, str) and item.strip() for item in evidence)
        ):
            raise HandoffError("A summary and nonempty evidence are required")
        await asyncio.to_thread(self.store.check_consumer, lineage, handle, consumer_id)
        await asyncio.to_thread(self.store.submit, lineage, handle, {"summary": summary, "evidence": evidence})
        following = await self.next_task(consumer_id, request_id, seconds)
        return {"state": "submitted", "lineage": lineage, "next": following}
