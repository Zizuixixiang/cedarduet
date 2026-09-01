from __future__ import annotations

import hashlib
import json
import random
from copy import deepcopy
from typing import Any

from .base import GamePlugin, MoveResult
from .tools import (
    advance_flow,
    discard_cards,
    draw_cards,
    ensure_card_zones,
    ensure_flow,
    public_card_state,
)


SUITS = ("spades", "hearts", "clubs", "diamonds")
SUIT_CODES = {
    "spades": "S",
    "hearts": "H",
    "clubs": "C",
    "diamonds": "D",
}
SUIT_LABELS = {
    "spades": "黑桃",
    "hearts": "红桃",
    "clubs": "梅花",
    "diamonds": "方块",
}
RANKS = ("A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K")
MAX_ACTIONS = 10_000


def build_train_deck() -> list[dict[str, str]]:
    """Return one standard 54-card deck with stable physical identities."""
    deck = [
        {
            "id": f"{SUIT_CODES[suit]}{rank}",
            "suit": suit,
            "rank": rank,
        }
        for suit in SUITS
        for rank in RANKS
    ]
    deck.extend((
        {"id": "JOKER-S", "suit": "joker", "rank": "small_joker"},
        {"id": "JOKER-B", "suit": "joker", "rank": "big_joker"},
    ))
    return deck


def matching_rank(card: dict[str, Any]) -> str:
    """Normalize both physical jokers to this edition's shared 王 rank."""
    rank = str(card.get("rank", ""))
    if rank in {"small_joker", "big_joker"}:
        return "joker"
    if rank not in RANKS:
        raise ValueError("牌张包含未知点数")
    return rank


def card_label(card: dict[str, Any]) -> str:
    rank = str(card.get("rank", ""))
    if rank == "small_joker":
        return "小王"
    if rank == "big_joker":
        return "大王"
    suit = str(card.get("suit", ""))
    if rank not in RANKS or suit not in SUIT_LABELS:
        raise ValueError("牌张包含未知点数或花色")
    return f"{SUIT_LABELS[suit]}{rank}"


class TrainCards(GamePlugin):
    game_type = "train_cards"
    display_name = "开火车"
    category = "card"
    min_players = 2
    max_players = 6
    allowed_player_counts = (2, 3, 4, 5, 6)
    recommended_players = 4
    supports_npcs = True
    uses_local_npc_strategy = True
    supports_stakes = True
    supports_multiplayer_stakes = True
    uses_custom_stake_settlement = True
    mcp_immediate_public_events = True
    rules_text = (
        "【本项目采用版本】\n"
        "开火车（也叫拉大车、小猫钓鱼）各地规则并不统一：常见资料在收牌后是否连出、"
        "J 是否全收、是否去掉或把大小王当万能牌等处互相矛盾。本项目采用便于 2–6 人"
        "闭环轮转的 54 张严格轮流版：不采用收牌者连出和 J 全收，避免这些双人变体在"
        "多人桌改变固定座次；这只是本项目版本，不声称是唯一标准。\n\n"
        "【发牌与牌堆】\n"
        "使用一副含大小王的 54 张牌。洗牌后从先手起按座位顺时针每次发 1 张，直到"
        "全部发完，所以不能整除时前面的席位会多 1 张。每家的牌背面朝上排成有顺序的"
        "个人牌堆，不可查看或重排；只公开剩余张数。收回的牌按它们在桌面公开牌列中"
        "从早到晚的原顺序接到个人牌堆底，原牌堆出到底后会自然继续翻到此前收回的牌，"
        "不会重新洗牌。\n\n"
        "【行动、收牌与轮转】\n"
        "轮到自己时只有一个动作：由裁判翻开个人牌堆最上方 1 张，追加到桌面公开牌列"
        "末端，客户端不提交或猜测牌面。若新牌点数与牌列中已有牌相同，行动者收走从"
        "那张旧同点牌到新牌的全部牌，范围包含两端；否则不收牌。牌列在每次收牌后不会"
        "留下重复点数，因此旧同点牌至多一张。A、J、Q、K 都按自身点数匹配，J 没有"
        "额外效果。大王与小王共同算作“王”这一点数，可以互相匹配；两者都不是万能牌，"
        "也不会单独触发全收。无论是否收牌，行动权都严格交给顺时针下一名未淘汰玩家。\n\n"
        "【淘汰、循环与终局】\n"
        "一名玩家翻牌并完成可能的收牌后若个人牌堆为空，立即淘汰，之后轮转跳过该席位。"
        "只剩一名未淘汰玩家时该玩家获胜，桌面遗留牌不再处理。由于本游戏完全由初始"
        "洗牌决定，若行动权、未淘汰席位、桌面牌列及所有个人牌堆顺序组成的完整局面"
        "再次出现，后续必然无限循环，立即判平局；另设 10000 次翻牌安全上限，达到也"
        "判平局。\n\n"
        "【CedarDuet 娱乐筹码】\n"
        "房间底注按每名败者分别结算：非平局时每名败者扣一份底注，唯一赢家获得 "
        "(人数-1) 份底注，二人桌和 3–6 人桌均采用这一明确的零和规则。完整局面循环或"
        "达到动作上限造成的平局，所有参与者均结算 0。"
    )
    move_format = (
        '翻牌：{"move":{"action":"flip"}}。'
        "只原样选择 private_state.legal_actions；牌面由裁判翻开并通过公开增量发布。"
    )

    def __init__(self, rng: random.Random | None = None) -> None:
        self._rng = rng or random.SystemRandom()

    @staticmethod
    def _state_skeleton() -> dict[str, Any]:
        state: dict[str, Any] = {
            "board_kind": "train_cards",
            "participant_order": [],
            "active_player_ids": [],
            "eliminated_player_ids": [],
            "turn_player_id": None,
            "cards": None,
            "action_history": [],
            "last_action": None,
            "last_collection": None,
            "winner_player_id": None,
            "draw_reason": None,
            "seen_position_hashes": [],
        }
        ensure_flow(state, phase="playing")
        return state

    def initial_state(self) -> dict[str, Any]:
        return self._state_skeleton()

    def tokens_for(self, participants: list[dict[str, Any]]) -> list[str]:
        return [f"P{index + 1}" for index, _item in enumerate(participants)]

    def initialize(self, participants: list[dict[str, Any]]) -> dict[str, Any]:
        ordered = sorted(participants, key=lambda item: item.get("seat_index", 0))
        if not ordered:
            raise ValueError("开火车至少需要一个等待席位")
        return self.initialize_for_first_player(
            participants, str(ordered[0]["player_id"])
        )

    def initialize_for_first_player(
        self,
        participants: list[dict[str, Any]],
        first_player_id: str,
    ) -> dict[str, Any]:
        if not 1 <= len(participants) <= 6:
            raise ValueError("开火车只支持 2–6 人，等待房允许先创建 1 个席位")
        ordered = sorted(participants, key=lambda item: item.get("seat_index", 0))
        order = [str(item["player_id"]) for item in ordered]
        if first_player_id not in order:
            raise ValueError("开火车先手必须是本桌参与者")
        opener_index = order.index(first_player_id)
        deal_order = order[opener_index:] + order[:opener_index]
        state = self._state_skeleton()
        state.update({
            "participant_order": order,
            "active_player_ids": list(order),
            "turn_player_id": first_player_id,
        })
        zones = ensure_card_zones(
            state, build_train_deck(), order, rng=self._rng
        )
        while zones["deck"]:
            for player_id in deal_order:
                if not zones["deck"]:
                    break
                draw_cards(state, player_id)
        state["seen_position_hashes"] = [self._position_hash(state)]
        return state

    @staticmethod
    def _position_hash(state: dict[str, Any]) -> str:
        zones = state.get("cards") or {}
        hands = zones.get("hands") or {}
        position = {
            "turn_player_id": state.get("turn_player_id"),
            "active_player_ids": list(state.get("active_player_ids", [])),
            "table_card_ids": [card.get("id") for card in zones.get("discard", [])],
            "hand_card_ids": {
                player_id: [card.get("id") for card in hands.get(player_id, [])]
                for player_id in state.get("participant_order", [])
            },
        }
        encoded = json.dumps(
            position, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        ).encode("ascii")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _next_active_player(state: dict[str, Any], player_id: str) -> str:
        order = state["participant_order"]
        active = set(state["active_player_ids"])
        start = order.index(player_id)
        for offset in range(1, len(order) + 1):
            candidate = str(order[(start + offset) % len(order)])
            if candidate in active:
                return candidate
        raise ValueError("没有可继续行动的玩家")

    @staticmethod
    def legal_actions_for(
        state: dict[str, Any], player_id: str
    ) -> list[dict[str, str]]:
        if (
            state.get("flow", {}).get("phase") != "playing"
            or state.get("winner_player_id") is not None
            or state.get("draw_reason") is not None
            or state.get("turn_player_id") != player_id
            or player_id not in state.get("active_player_ids", [])
        ):
            return []
        hand = state.get("cards", {}).get("hands", {}).get(player_id)
        return [{"action": "flip"}] if isinstance(hand, list) and hand else []

    def validate_action(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
    ) -> None:
        if move != {"action": "flip"}:
            raise ValueError("只能原样选择服务端发布的 flip 动作")
        player_id = str(actor["player_id"])
        if move not in self.legal_actions_for(state, player_id):
            raise ValueError("该动作不在服务端权威 legal_actions 中")

    def validate_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> None:
        del state, move, mark
        raise ValueError("开火车需要 participant-aware action 接口")

    def apply_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> dict[str, Any]:
        del state, move, mark
        raise ValueError("开火车需要 participant-aware action 接口")

    @staticmethod
    def _finish(
        state: dict[str, Any],
        *,
        winner_player_id: str | None = None,
        draw_reason: str | None = None,
    ) -> dict[str, Any]:
        state["winner_player_id"] = winner_player_id
        state["draw_reason"] = draw_reason
        state["turn_player_id"] = None
        state["flow"]["phase"] = "finished"
        return {
            "winner_player_id": winner_player_id,
            "draw": winner_player_id is None,
            "reason": draw_reason or "last_player_with_cards",
        }

    def apply_action(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
    ) -> MoveResult:
        self.validate_action(state, move, actor)
        player_id = str(actor["player_id"])
        hand = state["cards"]["hands"][player_id]
        revealed = deepcopy(hand[0])
        discard_cards(state, player_id, [hand[0]])
        table = state["cards"]["discard"]
        normalized = matching_rank(revealed)
        match_index = next(
            (
                index for index in range(len(table) - 2, -1, -1)
                if matching_rank(table[index]) == normalized
            ),
            None,
        )
        collected: list[dict[str, Any]] = []
        if match_index is not None:
            collected = deepcopy(table[match_index:])
            del table[match_index:]
            hand.extend(deepcopy(collected))

        eliminated = not hand
        if eliminated:
            state["active_player_ids"].remove(player_id)
            state["eliminated_player_ids"].append(player_id)
        advance_flow(state)
        record = {
            "action": "flip",
            "player_id": player_id,
            "revealed_card": revealed,
            "matched_rank": normalized if collected else None,
            "collected_cards": collected,
            "collected_count": len(collected),
            "eliminated_player_id": player_id if eliminated else None,
        }
        state["last_action"] = deepcopy(record)
        state["last_collection"] = (
            {
                "player_id": player_id,
                "rank": normalized,
                "cards": deepcopy(collected),
                "count": len(collected),
            }
            if collected else None
        )
        state["action_history"].append(deepcopy(record))

        result = None
        next_player_id = None
        draw_note = ""
        if len(state["active_player_ids"]) == 1:
            winner = str(state["active_player_ids"][0])
            result = self._finish(state, winner_player_id=winner)
        else:
            next_player_id = self._next_active_player(state, player_id)
            state["turn_player_id"] = next_player_id
            position_hash = self._position_hash(state)
            seen = state.setdefault("seen_position_hashes", [])
            if position_hash in seen:
                result = self._finish(state, draw_reason="repeated_position")
                next_player_id = None
                draw_note = "完整局面重复，后续必然循环，判为平局。"
            elif int(state["flow"].get("turn_number", 0)) >= MAX_ACTIONS:
                result = self._finish(state, draw_reason="action_limit")
                next_player_id = None
                draw_note = f"达到 {MAX_ACTIONS} 次翻牌安全上限，判为平局。"
            else:
                seen.append(position_hash)

        if collected:
            note = f"翻出{card_label(revealed)}，同点收回 {len(collected)} 张。"
        else:
            note = f"翻出{card_label(revealed)}，未触发收牌。"
        if eliminated:
            note += " 个人牌堆已空，本局淘汰。"
        if result and result.get("winner_player_id"):
            note += f" 只剩 {result['winner_player_id']}，获得胜利。"
        elif draw_note:
            note += f" {draw_note}"
        return MoveResult(
            state=state,
            next_player_id=next_player_id,
            participant_activity={player_id: "eliminated"} if eliminated else {},
            note=note,
            result=result,
        )

    def progress_after_action(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
        participants: list[dict[str, Any]],
        applied: dict[str, Any] | MoveResult,
    ) -> dict[str, Any] | MoveResult:
        del state, move, actor
        if not isinstance(applied, MoveResult):
            return applied
        public = self.public_state(applied.state, participants)
        last_action = public["last_action"]
        delta: dict[str, Any] = {
            "revealed_card": deepcopy(last_action["revealed_card"]),
        }
        if public["winner_player_id"] is not None:
            delta["winner_player_id"] = public["winner_player_id"]
        if public["draw_reason"] is not None:
            delta["draw_reason"] = public["draw_reason"]
        applied.public_event = {
            "train_cards_delta": delta
        }
        return applied

    def public_state(
        self,
        state: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return self._project_public_state(
            state,
            participants,
            terminal=state.get("flow", {}).get("phase") == "finished",
        )

    def terminal_public_state(
        self,
        state: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return self._project_public_state(state, participants, terminal=True)

    def _project_public_state(
        self,
        state: dict[str, Any],
        participants: list[dict[str, Any]],
        *,
        terminal: bool,
    ) -> dict[str, Any]:
        del participants
        cards = public_card_state(state)
        projected = {
            "board_kind": "train_cards",
            "flow": deepcopy(state["flow"]),
            "table_cards": cards["discard"],
            "hand_counts": cards["hand_counts"],
            "current_player_id": state.get("turn_player_id"),
            "active_player_ids": list(state.get("active_player_ids", [])),
            "eliminated_player_ids": list(state.get("eliminated_player_ids", [])),
            "last_action": deepcopy(state.get("last_action")),
            "last_collection": deepcopy(state.get("last_collection")),
            "winner_player_id": state.get("winner_player_id"),
            "draw_reason": state.get("draw_reason"),
            "last_action_note": state.get("last_action_note", ""),
        }
        if terminal:
            hands = state.get("cards", {}).get("hands", {})
            projected["terminal_hands"] = {
                player_id: deepcopy(hands.get(player_id, []))
                for player_id in state.get("participant_order", [])
            }
        return projected

    def private_state(
        self,
        state: dict[str, Any],
        viewer: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any]:
        del participants
        return {
            "legal_actions": self.legal_actions_for(
                state, str(viewer["player_id"])
            )
        }

    def participant_summary(
        self,
        state: dict[str, Any],
        participant: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, int]:
        del participants
        return {
            "hand_count": int(
                state.get("hand_counts", {}).get(participant["player_id"], 0)
            )
        }

    def result_for(
        self,
        state: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        del participants
        winner = state.get("winner_player_id")
        if winner is not None:
            return {
                "winner_player_id": winner,
                "draw": False,
                "reason": "last_player_with_cards",
            }
        draw_reason = state.get("draw_reason")
        return {"draw": True, "reason": draw_reason} if draw_reason else None

    def settlement_deltas(
        self,
        state: dict[str, Any],
        result: dict[str, Any],
        participants: list[dict[str, Any]],
        stake: int,
    ) -> dict[str, int]:
        del state
        player_ids = [str(item["player_id"]) for item in participants]
        if result.get("draw"):
            return {player_id: 0 for player_id in player_ids}
        winner = result.get("winner_player_id")
        if winner not in player_ids:
            raise ValueError("开火车终局缺少有效唯一赢家")
        return {
            player_id: stake * (len(player_ids) - 1)
            if player_id == winner else -stake
            for player_id in player_ids
        }

    def check_winner(self, state: dict[str, Any]) -> str | None:
        del state
        return None

    def npc_compact_rules(
        self,
        state: dict[str, Any],
        actor: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> str:
        del state, actor, participants
        return "开火车只有服务端发布的 flip 动作；牌面和收牌均由裁判决定。"

    def npc_public_actions(
        self,
        state: dict[str, Any],
        actor: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        del actor, participants
        return deepcopy(state.get("action_history", [])[-24:])

    def npc_legal_actions(
        self,
        state: dict[str, Any],
        actor: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        del participants
        return self.legal_actions_for(state, str(actor["player_id"]))

    def choose_local_npc_action(
        self,
        state: dict[str, Any],
        actor: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        actions = self.npc_legal_actions(state, actor, participants)
        return deepcopy(actions[0]) if actions else None

    def format_action(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
    ) -> str:
        del state, actor
        return "翻下一张" if move == {"action": "flip"} else str(move)

    def format_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> str:
        del state, mark
        return str(move.get("action", "flip"))
