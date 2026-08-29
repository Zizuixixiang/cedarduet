"""Thin Cedar Duet adapter around the vendored upstream Guandan engine.

No combination recognition, action comparison, tribute rule, level update, or
terminal rule lives here. Those decisions come from the vendored v0.1.0
``GuandanGame`` / ``GuandanRound`` / ``GuandanJudger`` / ``GuandanPlayer``.
This module only persists that object graph, assigns stable physical card IDs,
maps authoritative raw actions to compact IDs, and builds host projections.
"""

from __future__ import annotations

import base64
import hashlib
import json
import pickle
import random
from copy import deepcopy
from typing import Any

import numpy as np

from .guandan_rlcard.constants import CARD_RANK, make_card_values
from .guandan_rlcard.game.card_utils import card_to_str
from .guandan_rlcard.game.dealer import GuandanDealer
from .guandan_rlcard.game.game import GuandanGame
from .guandan_rlcard.game.player import GuandanPlayer


UPSTREAM_URL = "https://github.com/Choysang/rlcard-guandan"
UPSTREAM_TAG = "v0.1.0"
UPSTREAM_SHA = "42f83aa8d84c0047473e069244e07db0c02af420"

_SUIT_NAMES = {"S": "spades", "H": "hearts", "C": "clubs", "D": "diamonds"}
_PATTERN_TYPES = {
    "Single": ("single", "单张"),
    "Pair": ("pair", "对子"),
    "Trips": ("trips", "三张"),
    "ThreeWithTwo": ("three_with_two", "三带二"),
    "ThreePair": ("three_pair", "三连对"),
    "TwoTrips": ("two_trips", "钢板"),
    "Straight": ("straight", "顺子"),
    "StraightFlush": ("straight_flush", "同花顺"),
    "Bomb": ("bomb", "炸弹"),
}


def _external_rank(rank: str) -> str:
    return "10" if rank == "T" else rank


def _upstream_rank(rank: str) -> str:
    return "T" if rank == "10" else rank


def _rank_label(index: int) -> str:
    return _external_rank(CARD_RANK[min(max(index, 0), 12)])


def _card_identity(card: Any) -> str:
    return card_to_str(card)


def _card_id_for(code: str, copy_number: int) -> str:
    if code == "SB":
        return f"d{copy_number}-J-black_joker"
    if code == "HR":
        return f"d{copy_number}-J-red_joker"
    return f"d{copy_number}-{code[0]}-{_external_rank(code[1])}"


def _ensure_card_ids(game: GuandanGame) -> None:
    """Tag the upstream physical cards once; shuffles/transfers retain tags."""
    counts: dict[str, int] = {}
    for card in game.round.dealer.deck:
        if getattr(card, "_cedar_guandan_id", None):
            continue
        code = _card_identity(card)
        counts[code] = counts.get(code, 0) + 1
        card._cedar_guandan_id = _card_id_for(code, counts[code])


def _card_dict(card: Any) -> dict[str, Any]:
    code = _card_identity(card)
    card_id = str(getattr(card, "_cedar_guandan_id"))
    copy_number = int(card_id[1])
    if code == "SB":
        return {"id": card_id, "copy": copy_number, "suit": "joker", "rank": "black_joker"}
    if code == "HR":
        return {"id": card_id, "copy": copy_number, "suit": "joker", "rank": "red_joker"}
    return {
        "id": card_id,
        "copy": copy_number,
        "suit": _SUIT_NAMES[code[0]],
        "rank": _external_rank(code[1]),
    }


def is_wild(card: dict[str, Any], level_rank: str) -> bool:
    """Presentation helper for the upstream heart-level wildcard."""
    return card.get("suit") == "hearts" and card.get("rank") == level_rank


def card_strength(card: dict[str, Any], level_rank: str) -> int:
    """Presentation sort value sourced from upstream ``make_card_values``."""
    rank = str(card.get("rank"))
    upstream = {"black_joker": "B", "red_joker": "R"}.get(rank, _upstream_rank(rank))
    return int(make_card_values(_upstream_rank(level_rank))[upstream])


def card_sort_key(card: dict[str, Any], level_rank: str) -> tuple[int, int, int]:
    suits = {"spades": 0, "hearts": 1, "clubs": 2, "diamonds": 3, "joker": 4}
    return (
        card_strength(card, level_rank),
        suits.get(str(card.get("suit")), 5),
        int(card.get("copy", 0)),
    )


def card_label(card: dict[str, Any]) -> str:
    rank = str(card.get("rank", ""))
    if rank == "black_joker":
        return "小王"
    if rank == "red_joker":
        return "大王"
    suit = {
        "spades": "黑桃", "hearts": "红桃", "clubs": "梅花", "diamonds": "方块",
    }.get(str(card.get("suit")), "")
    return f"{suit}{rank}"


def _serialize_game(game: GuandanGame) -> str:
    return base64.b64encode(
        pickle.dumps(game, protocol=pickle.HIGHEST_PROTOCOL)
    ).decode("ascii")


def _deserialize_game(state: dict[str, Any]) -> GuandanGame:
    encoded = state.get("engine_blob")
    if not isinstance(encoded, str) or not encoded:
        raise ValueError("掼蛋上游引擎状态缺失")
    game = pickle.loads(base64.b64decode(encoded.encode("ascii"), validate=True))
    if not isinstance(game, GuandanGame) or not game.interactive_tribute:
        raise ValueError("掼蛋上游引擎状态版本不兼容")
    return game


def _participant_id(state: dict[str, Any], seat: int | None) -> str | None:
    if seat is None or seat < 0:
        return None
    order = state.get("participant_order", [])
    return str(order[seat]) if seat < len(order) else None


def _pattern_from_raw(action: list[Any]) -> dict[str, Any]:
    pattern_type, label = _PATTERN_TYPES[action[0]]
    if action[0] == "Bomb" and action[1] == "R":
        pattern_type, label = "joker_bomb", "四王炸"
    return {
        "type": pattern_type,
        "label": label,
        "size": len(action[2]),
        "main_rank": _external_rank(str(action[1])),
        "upstream_type": str(action[0]),
    }


def _physical_cards_for_action(player: GuandanPlayer, action: list[Any]) -> list[Any]:
    if not action or action[0] == "PASS":
        return []
    available = list(player.current_hand)
    selected = []
    for code in action[2]:
        for index, card in enumerate(available):
            if _card_identity(card) == code:
                selected.append(card)
                available.pop(index)
                break
        else:
            raise ValueError("上游合法动作引用了手牌中不存在的牌")
    return selected


def _action_kind(game: GuandanGame, action: list[Any]) -> str:
    if game.round.tribute_pending:
        return "tribute" if action[0] == "tribute" else "return_tribute"
    if not action:
        return "wind_follow"
    return "pass" if action[0] == "PASS" else "play"


def _raw_legal_actions(game: GuandanGame) -> list[list[Any]]:
    if game.is_over():
        return []
    seat = game.round.current_player
    player = game.players[seat]
    if game.round.tribute_pending:
        return list(game.round.available_tribute_actions(player))
    if not player.current_hand:
        # Upstream deliberately asks an emptied player for [] so its teammate
        # can receive the wind-follow lead after the other seats passed.
        return [[]]
    return list(player.available_actions(game.cur_rank, game.round.greater_player, game.judger))


def _action_id(state: dict[str, Any], seat: int, action: list[Any]) -> str:
    payload = {
        "upstream_sha": UPSTREAM_SHA,
        "deal": int(state.get("deal_number", 0)),
        "turn": int(state.get("turn_serial", 0)),
        "seat": seat,
        "action": action,
    }
    encoded = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return "g_" + hashlib.sha256(encoded).hexdigest()[:16]


def _descriptor(state: dict[str, Any], game: GuandanGame, action: list[Any]) -> dict[str, Any]:
    seat = game.round.current_player
    kind = _action_kind(game, action)
    cards = _physical_cards_for_action(game.players[seat], action)
    item = {
        "action_id": _action_id(state, seat, action),
        "kind": kind,
        "card_ids": [str(getattr(card, "_cedar_guandan_id")) for card in cards],
        "pattern_type": None,
        "pattern_label": {
            "pass": "过", "tribute": "进贡", "return_tribute": "还贡", "wind_follow": "接风",
        }.get(kind, "出牌"),
    }
    if kind == "play":
        pattern = _pattern_from_raw(action)
        item.update({"pattern": pattern, "pattern_type": pattern["type"], "pattern_label": pattern["label"]})
    return item


def _legal_pairs(
    state: dict[str, Any], game: GuandanGame
) -> list[tuple[dict[str, Any], list[Any]]]:
    unique: dict[str, tuple[dict[str, Any], list[Any]]] = {}
    for raw in _raw_legal_actions(game):
        item = _descriptor(state, game, raw)
        unique.setdefault(item["action_id"], (item, raw))
    return list(unique.values())


def _tribute_projection(state: dict[str, Any], game: GuandanGame) -> dict[str, Any]:
    source = game.round.tribute_state
    result: dict[str, Any] = {
        "status": str(source.get("status", "none")),
        "mode": str(source.get("mode", "none")),
        "countered": bool(source.get("countered", False)),
        "payer_ids": [_participant_id(state, seat) for seat in source.get("payer_ids", [])],
        "receiver_ids": [_participant_id(state, seat) for seat in source.get("receiver_ids", [])],
        "pending_payer_ids": [
            _participant_id(state, seat) for seat in source.get("pending_payer_ids", [])
        ],
        "pending_receiver_ids": [
            _participant_id(state, seat) for seat in source.get("pending_receiver_ids", [])
        ],
        "tributes": [],
        "returns": [],
    }
    if "tie" in source:
        result["tie"] = bool(source["tie"])
    leader = _participant_id(state, source.get("leader_id"))
    if leader is not None:
        result["leader_player_id"] = leader
    for offering in source.get("tributes", []):
        item = {
            "payer_player_id": _participant_id(state, offering.get("payer_id")),
            "card": _card_dict(offering["card"]),
        }
        receiver = _participant_id(state, offering.get("receiver_id"))
        if receiver is not None:
            item["receiver_player_id"] = receiver
        result["tributes"].append(item)
    for returned in source.get("returns", []):
        result["returns"].append({
            "receiver_player_id": _participant_id(state, returned.get("receiver_id")),
            "payer_player_id": _participant_id(state, returned.get("payer_id")),
            "card": _card_dict(returned["card"]),
        })
    return result


def _finished_seats(game: GuandanGame) -> list[int]:
    if game.round.game_over:
        return [seat for seat in game.round.result if seat >= 0]
    return [seat for seat in game.round.result[:game.round.win_count] if seat >= 0]


def _deal_summary(state: dict[str, Any], game: GuandanGame) -> dict[str, Any] | None:
    source = game.last_deal_summary
    if not isinstance(source, dict):
        return None
    placements = [_participant_id(state, seat) for seat in source.get("result", [])]
    winner_index = int(source["winner_team"])
    winner_team = "A" if winner_index == 0 else "B"
    before = source["rank_before"]
    after = source["rank_after"]
    return {
        "deal_number": int(source["deal_number"]),
        "placements": placements,
        "winner_team": winner_team,
        "level_gain": int(source["level_gain"]),
        "level_before": _rank_label(int(before[winner_index])),
        "level_after": _rank_label(int(after[winner_index])),
        "played_level": _rank_label(int(before[winner_index])),
        "double_up": placements[0] is not None and placements[1] is not None
        and state["teams"][placements[0]] == state["teams"][placements[1]],
        "match_won": bool(game.is_over()),
    }


def _sync_state(state: dict[str, Any], game: GuandanGame) -> None:
    _ensure_card_ids(game)
    order = list(state["participant_order"])
    state["engine"] = "Choysang/rlcard-guandan"
    state["engine_version"] = GuandanEngine.version
    state["upstream_sha"] = UPSTREAM_SHA
    state["deal_number"] = int(game.game_count)
    state["teams"] = {
        player_id: "A" if seat % 2 == 0 else "B" for seat, player_id in enumerate(order)
    }
    state["team_levels"] = {"A": _rank_label(int(game.team0_rank)), "B": _rank_label(int(game.team1_rank))}
    state["level_rank"] = _rank_label(int(game.cur_rank))
    state["hands"] = {
        player_id: [_card_dict(card) for card in game.players[seat].current_hand]
        for seat, player_id in enumerate(order)
    }
    state["finish_order"] = [str(order[seat]) for seat in _finished_seats(game)]
    state["tribute"] = _tribute_projection(state, game)
    if game.is_over():
        state["phase"] = "finished"
        state["turn_player_id"] = None
        winner = "A" if game.winner_team == 0 else "B"
        summary = _deal_summary(state, game) or {}
        state["winner_team"] = winner
        state["match_result"] = {
            "winner_team": winner,
            "winning_player_ids": [player_id for player_id in order if state["teams"][player_id] == winner],
            "placements": list(summary.get("placements", [])),
            "team_levels": deepcopy(state["team_levels"]),
            "deal_count": int(game.game_count),
        }
    else:
        status = game.round.tribute_state.get("status")
        state["phase"] = {"selecting_tribute": "tribute", "selecting_return": "return_tribute"}.get(
            status, "playing"
        )
        state["turn_player_id"] = str(order[game.round.current_player])
        state["winner_team"] = None
        state["match_result"] = None


class GuandanEngine:
    """JSON-state host facade whose decisions all come from upstream."""

    version = "0.1.0+cedar-adapter.1"

    @staticmethod
    def build_deck() -> list[dict[str, Any]]:
        dealer = GuandanDealer(np.random.RandomState(0))
        counts: dict[str, int] = {}
        result = []
        for card in dealer.deck:
            code = _card_identity(card)
            counts[code] = counts.get(code, 0) + 1
            card._cedar_guandan_id = _card_id_for(code, counts[code])
            result.append(_card_dict(card))
        return result

    @classmethod
    def waiting_state(cls, participant_ids: list[str]) -> dict[str, Any]:
        return {
            "engine": "Choysang/rlcard-guandan",
            "engine_version": cls.version,
            "upstream_sha": UPSTREAM_SHA,
            "participant_order": list(participant_ids),
            "teams": {player_id: "A" if seat % 2 == 0 else "B" for seat, player_id in enumerate(participant_ids)},
            "team_levels": {"A": "2", "B": "2"},
            "level_rank": "2",
            "deal_number": 0,
            "phase": "waiting",
            "turn_player_id": None,
            "hands": {player_id: [] for player_id in participant_ids},
            "played_cards": [],
            "trick": None,
            "finish_order": [],
            "tribute": {"status": "none", "mode": "none"},
            "deal_history": [],
            "action_history": [],
            "last_action": None,
            "last_public_delta": None,
            "winner_team": None,
            "match_result": None,
            "turn_serial": 0,
            "engine_blob": None,
        }

    @classmethod
    def new_match(
        cls,
        participant_ids: list[str],
        opener_player_id: str,
        rng: random.Random | random.SystemRandom | None = None,
    ) -> dict[str, Any]:
        if len(participant_ids) != 4 or len(set(participant_ids)) != 4:
            raise ValueError("掼蛋规则核心需要 4 个唯一席位")
        if opener_player_id not in participant_ids:
            raise ValueError("首位不属于掼蛋席位")
        source_rng = rng or random.SystemRandom()
        seed = int(source_rng.randrange(0, 2**32))
        game = GuandanGame(interactive_tribute=True)
        game.perfect_info = False
        game.np_random = np.random.RandomState(seed)
        players = [GuandanPlayer(seat, game.np_random) for seat in range(4)]
        game.init_game(players)
        _ensure_card_ids(game)
        opener_seat = participant_ids.index(opener_player_id)
        game.round.current_player = opener_seat
        game.state = game.get_state(opener_seat)

        state = cls.waiting_state(participant_ids)
        state.update({
            "played_cards": [],
            "trick": {
                "number": 1,
                "leader_player_id": opener_player_id,
                "last_play": None,
                "pass_player_ids": [],
                "wind_follow": False,
            },
        })
        _sync_state(state, game)
        state["last_action"] = {"kind": "deal_start", "deal_number": 1, "opener_player_id": opener_player_id}
        state["last_public_delta"] = cls._base_delta(state, "deal_start")
        state["engine_blob"] = _serialize_game(game)
        return state

    @classmethod
    def legal_actions(cls, state: dict[str, Any], player_id: str) -> list[dict[str, Any]]:
        if state.get("phase") in {"waiting", "finished"}:
            return []
        game = _deserialize_game(state)
        if _participant_id(state, game.round.current_player) != player_id:
            return []
        return [deepcopy(item) for item, _raw in _legal_pairs(state, game)]

    @staticmethod
    def _base_delta(state: dict[str, Any], kind: str) -> dict[str, Any]:
        return {
            "kind": kind,
            "deal_number": int(state.get("deal_number", 0)),
            "phase": str(state.get("phase", "waiting")),
            "turn_player_id": state.get("turn_player_id"),
            "level_rank": str(state.get("level_rank", "2")),
            "team_levels": deepcopy(state.get("team_levels", {})),
            "hand_counts": {
                player_id: len(state.get("hands", {}).get(player_id, []))
                for player_id in state.get("participant_order", [])
            },
            "finish_order": list(state.get("finish_order", [])),
        }

    @classmethod
    def apply_action(cls, state: dict[str, Any], player_id: str, action_id: str) -> dict[str, Any]:
        game = _deserialize_game(state)
        if _participant_id(state, game.round.current_player) != player_id:
            raise ValueError("尚未轮到该玩家")
        pair = next(
            ((item, raw) for item, raw in _legal_pairs(state, game) if item["action_id"] == action_id),
            None,
        )
        if pair is None:
            raise ValueError("action_id 不在上游规则核心当前 legal_actions 中")
        descriptor, raw = pair
        action_level = str(state["level_rank"])
        before_deal = int(game.game_count)
        before_greater = game.round.greater_player.player_id if game.round.greater_player is not None else None
        selected = [
            _card_dict(card)
            for card in _physical_cards_for_action(game.players[game.round.current_player], raw)
        ]

        game.step(raw)
        _ensure_card_ids(game)
        state["turn_serial"] = int(state.get("turn_serial", 0)) + 1
        kind = str(descriptor["kind"])
        deal_advanced = int(game.game_count) != before_deal

        if kind == "play":
            state.setdefault("played_cards", []).extend(deepcopy(selected))
            state["trick"] = state.get("trick") or {"number": 1, "leader_player_id": player_id}
            state["trick"].update({
                "last_play": {"player_id": player_id, "cards": deepcopy(selected), "pattern": deepcopy(descriptor["pattern"])},
                "pass_player_ids": [],
                "wind_follow": False,
            })
        elif kind == "pass":
            trick = state.get("trick") or {}
            passed = list(trick.get("pass_player_ids", []))
            if player_id not in passed:
                passed.append(player_id)
            trick["pass_player_ids"] = passed
            state["trick"] = trick
        elif kind == "wind_follow":
            state["trick"] = {
                "number": int((state.get("trick") or {}).get("number", 1)) + 1,
                "leader_player_id": _participant_id(state, game.round.current_player),
                "last_play": None,
                "pass_player_ids": [],
                "wind_follow": True,
            }

        summary = _deal_summary(state, game)
        if summary and not any(
            item.get("deal_number") == summary["deal_number"] for item in state.get("deal_history", [])
        ):
            state.setdefault("deal_history", []).append(deepcopy(summary))

        if deal_advanced:
            state["played_cards"] = []
            next_id = _participant_id(state, game.round.current_player)
            state["trick"] = {
                "number": 1,
                "leader_player_id": next_id,
                "last_play": None,
                "pass_player_ids": [],
                "wind_follow": False,
            }

        _sync_state(state, game)
        if kind == "pass" and before_greater is not None:
            next_seat = game.round.current_player
            if next_seat == before_greater and game.players[next_seat].current_hand:
                state["trick"] = {
                    "number": int((state.get("trick") or {}).get("number", 1)) + 1,
                    "leader_player_id": _participant_id(state, next_seat),
                    "last_play": None,
                    "pass_player_ids": [],
                    "wind_follow": False,
                }

        record: dict[str, Any] = {"kind": kind, "player_id": player_id, "level_rank": action_level}
        if selected:
            if kind == "play":
                record.update({"cards": deepcopy(selected), "pattern": deepcopy(descriptor["pattern"])})
            else:
                record["card"] = deepcopy(selected[0])
        if summary and int(summary["deal_number"]) == before_deal:
            record["deal_end"] = deepcopy(summary)

        delta = cls._base_delta(state, kind)
        delta["player_id"] = player_id
        if kind == "play":
            delta.update({
                "cards": deepcopy(selected),
                "pattern": deepcopy(descriptor["pattern"]),
                "went_out": player_id in state.get("finish_order", []),
            })
        elif kind == "pass":
            delta["trick_end"] = (state.get("trick") or {}).get("last_play") is None
        elif kind == "wind_follow":
            delta.update({"wind_follow": True, "next_leader_player_id": state.get("turn_player_id")})
        else:
            delta["card"] = deepcopy(selected[0])
            delta["tribute"] = cls.public_tribute(state)
        if summary and int(summary["deal_number"]) == before_deal:
            delta.update({
                "deal_end": deepcopy(summary),
                "tribute": cls.public_tribute(state),
                "match_result": deepcopy(state.get("match_result")),
            })

        note = cls._note(kind, descriptor, selected, state, summary)
        state["last_action"] = deepcopy(record)
        state.setdefault("action_history", []).append(deepcopy(record))
        state["last_public_delta"] = deepcopy(delta)
        state["last_action_note"] = note
        state["engine_blob"] = _serialize_game(game)
        return {"record": record, "public_delta": delta, "note": note}

    @staticmethod
    def _note(
        kind: str,
        descriptor: dict[str, Any],
        cards: list[dict[str, Any]],
        state: dict[str, Any],
        summary: dict[str, Any] | None,
    ) -> str:
        if summary is not None:
            return (
                f"第 {summary['deal_number']} 副结束，{summary['winner_team']} 队"
                f"升级 {summary['level_gain']} 级。"
            )
        if kind == "play":
            return f"打出{descriptor['pattern_label']}（{len(cards)} 张）。"
        if kind == "pass":
            return "过。"
        if kind == "wind_follow":
            return f"接风，由 {state.get('turn_player_id')} 领出。"
        if kind == "tribute":
            return f"{card_label(cards[0])}进贡。"
        return f"{card_label(cards[0])}还贡。"

    @staticmethod
    def public_tribute(state: dict[str, Any]) -> dict[str, Any]:
        return deepcopy(state.get("tribute") or {"status": "none", "mode": "none"})

    @classmethod
    def assert_card_conservation(cls, state: dict[str, Any]) -> None:
        if int(state.get("deal_number", 0)) < 1 or state.get("phase") == "finished":
            return
        cards = [card for hand in state.get("hands", {}).values() for card in hand]
        cards.extend(state.get("played_cards", []))
        for offering in (state.get("tribute") or {}).get("tributes", []):
            if "receiver_player_id" not in offering:
                cards.append(offering["card"])
        ids = [str(card["id"]) for card in cards]
        if len(ids) != 108 or len(set(ids)) != 108:
            raise ValueError("上游当前副牌不满足 108 张物理牌守恒")

    # Test/debug hooks expose the actual upstream object without adding any
    # alternate rule path. Production callers use legal_actions/apply_action.
    _load_game = staticmethod(_deserialize_game)

    @staticmethod
    def _store_game(state: dict[str, Any], game: GuandanGame) -> None:
        _sync_state(state, game)
        state["engine_blob"] = _serialize_game(game)
