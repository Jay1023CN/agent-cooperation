"""Renew local handoff leases while both external authorities remain valid."""

from __future__ import annotations

import threading
from typing import Callable

from .handoff_store import HandoffStore


class LeaseKeeper:
    def __init__(self, store: HandoffStore, verify: Callable, check: Callable, interval: float = 15):
        if not 0 < interval < 60:
            raise ValueError("Lease renewal interval must be less than 60 seconds")
        self.store, self.verify, self.check, self.interval = store, verify, check, interval
        self.lock = threading.Lock()
        self.workers: dict[str, tuple[threading.Event, threading.Thread]] = {}
        self.closed = False

    def refresh(self, lineage: str, task_id: str, issue_id: str) -> float:
        if self.verify(task_id, issue_id)["lineage"] != lineage:
            raise ValueError("Multica lineage changed")
        self.check(lineage, task_id, self.store.instance_id())
        return self.store.renew(lineage, task_id)

    def start(self, lineage: str, task_id: str, issue_id: str):
        with self.lock:
            if self.closed:
                raise RuntimeError("Lease keeper is closed")
            existing = self.workers.get(lineage)
            if existing and existing[1].is_alive():
                return
            stop = threading.Event()

            def run():
                try:
                    while not stop.wait(self.interval):
                        try:
                            snapshot = self.store.inspect(lineage)
                            if not snapshot or snapshot["state"] not in ("pending", "claimed"):
                                return
                            self.refresh(lineage, task_id, issue_id)
                        except Exception:
                            # Never resume after a failed verification; preserve the record for inspection.
                            try:
                                self.store.expire(lineage)
                            except Exception:
                                pass  # If storage is unavailable, the existing lease still times out.
                            return
                finally:
                    with self.lock:
                        current = self.workers.get(lineage)
                        if current and current[0] is stop:
                            self.workers.pop(lineage)

            thread = threading.Thread(target=run, name="handoff-lease", daemon=True)
            self.workers[lineage] = (stop, thread)
            thread.start()

    def stop(self, lineage: str):
        with self.lock:
            worker = self.workers.get(lineage)
            if worker:
                worker[0].set()

    def close(self):
        with self.lock:
            self.closed = True
            for stop, _ in self.workers.values():
                stop.set()
