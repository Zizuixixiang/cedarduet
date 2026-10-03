"""Request leases only: never read or mutate room/presence/game state.

The loopback MCP adapter supplies an ordered generation for each explicit call;
heartbeat retries reuse it. Tombstones reject delayed requests after cancellation.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)
LEASE_SECONDS = 3720  # maximum gateway wait (3600s) plus transport buffer


class WaitCancelled(Exception):
    pass


def cancelled_response(room_id):
    return {
        "ok": True, "status": "wait_cancelled", "room_id": room_id,
        "message": "挂等已取消或被新请求替代；停止此调用链，不要据此行动或自动续等。需要时显式重新挂等。",
    }


@dataclass
class WaitLease:
    generation: int
    cancelled: asyncio.Event = field(default_factory=asyncio.Event)
    running: bool = True

    def check(self):
        if self.cancelled.is_set():
            raise WaitCancelled


class WaitControl:
    def __init__(self):
        self.entries = {}
        self.last_generation = 0
        self.started_at = time.time_ns()

    def apply(self, player, room, *, generation=None, waiting=False, resume=False):
        now = time.time_ns()
        cutoff = now - LEASE_SECONDS * 1_000_000_000
        for key, (old_generation, lease) in list(self.entries.items()):
            if old_generation < cutoff:
                if lease:
                    lease.cancelled.set()
                del self.entries[key]
        key = (player, room)
        previous, lease = self.entries.get(key, (0, None))
        if generation is None:
            generation = max(now, self.last_generation + 1, previous + 1)
        self.last_generation = max(generation, self.last_generation)
        if generation < max(cutoff, self.started_at) or generation < previous:
            raise WaitCancelled
        if waiting and generation == previous:
            # A generation identifies one sequential request chain, never a
            # replay of its initial move/message or concurrent heartbeat calls.
            if lease is None or not resume or lease.running:
                raise WaitCancelled
            lease.check()
            lease.running = True
            return lease
        if resume and (generation != previous or lease is None):
            raise WaitCancelled
        if key not in self.entries and len(self.entries) >= 10000:
            # Never evict a live tombstone and thereby resurrect a delayed call.
            raise WaitCancelled
        if lease:
            lease.cancelled.set()
            logger.info("duel_wait %s player=%s room=%s", "superseded" if waiting else "cancelled", player, room)
        lease = WaitLease(generation) if waiting else None
        self.entries[key] = (generation, lease)
        if lease:
            logger.info("duel_wait created player=%s room=%s", player, room)
        return lease

    def finish(self, player, room, lease, *, reason="finished"):
        if self.entries.get((player, room)) == (lease.generation, lease):
            self.entries[(player, room)] = (lease.generation, None)
            lease.cancelled.set()
            logger.info("duel_wait %s player=%s room=%s", reason, player, room)


wait_control = WaitControl()
