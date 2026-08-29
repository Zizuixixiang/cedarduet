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
    ProviderDecision,
    get_npc_provider,
)
from .npc_runtime import (
    NpcDecisionTicket,
    complete_npc_decision,
    fail_npc_decision,
    reserve_npc_decision,
)


@dataclass(frozen=True)
class NpcTurnResult:
    status: str
    source: str
    action: dict[str, Any] | None
    message: str | None
    room: dict[str, Any] | None


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


def _compact_public_events(
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


def _decision_request(
    room: dict[str, Any], npc_player_id: str
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
        recent_public_events = _compact_public_events(
            list_timeline(
                room["room_id"], 20, npc_player_id, public_only=True
            ),
            participant_directory,
        )
        public_actions = game.npc_public_actions(
            deepcopy(state), deepcopy(actor), participants
        )
        legal_actions = game.npc_legal_actions(
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
        or not isinstance(legal_actions, list)
        or not legal_actions
        or any(not isinstance(item, dict) for item in legal_actions)
        or not isinstance(game_rules, str)
        or not game_rules.strip()
    ):
        raise DuelError("NPC 插件必须提供规则、状态和至少一个合法行动")
    action_map: dict[str, dict[str, Any]] = {}
    exposed_actions = []
    for action in legal_actions:
        action_id = _action_id(action)
        if action_id in action_map and action_map[action_id] != action:
            raise DuelError("NPC 合法行动 ID 冲突")
        action_map[action_id] = deepcopy(action)
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
            return NpcTurnResult(
                "already_applied", "recovered", action, message, latest
            )
        return NpcTurnResult("applied", "recovered", action, message, updated)
    if not ticket.created:
        return NpcTurnResult("in_progress", "existing", None, None, None)

    try:
        request, action_map = _decision_request(room, npc_player_id)
    except Exception as exc:
        fail_npc_decision(ticket, str(exc))
        raise
    selected: ProviderDecision | None = None
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
        return NpcTurnResult(
            "already_applied", "recovered", action, selected.message, latest
        )
    return NpcTurnResult("applied", source, action, selected.message, updated)
