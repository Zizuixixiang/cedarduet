"""Small persistent-state helpers for future card, dice, and phased games.

All randomness returned here is data: callers keep the mutated game state in
``rooms.board_state`` so reloads never reroll or reshuffle. These helpers contain
no concrete game rules and are not themselves plugins.
"""

from __future__ import annotations

import random
from copy import deepcopy
from typing import Any, Iterable, Sequence


FLOW_KEY = "flow"


def ensure_flow(
    state: dict[str, Any],
    *,
    phase: str,
    round_number: int = 1,
    turn_number: int = 0,
) -> dict[str, Any]:
    """Create recoverable phase/round/turn metadata exactly once."""
    if not isinstance(phase, str) or not phase.strip():
        raise ValueError("phase 不能为空")
    if round_number < 1 or turn_number < 0:
        raise ValueError("round_number/turn_number 超出范围")
    flow = state.setdefault(
        FLOW_KEY,
        {
            "phase": phase.strip(),
            "round_number": round_number,
            "turn_number": turn_number,
        },
    )
    if not isinstance(flow, dict):
        raise ValueError("state.flow 必须是对象")
    return flow


def advance_flow(
    state: dict[str, Any],
    *,
    phase: str | None = None,
    next_round: bool = False,
    turn_increment: int = 1,
) -> dict[str, Any]:
    """Advance persisted flow metadata without choosing the next participant."""
    flow = state.get(FLOW_KEY)
    if not isinstance(flow, dict):
        raise ValueError("请先调用 ensure_flow 初始化 state.flow")
    if phase is not None:
        if not isinstance(phase, str) or not phase.strip():
            raise ValueError("phase 不能为空")
        flow["phase"] = phase.strip()
    if isinstance(turn_increment, bool) or not isinstance(turn_increment, int):
        raise ValueError("turn_increment 必须是整数")
    if next_round:
        flow["round_number"] = int(flow.get("round_number", 1)) + 1
        flow["turn_number"] = 0
    else:
        flow["turn_number"] = int(flow.get("turn_number", 0)) + turn_increment
    return flow


def ensure_card_zones(
    state: dict[str, Any],
    cards: Sequence[Any],
    player_ids: Iterable[str],
    *,
    key: str = "cards",
    rng: random.Random | None = None,
) -> dict[str, Any]:
    """Shuffle and persist deck/discard/hands only when ``key`` is absent."""
    existing = state.get(key)
    if existing is not None:
        if not isinstance(existing, dict):
            raise ValueError(f"state.{key} 必须是对象")
        return existing
    ids = list(player_ids)
    if not ids or len(ids) != len(set(ids)):
        raise ValueError("player_ids 必须非空且不能重复")
    deck = deepcopy(list(cards))
    (rng or random.SystemRandom()).shuffle(deck)
    zones = {
        "deck": deck,
        "discard": [],
        "hands": {player_id: [] for player_id in ids},
    }
    state[key] = zones
    return zones


def draw_cards(
    state: dict[str, Any],
    player_id: str,
    count: int = 1,
    *,
    key: str = "cards",
) -> list[Any]:
    """Move persisted cards from the top of deck into one seat's hand."""
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ValueError("count 必须是非负整数")
    zones = state.get(key)
    if not isinstance(zones, dict) or not isinstance(zones.get("hands"), dict):
        raise ValueError("牌区尚未初始化")
    if player_id not in zones["hands"]:
        raise ValueError("player_id 不在牌区座位中")
    deck = zones.get("deck")
    if not isinstance(deck, list):
        raise ValueError("deck 必须是数组")
    drawn = [deck.pop() for _ in range(min(count, len(deck)))]
    zones["hands"][player_id].extend(drawn)
    return deepcopy(drawn)


def discard_cards(
    state: dict[str, Any],
    player_id: str,
    cards: Sequence[Any],
    *,
    key: str = "cards",
) -> None:
    """Move exact persisted card values from one hand to the discard pile."""
    zones = state.get(key)
    if not isinstance(zones, dict) or player_id not in zones.get("hands", {}):
        raise ValueError("牌区或 player_id 无效")
    hand = zones["hands"][player_id]
    discard = zones.get("discard")
    if not isinstance(hand, list) or not isinstance(discard, list):
        raise ValueError("hand/discard 必须是数组")
    for card in cards:
        try:
            hand.remove(card)
        except ValueError as exc:
            raise ValueError("要弃置的牌不在该参与者手中") from exc
        discard.append(card)


def public_card_state(state: dict[str, Any], *, key: str = "cards") -> dict[str, Any]:
    """Return counts plus public discard data, never another seat's hand."""
    zones = state.get(key)
    if not isinstance(zones, dict):
        raise ValueError("牌区尚未初始化")
    return {
        "deck_count": len(zones.get("deck", [])),
        "discard": deepcopy(zones.get("discard", [])),
        "hand_counts": {
            player_id: len(hand)
            for player_id, hand in zones.get("hands", {}).items()
        },
    }


def private_hand(
    state: dict[str, Any], player_id: str, *, key: str = "cards"
) -> list[Any]:
    zones = state.get(key)
    hands = zones.get("hands") if isinstance(zones, dict) else None
    if not isinstance(hands, dict) or player_id not in hands:
        raise ValueError("牌区或 player_id 无效")
    return deepcopy(hands[player_id])


def roll_dice(
    state: dict[str, Any],
    *,
    roller_player_id: str,
    count: int = 1,
    sides: int = 6,
    visible_to_player_ids: Iterable[str] | None = None,
    key: str = "dice_rolls",
    rng: random.Random | None = None,
) -> dict[str, Any]:
    """Append and return one persisted public or participant-private roll."""
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise ValueError("count 必须是正整数")
    if isinstance(sides, bool) or not isinstance(sides, int) or sides < 2:
        raise ValueError("sides 必须是至少 2 的整数")
    rolls = state.setdefault(key, [])
    if not isinstance(rolls, list):
        raise ValueError(f"state.{key} 必须是数组")
    visible_to = (
        sorted(set(visible_to_player_ids))
        if visible_to_player_ids is not None
        else None
    )
    generator = rng or random.SystemRandom()
    record = {
        "sequence": len(rolls) + 1,
        "roller_player_id": roller_player_id,
        "count": count,
        "sides": sides,
        "values": [generator.randint(1, sides) for _ in range(count)],
        "visible_to_player_ids": visible_to,
    }
    rolls.append(record)
    return deepcopy(record)


def visible_dice_rolls(
    state: dict[str, Any], viewer_player_id: str, *, key: str = "dice_rolls"
) -> list[dict[str, Any]]:
    """Project persisted rolls visible to one authenticated participant."""
    rolls = state.get(key, [])
    if not isinstance(rolls, list):
        raise ValueError(f"state.{key} 必须是数组")
    return deepcopy([
        record
        for record in rolls
        if record.get("visible_to_player_ids") is None
        or viewer_player_id in record.get("visible_to_player_ids", [])
    ])
