"""Idempotent rules-engine-to-provider NPC turn execution."""

from __future__ import annotations

import asyncio
import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from .database import connect, decode_room
from .framework import (
    DuelError,
    _decorate,
    _room_id,
    list_timeline,
    play_move,
    project_room_for_viewer,
)
from .games import get_game
from .npc_personas import PersonaConfigError, get_persona
from .npc_providers import (
    NpcDecisionRequest,
    NpcProvider,
    NpcSpeechRequest,
    ProviderDecision,
    get_npc_provider,
)
from .npc_runtime import (
    NpcDecisionTicket,
    NpcSpeechClaim,
    begin_npc_full_turn,
    complete_npc_full_turn,
    complete_npc_decision,
    complete_npc_speech,
    fail_npc_decision,
    fail_npc_speech,
    reserve_npc_decision,
)


_speech_tasks: set[asyncio.Task[bool]] = set()


@dataclass(frozen=True)
class NpcTurnResult:
    status: str
    source: str
    action: dict[str, Any] | None
    message: str | None
    room: dict[str, Any] | None
    speech_task: asyncio.Task[bool] | None = None


def _load_room(room_id: str) -> dict[str, Any]:
    normalized = _room_id(room_id)
    conn = connect()
    try:
        row = conn.execute(
            "SELECT * FROM rooms WHERE room_id = ?", (normalized,)
        ).fetchone()
        if row is None:
            raise DuelError("房间不存在", 404)
        return _decorate(decode_room(row, conn))
    finally:
        conn.close()


def _action_id(action: dict[str, Any]) -> str:
    canonical = json.dumps(
        action, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "a_" + hashlib.sha256(canonical).hexdigest()[:20]


def _participant_directory(projected_room: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "player_id": participant["player_id"],
            "display_name": participant["display_name"],
            "seat_index": participant["seat_index"],
            "participant_kind": participant["participant_kind"],
            "activity_state": participant.get("activity_state", "active"),
            "participant_summary": deepcopy(participant.get("game_metadata", {})),
        }
        for participant in sorted(
            projected_room["participants"], key=lambda item: item["seat_index"]
        )
    ]


def _compact_visible_events(
    events: list[dict[str, Any]], participants: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    participant_by_id = {item["player_id"]: item for item in participants}
    compact: list[dict[str, Any]] = []
    for event in events:
        sender = event.get("sender")
        if not isinstance(sender, dict):
            raise DuelError("NPC 公共事件缺少行动者")
        player_id = sender.get("player_id")
        participant = participant_by_id.get(player_id)
        if participant is not None:
            actor = {
                key: participant[key]
                for key in (
                    "player_id", "display_name", "seat_index", "participant_kind"
                )
            }
        elif player_id == "system":
            actor = {
                "player_id": "system",
                "display_name": str(sender.get("name") or "双弈裁判"),
                "seat_index": None,
                "participant_kind": "system",
            }
        else:
            raise DuelError("NPC 公共事件行动者不属于房间")
        item: dict[str, Any] = {
            "sequence": event["sequence"],
            "created_at": event["created_at"],
            "event_type": event["event_type"],
            "actor": actor,
        }
        text = event.get("text")
        if isinstance(text, str) and text:
            item["text"] = text
        if event["event_type"] == "move":
            move_label = event.get("move_label")
            if isinstance(move_label, str) and move_label:
                item["move_label"] = move_label
        move = event.get("move")
        if isinstance(move, dict):
            item["move"] = deepcopy(move)
        compact.append(item)
    return compact


def _authoritative_legal_actions(
    room: dict[str, Any], actor: dict[str, Any]
) -> list[dict[str, Any]]:
    game = get_game(room["game_type"])
    try:
        actions = game.npc_legal_actions(
            deepcopy(room["board_state"]),
            deepcopy(actor),
            deepcopy(room["participants"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise DuelError(f"NPC 插件合法行动无效：{exc}") from exc
    if (
        not isinstance(actions, list)
        or not actions
        or any(not isinstance(item, dict) for item in actions)
    ):
        raise DuelError("NPC 插件必须提供至少一个合法行动")
    return actions


def _action_map(
    legal_actions: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    mapped: dict[str, dict[str, Any]] = {}
    for action in legal_actions:
        action_id = _action_id(action)
        if action_id in mapped and mapped[action_id] != action:
            raise DuelError("NPC 合法行动 ID 冲突")
        mapped[action_id] = deepcopy(action)
    return mapped


def _decision_request(
    room: dict[str, Any], npc_player_id: str,
    legal_actions: list[dict[str, Any]],
) -> tuple[NpcDecisionRequest, dict[str, dict[str, Any]]]:
    game = get_game(room["game_type"])
    actor = next(
        (
            item for item in room["participants"]
            if item["player_id"] == npc_player_id
        ),
        None,
    )
    if actor is None or actor.get("participant_kind") != "system_npc":
        raise DuelError("当前行动者不是系统 NPC", 409)
    try:
        persona = get_persona(actor["npc_persona_id"])
        participants = deepcopy(room["participants"])
        state = deepcopy(room["board_state"])
        projected_room = project_room_for_viewer(room, npc_player_id)
        participant_directory = _participant_directory(projected_room)
        public_state = deepcopy(projected_room["board_state"])
        private_state = deepcopy(projected_room["private_state"])
        recent_public_events = _compact_visible_events(
            list_timeline(
                room["room_id"], 20, npc_player_id, public_only=True
            ),
            participant_directory,
        )
        public_actions = game.npc_public_actions(
            deepcopy(state), deepcopy(actor), participants
        )
        game_rules = game.npc_compact_rules(
            deepcopy(state), deepcopy(actor), participants
        )
    except PersonaConfigError as exc:
        raise DuelError(str(exc), 503) from exc
    except (KeyError, TypeError, ValueError) as exc:
        raise DuelError(f"NPC 插件上下文无效：{exc}") from exc
    if (
        not isinstance(public_state, dict)
        or not isinstance(private_state, dict)
        or not isinstance(public_actions, list)
        or any(not isinstance(item, dict) for item in public_actions)
        or not isinstance(game_rules, str)
        or not game_rules.strip()
    ):
        raise DuelError("NPC 插件必须提供规则、状态和至少一个合法行动")
    action_map = _action_map(legal_actions)
    exposed_actions = []
    for action in legal_actions:
        action_id = _action_id(action)
        exposed_actions.append({"action_id": action_id, "action": deepcopy(action)})
    return (
        NpcDecisionRequest(
            persona=persona.model_context(),
            game_rules=game_rules.strip(),
            participants=participant_directory,
            public_state=public_state,
            private_state=private_state,
            recent_public_events=recent_public_events,
            public_actions=public_actions,
            legal_actions=exposed_actions,
        ),
        action_map,
    )


def _speech_request(room: dict[str, Any], npc_player_id: str) -> NpcSpeechRequest:
    game = get_game(room["game_type"])
    actor = next((
        item for item in room["participants"]
        if item["player_id"] == npc_player_id
    ), None)
    if actor is None or actor.get("participant_kind") != "system_npc":
        raise DuelError("发言者不是系统 NPC", 409)
    try:
        persona = get_persona(actor["npc_persona_id"])
        projected_room = project_room_for_viewer(room, npc_player_id)
        participants = _participant_directory(projected_room)
        visible_timeline = _compact_visible_events(
            list_timeline(
                room["room_id"], None, npc_player_id, public_only=False
            ),
            participants,
        )
    except PersonaConfigError as exc:
        raise DuelError(str(exc), 503) from exc
    except (KeyError, TypeError, ValueError) as exc:
        raise DuelError(f"NPC 发言上下文无效：{exc}") from exc
    return NpcSpeechRequest(
        persona=persona.model_context(),
        game_rules=str(room.get("rules_text") or game.rules_text),
        participants=participants,
        public_state=deepcopy(projected_room["board_state"]),
        private_state=deepcopy(projected_room["private_state"]),
        visible_timeline=visible_timeline,
    )


async def _attempt_npc_speech(
    claim: NpcSpeechClaim, provider: NpcProvider
) -> bool:
    try:
        room = _load_room(claim.room_id)
        request = _speech_request(room, claim.npc_player_id)
        message = await provider.speak(request)
        if not isinstance(message, str) or not message.strip():
            raise ValueError("NPC speech provider 未返回发言")
        return complete_npc_speech(claim, message)
    except asyncio.CancelledError:
        fail_npc_speech(claim, "NPC speech cancelled")
        raise
    except Exception as exc:
        fail_npc_speech(claim, str(exc))
        return False


def _schedule_npc_speech(
    claim: NpcSpeechClaim, provider: NpcProvider
) -> asyncio.Task[bool]:
    task = asyncio.create_task(
        _attempt_npc_speech(claim, provider),
        name=(
            f"npc-speech:{claim.room_id}:{claim.npc_player_id}:"
            f"{claim.completion_revision}"
        ),
    )
    _speech_tasks.add(task)
    task.add_done_callback(_speech_tasks.discard)
    return task


async def wait_for_npc_speech_tasks() -> None:
    """Test/shutdown helper; normal turn execution never waits for speech."""
    while _speech_tasks:
        await asyncio.gather(*list(_speech_tasks), return_exceptions=True)


def _finish_npc_action(
    updated: dict[str, Any], npc_player_id: str,
    provider: NpcProvider | None,
) -> asyncio.Task[bool] | None:
    if (
        updated.get("status") == "playing"
        and updated.get("current_player_id") == npc_player_id
    ):
        return None
    claim = complete_npc_full_turn(
        updated["room_id"], npc_player_id, int(updated["revision"])
    )
    if claim is None:
        return None
    return _schedule_npc_speech(claim, provider or get_npc_provider())


def _stored_decision(ticket: NpcDecisionTicket) -> tuple[dict[str, Any], str | None]:
    decision = ticket.decision or {}
    action = decision.get("action")
    message = decision.get("message")
    if not isinstance(action, dict) or (
        message is not None and not isinstance(message, str)
    ):
        raise DuelError("已保存的 NPC 决策无效", 500)
    return action, message


async def run_current_npc_turn(
    room_id: str,
    *,
    provider: NpcProvider | None = None,
) -> NpcTurnResult:
    """Generate and apply at most one action for the current room revision."""
    room = _load_room(room_id)
    npc_player_id = room.get("current_player_id")
    actor = next(
        (
            item for item in room["participants"]
            if item["player_id"] == npc_player_id
        ),
        None,
    )
    if actor is None or actor.get("participant_kind") != "system_npc":
        raise DuelError("当前行动者不是系统 NPC", 409)
    begin_npc_full_turn(room["room_id"], room["revision"], npc_player_id)
    ticket = reserve_npc_decision(
        room["room_id"], room["revision"], npc_player_id
    )
    if ticket.status == "completed":
        action, message = _stored_decision(ticket)
        try:
            updated = play_move(
                room["room_id"], "ai", npc_player_id, action, message=message,
                expected_revision=room["revision"],
            )
        except DuelError:
            latest = _load_room(room["room_id"])
            if latest["revision"] <= room["revision"]:
                raise
            speech_task = _finish_npc_action(latest, npc_player_id, provider)
            return NpcTurnResult(
                "already_applied", "recovered", action, message, latest,
                speech_task,
            )
        speech_task = _finish_npc_action(updated, npc_player_id, provider)
        return NpcTurnResult(
            "applied", "recovered", action, message, updated, speech_task
        )
    if not ticket.created:
        return NpcTurnResult("in_progress", "existing", None, None, None)

    game = get_game(room["game_type"])
    selected: ProviderDecision | None = None
    try:
        legal_actions = _authoritative_legal_actions(room, actor)
        action_map = _action_map(legal_actions)
    except Exception as exc:
        fail_npc_decision(ticket, str(exc))
        raise
    if game.uses_local_npc_strategy:
        try:
            actor_copy = deepcopy(actor)
            participants = deepcopy(room["participants"])
            state = deepcopy(room["board_state"])
            action = game.choose_local_npc_action(
                state, actor_copy, participants
            )
            if (
                not isinstance(action, dict)
                or action not in legal_actions
                or not legal_actions
            ):
                raise ValueError("本地 NPC 策略必须选择权威 legal_actions 中的动作")
            selected = ProviderDecision(_action_id(action), None)
            source = "local"
        except Exception as exc:
            fail_npc_decision(ticket, str(exc))
            raise
    elif len(legal_actions) == 1:
        forced_id = next(iter(action_map))
        selected = ProviderDecision(forced_id, None)
        source = "forced"
    else:
        try:
            request, action_map = _decision_request(
                room, npc_player_id, legal_actions
            )
        except Exception as exc:
            fail_npc_decision(ticket, str(exc))
            raise
        active_provider = provider or get_npc_provider()
        if not ticket.stale_recovery:
            for _attempt in range(2):
                try:
                    candidate = await active_provider.decide(request)
                    if candidate.action_id not in action_map:
                        raise ValueError("provider 选择了不属于权威列表的 action_id")
                    selected = candidate
                    break
                except asyncio.CancelledError:
                    fail_npc_decision(ticket, "NPC decision cancelled")
                    raise
                except Exception:
                    selected = None
        if selected is None:
            fallback_id = sorted(action_map)[0]
            selected = ProviderDecision(fallback_id, None)
            source = "fallback"
        else:
            source = active_provider.name
    action = action_map[selected.action_id]
    completed = complete_npc_decision(
        ticket,
        action,
        list(action_map.values()),
        message=selected.message,
    )
    if not completed:
        recovered = reserve_npc_decision(
            room["room_id"], room["revision"], npc_player_id
        )
        action, recovered_message = _stored_decision(recovered)
        selected = ProviderDecision(_action_id(action), recovered_message)
        source = "recovered"
    try:
        updated = play_move(
            room["room_id"], "ai", npc_player_id, action,
            message=selected.message,
            expected_revision=room["revision"],
        )
    except DuelError:
        latest = _load_room(room["room_id"])
        if latest["revision"] <= room["revision"]:
            raise
        speech_task = _finish_npc_action(latest, npc_player_id, provider)
        return NpcTurnResult(
            "already_applied", "recovered", action, selected.message, latest,
            speech_task,
        )
    speech_task = _finish_npc_action(updated, npc_player_id, provider)
    return NpcTurnResult(
        "applied", source, action, selected.message, updated, speech_task
    )
