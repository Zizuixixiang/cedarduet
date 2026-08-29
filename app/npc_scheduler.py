"""Background orchestration for persisted, idempotent system-NPC turns."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from .framework import DuelError, _room_id, get_room
from .npc_controller import NpcTurnResult, run_current_npc_turn
from .npc_runtime import (
    NPC_DECISION_LEASE_SECONDS,
    list_active_npc_turn_room_ids,
)


logger = logging.getLogger(__name__)

MAX_CONSECUTIVE_NPC_TURNS = 16
NPC_IN_PROGRESS_RETRY_SECONDS = NPC_DECISION_LEASE_SECONDS + 1

TurnRunner = Callable[[str], Awaitable[NpcTurnResult]]
RoomChangedCallback = Callable[[str], None]


def is_system_npc_turn(room: dict) -> bool:
    if room.get("status") != "playing":
        return False
    current_player_id = room.get("current_player_id")
    return any(
        participant.get("player_id") == current_player_id
        and participant.get("participant_kind") == "system_npc"
        and participant.get("join_status") == "joined"
        and participant.get("activity_state", "active") == "active"
        and participant.get("active", True)
        for participant in room.get("participants", [])
    )


class NpcTurnScheduler:
    """Keep at most one local worker per room while SQLite deduplicates workers."""

    def __init__(
        self,
        *,
        turn_runner: TurnRunner = run_current_npc_turn,
        room_changed: RoomChangedCallback | None = None,
        max_consecutive_turns: int = MAX_CONSECUTIVE_NPC_TURNS,
        in_progress_retry_seconds: float = NPC_IN_PROGRESS_RETRY_SECONDS,
    ) -> None:
        if max_consecutive_turns < 1:
            raise ValueError("max_consecutive_turns must be positive")
        self._turn_runner = turn_runner
        self._room_changed = room_changed
        self._max_consecutive_turns = max_consecutive_turns
        self._in_progress_retry_seconds = in_progress_retry_seconds
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._retry_tasks: dict[str, asyncio.Task[None]] = {}
        self._requested_again: set[str] = set()
        self._registry_lock = asyncio.Lock()
        self._started = False
        self._closed = False

    async def start(self) -> None:
        """Enable scheduling and enqueue every persisted active NPC turn."""
        async with self._registry_lock:
            if self._started:
                return
            self._started = True
            self._closed = False
        for room_id in list_active_npc_turn_room_ids():
            await self.schedule(room_id)

    async def shutdown(self) -> None:
        """Cancel owned background work and release every in-memory room slot."""
        async with self._registry_lock:
            self._closed = True
            self._started = False
            tasks = [*self._tasks.values(), *self._retry_tasks.values()]
            self._tasks.clear()
            self._retry_tasks.clear()
            self._requested_again.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def schedule(self, room_id: str) -> bool:
        """Idempotently enqueue a room without waiting for an NPC provider."""
        room_id = _room_id(room_id)
        async with self._registry_lock:
            if not self._started or self._closed:
                return False
            existing = self._tasks.get(room_id)
            if existing is not None and not existing.done():
                self._requested_again.add(room_id)
                return False
            task = asyncio.create_task(
                self._run_room(room_id), name=f"npc-turn:{room_id}"
            )
            self._tasks[room_id] = task
        return True

    async def _run_room(self, room_id: str) -> None:
        outcome = "error"
        try:
            outcome = await self._drain_room(room_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("system NPC background turn failed for room %s", room_id)
        finally:
            restart = False
            retry_to_cancel: asyncio.Task[None] | None = None
            async with self._registry_lock:
                current = asyncio.current_task()
                if self._tasks.get(room_id) is current:
                    self._tasks.pop(room_id, None)
                if outcome in {"idle", "missing", "limit"}:
                    retry_to_cancel = self._retry_tasks.pop(room_id, None)
                requested_again = room_id in self._requested_again
                self._requested_again.discard(room_id)
                restart = (
                    requested_again
                    and outcome not in {"in_progress", "limit"}
                    and self._started
                    and not self._closed
                )
            if retry_to_cancel is not None:
                retry_to_cancel.cancel()
            if outcome == "in_progress":
                await self._arm_in_progress_retry(room_id)
            elif restart:
                await self.schedule(room_id)

    async def _drain_room(self, room_id: str) -> str:
        for _turn_index in range(self._max_consecutive_turns):
            try:
                room = get_room(room_id)
            except DuelError as exc:
                if exc.status_code == 404:
                    return "missing"
                raise
            if not is_system_npc_turn(room):
                return "idle"
            revision = room["revision"]
            try:
                result = await self._turn_runner(room_id)
            except DuelError:
                latest = get_room(room_id)
                if (
                    latest.get("revision") != revision
                    or not is_system_npc_turn(latest)
                ):
                    continue
                raise
            if result.status == "in_progress":
                return "in_progress"
            if result.status not in {"applied", "already_applied"}:
                return "idle"
            if self._room_changed is not None:
                try:
                    self._room_changed(room_id)
                except Exception:
                    logger.exception(
                        "system NPC revision notification failed for room %s",
                        room_id,
                    )
        return "limit"

    async def _arm_in_progress_retry(self, room_id: str) -> None:
        async with self._registry_lock:
            if (
                not self._started
                or self._closed
                or room_id in self._retry_tasks
            ):
                return
            retry = asyncio.create_task(
                self._retry_after_lease(room_id),
                name=f"npc-turn-retry:{room_id}",
            )
            self._retry_tasks[room_id] = retry

    async def _retry_after_lease(self, room_id: str) -> None:
        try:
            await asyncio.sleep(self._in_progress_retry_seconds)
            async with self._registry_lock:
                current = asyncio.current_task()
                if self._retry_tasks.get(room_id) is current:
                    self._retry_tasks.pop(room_id, None)
            await self.schedule(room_id)
        except asyncio.CancelledError:
            raise
        finally:
            async with self._registry_lock:
                current = asyncio.current_task()
                if self._retry_tasks.get(room_id) is current:
                    self._retry_tasks.pop(room_id, None)
