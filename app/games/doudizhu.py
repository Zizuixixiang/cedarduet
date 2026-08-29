from __future__ import annotations

import random
from copy import deepcopy
from typing import Any

from third_party.onestraw_doudizhu import (
    RANK_LABELS,
    RANK_INDEX,
    classify_ranks,
    legal_rank_plays,
    pattern_from_public,
)

from .base import GamePlugin, MoveResult
from .tools import (
    discard_cards,
    draw_cards,
    ensure_card_zones,
    ensure_flow,
    private_hand,
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
NORMAL_RANKS = RANK_LABELS[:13]
def build_deck() -> list[dict[str, str]]:
    deck = [
        {"id": f"{SUIT_CODES[suit]}{rank}", "suit": suit, "rank": rank}
        for suit in SUITS
        for rank in NORMAL_RANKS
    ]
    deck.extend((
        {"id": "JOKER-S", "suit": "joker", "rank": "small_joker"},
        {"id": "JOKER-B", "suit": "joker", "rank": "big_joker"},
    ))
    return deck


def _card_sort_key(card: dict[str, Any]) -> tuple[int, int, str]:
    rank = str(card.get("rank"))
    if rank not in RANK_INDEX:
        raise ValueError("牌张包含未知点数")
    suit = str(card.get("suit"))
    suit_value = SUITS.index(suit) if suit in SUITS else len(SUITS)
    return RANK_INDEX[rank], suit_value, str(card.get("id", ""))


def card_label(card: dict[str, Any]) -> str:
    rank = str(card.get("rank"))
    if rank == "small_joker":
        return "小王"
    if rank == "big_joker":
        return "大王"
    suit = str(card.get("suit"))
    if rank not in NORMAL_RANKS or suit not in SUIT_LABELS:
        raise ValueError("牌张包含未知点数或花色")
    return f"{SUIT_LABELS[suit]}{rank}"


class Doudizhu(GamePlugin):
    game_type = "doudizhu"
    display_name = "斗地主"
    category = "card"
    min_players = 3
    max_players = 3
    allowed_player_counts = (3,)
    recommended_players = 3
    supports_npcs = True
    supports_stakes = False
    mcp_immediate_public_events = True
    rules_text = (
        "【牌局与叫分】\n"
        "固定三人经典 54 张版本，每人先发 17 张，另留 3 张底牌。由房间选定的首叫者"
        "开始，每人按座位顺序且仅叫一次：可叫 0 分（不叫），或叫出严格高于当前最高分"
        "的 1、2、3 分；有人叫 3 分立即结束叫分，否则三人叫完后最高者成为地主。三人"
        "全部不叫时重新洗牌发牌，并由最后一位不叫者开始新一轮叫分。地主取得 3 张底牌，"
        "定地主后底牌向全桌公开；地主先出牌，另外两人为同一农民阵营，互为对家。\n\n"
        "【牌型与压制】\n"
        "采用 onestraw/doudizhu 0.1.5 的 37 类、34,152 项牌型语义：单张、对子、三张、"
        "三带一、三带一对、至少五张顺子、至少三连对、至少二连三张的飞机，以及飞机带"
        "同数量单张或对子、四带二、四带两对、四张炸弹和双王王炸。顺子、连对和飞机主体"
        "不能含 2 或王。普通牌只有同一细分牌型且主体点数更大才能压制；炸弹可压普通牌，"
        "更大点数的炸弹可压较小炸弹，王炸最高。该核心允许把一对当作四带二的两张单牌，"
        "也允许用同点四张拆成飞机带对或四带两对中的两对。牌型解释及比较一律由服务端"
        "发布的物理牌组合裁决。\n\n"
        "【轮转与胜负】\n"
        "地主领出后依次行动；跟牌者可出能压住桌面组合的牌或过牌，引牌者不能过。一次"
        "成功出牌会清除此前的过牌状态；其余两家连续过牌后本墩结束，由最后成功出牌者"
        "重新自由领出。任一玩家手牌清空立即结束：地主先清空则地主阵营胜，否则农民阵营"
        "胜。\n\n"
        "【倍数与计分】\n"
        "局内倍数以最终叫分 1、2 或 3 为基数；每打出一次四张炸弹或王炸即乘 2，不设"
        "倍数上限，也不设春天、反春天及其他附加翻倍。第一版不接入筹码或钱包，倍数、"
        "炸弹数和胜负仅作公开局面记录，不产生筹码结算。"
    )
    move_format = (
        '叫分：原样提交自己私有状态中发布的 {"action":"bid","action_id":"bid:1",'
        '"score":1}；出牌：原样提交带 action_id、card_ids 和牌型信息的 play；过牌：'
        '{"action":"pass","action_id":"pass"}。不得自行枚举牌型。'
    )

    def __init__(self, rng: random.Random | None = None) -> None:
        self._rng = rng or random.SystemRandom()

    @staticmethod
    def _state_skeleton() -> dict[str, Any]:
        state: dict[str, Any] = {
            "board_kind": "doudizhu",
            "participant_order": [],
            "turn_player_id": None,
            "cards": None,
            "bottom_cards": [],
            "bottom_revealed": False,
            "deal_number": 0,
            "bidding": None,
            "landlord_player_id": None,
            "roles_by_player": {},
            "trick": None,
            "base_score": 0,
            "multiplier": 1,
            "bomb_count": 0,
            "play_sequence": 0,
            "action_history": [],
            "last_action": None,
            "winner_player_id": None,
            "winning_side": None,
            "winning_player_ids": [],
        }
        ensure_flow(state, phase="waiting")
        return state

    def initial_state(self) -> dict[str, Any]:
        return self._state_skeleton()

    def tokens_for(self, participants: list[dict[str, Any]]) -> list[str]:
        return [f"P{index + 1}" for index, _item in enumerate(participants)]

    def initialize(self, participants: list[dict[str, Any]]) -> dict[str, Any]:
        ordered = sorted(participants, key=lambda item: item.get("seat_index", 0))
        if not ordered:
            raise ValueError("斗地主至少需要一个等待席位")
        return self.initialize_for_first_player(
            participants, str(ordered[0]["player_id"])
        )

    def initialize_for_first_player(
        self,
        participants: list[dict[str, Any]],
        first_player_id: str,
    ) -> dict[str, Any]:
        if not 1 <= len(participants) <= 3:
            raise ValueError("斗地主固定三人，等待房允许先创建一至两个席位")
        ordered = sorted(participants, key=lambda item: item.get("seat_index", 0))
        order = [str(item["player_id"]) for item in ordered]
        if first_player_id not in order:
            raise ValueError("斗地主首叫者必须是本桌参与者")
        state = self._state_skeleton()
        state["participant_order"] = order
        state["roles_by_player"] = {player_id: "unassigned" for player_id in order}
        ensure_card_zones(state, build_deck(), order, rng=self._rng)
        state["turn_player_id"] = first_player_id
        if len(order) == 3:
            self._deal_round(state, first_player_id, bidding_round=1, deal_number=1)
        return state

    def _deal_round(
        self,
        state: dict[str, Any],
        opener_player_id: str,
        *,
        bidding_round: int,
        deal_number: int,
    ) -> None:
        order = list(state["participant_order"])
        state["cards"] = None
        ensure_card_zones(state, build_deck(), order, rng=self._rng)
        for _round in range(17):
            for player_id in order:
                draw_cards(state, player_id)
        state["bottom_cards"] = deepcopy(state["cards"]["deck"])
        state["cards"]["deck"] = []
        if len(state["bottom_cards"]) != 3:
            raise RuntimeError("斗地主发牌后必须保留三张底牌")
        state.update({
            "bottom_revealed": False,
            "deal_number": deal_number,
            "bidding": {
                "round": bidding_round,
                "opener_player_id": opener_player_id,
                "highest_score": 0,
                "highest_bidder_id": None,
                "actions": [],
                "acted_player_ids": [],
            },
            "landlord_player_id": None,
            "roles_by_player": {player_id: "unassigned" for player_id in order},
            "trick": None,
            "base_score": 0,
            "multiplier": 1,
            "bomb_count": 0,
            "play_sequence": 0,
            "winner_player_id": None,
            "winning_side": None,
            "winning_player_ids": [],
            "turn_player_id": opener_player_id,
        })
        state["flow"].update({
            "phase": "bidding",
            "round_number": bidding_round,
            "turn_number": 0,
        })

    @staticmethod
    def _new_trick(number: int, leader_player_id: str) -> dict[str, Any]:
        return {
            "number": number,
            "leader_player_id": leader_player_id,
            "last_play": None,
            "pass_player_ids": [],
        }

    @staticmethod
    def _next_player(state: dict[str, Any], player_id: str) -> str:
        order = state["participant_order"]
        return str(order[(order.index(player_id) + 1) % len(order)])

    @staticmethod
    def _cards_for_ids(
        state: dict[str, Any], player_id: str, card_ids: list[str]
    ) -> list[dict[str, Any]]:
        hands = state.get("cards", {}).get("hands", {})
        hand = hands.get(player_id)
        if not isinstance(hand, list):
            raise ValueError("行动者不在本局手牌区")
        by_id = {str(card.get("id")): card for card in hand}
        if len(by_id) != len(hand):
            raise ValueError("持久化手牌 ID 不唯一")
        try:
            return [by_id[card_id] for card_id in card_ids]
        except KeyError as exc:
            raise ValueError("所选牌不全在行动者手中") from exc

    @staticmethod
    def _physical_cards_for_ranks(
        hand: list[dict[str, Any]], ranks: tuple[int, ...]
    ) -> list[dict[str, Any]]:
        by_rank: dict[int, list[dict[str, Any]]] = {}
        for card in sorted(hand, key=_card_sort_key):
            by_rank.setdefault(RANK_INDEX[str(card["rank"])], []).append(card)
        required: dict[int, int] = {}
        for rank in ranks:
            required[rank] = required.get(rank, 0) + 1
        cards: list[dict[str, Any]] = []
        for rank in sorted(required):
            cards.extend(by_rank.get(rank, [])[:required[rank]])
        if len(cards) != len(ranks):
            raise RuntimeError("规则核心发布了手牌中不存在的点数组合")
        return cards

    @classmethod
    def _play_actions_for(
        cls, state: dict[str, Any], player_id: str
    ) -> list[dict[str, Any]]:
        hand = state["cards"]["hands"].get(player_id, [])
        target_value = (state.get("trick") or {}).get("last_play")
        target = None
        if isinstance(target_value, dict):
            target = pattern_from_public(target_value["pattern"])
        actions: list[dict[str, Any]] = []
        for rank_play in legal_rank_plays(
            (str(card["rank"]) for card in hand), target
        ):
            cards = cls._physical_cards_for_ranks(hand, rank_play.ranks)
            card_ids = [str(card["id"]) for card in cards]
            pattern = rank_play.pattern
            actions.append({
                "action": "play",
                "action_id": (
                    f"play:{pattern.type_code}:{pattern.main_value}:"
                    + "-".join(card_ids)
                ),
                "card_ids": card_ids,
                "pattern_type": pattern.type_code,
                "pattern_label": pattern.label,
                "main_rank": pattern.main_rank,
            })
        return actions

    @classmethod
    def legal_actions_for(
        cls, state: dict[str, Any], player_id: str
    ) -> list[dict[str, Any]]:
        if (
            state.get("winner_player_id") is not None
            or state.get("turn_player_id") != player_id
        ):
            return []
        phase = state.get("flow", {}).get("phase")
        if phase == "bidding":
            bidding = state.get("bidding") or {}
            if player_id in bidding.get("acted_player_ids", []):
                return []
            scores = [0] + list(range(int(bidding.get("highest_score", 0)) + 1, 4))
            return [
                {
                    "action": "bid",
                    "action_id": f"bid:{score}",
                    "score": score,
                    "label": "不叫" if score == 0 else f"{score} 分",
                }
                for score in scores
            ]
        if phase != "playing":
            return []
        actions = cls._play_actions_for(state, player_id)
        if (state.get("trick") or {}).get("last_play") is not None:
            actions.append({"action": "pass", "action_id": "pass"})
        return actions

    @classmethod
    def _resolve_action(
        cls,
        state: dict[str, Any],
        player_id: str,
        move: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(move, dict):
            raise ValueError("move 必须是对象")
        legal = cls.legal_actions_for(state, player_id)
        action_id = move.get("action_id")
        if isinstance(action_id, str):
            matches = [action for action in legal if action["action_id"] == action_id]
        elif move.get("action") == "bid":
            matches = [
                action for action in legal
                if action["action"] == "bid" and action["score"] == move.get("score")
            ]
        elif move.get("action") == "pass":
            matches = [action for action in legal if action["action"] == "pass"]
        elif move.get("action") == "play" and isinstance(move.get("card_ids"), list):
            requested_ids = list(move["card_ids"])
            matches = [
                action for action in legal
                if action["action"] == "play"
                and action["card_ids"] == requested_ids
                and (
                    "pattern_type" not in move
                    or action["pattern_type"] == move["pattern_type"]
                )
            ]
        else:
            matches = []
        if len(matches) != 1:
            raise ValueError("行动必须原样匹配服务端当前发布的一项合法动作")
        resolved = matches[0]
        if set(move) - set(resolved):
            raise ValueError("行动包含服务端未发布的字段")
        if move.get("action") != resolved["action"]:
            raise ValueError("action 与 action_id 不一致")
        for key, value in move.items():
            if key in resolved and value != resolved[key]:
                raise ValueError("客户端动作内容与服务端发布值不一致")
        return resolved

    def validate_action(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
    ) -> None:
        if state.get("flow", {}).get("phase") == "finished":
            raise ValueError("对局已经结束")
        player_id = str(actor["player_id"])
        if player_id not in state.get("participant_order", []):
            raise ValueError("行动者不在本局参与者中")
        if state.get("turn_player_id") != player_id:
            raise ValueError("当前行动权属于另一名参与者")
        self._resolve_action(state, player_id, move)

    def validate_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> None:
        del state, move, mark
        raise ValueError("斗地主需要 participant-aware action 接口")

    def apply_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> dict[str, Any]:
        del state, move, mark
        raise ValueError("斗地主需要 participant-aware action 接口")

    def _finalize_landlord(
        self, state: dict[str, Any], landlord_player_id: str, score: int
    ) -> None:
        state["cards"]["hands"][landlord_player_id].extend(
            deepcopy(state["bottom_cards"])
        )
        state["bottom_revealed"] = True
        state["landlord_player_id"] = landlord_player_id
        state["roles_by_player"] = {
            player_id: "landlord" if player_id == landlord_player_id else "farmer"
            for player_id in state["participant_order"]
        }
        state["base_score"] = score
        state["multiplier"] = score
        state["trick"] = self._new_trick(1, landlord_player_id)
        state["turn_player_id"] = landlord_player_id
        state["flow"].update({
            "phase": "playing",
            "round_number": 1,
            "turn_number": 0,
        })

    def _apply_bid(
        self,
        state: dict[str, Any],
        action: dict[str, Any],
        actor: dict[str, Any],
    ) -> MoveResult:
        player_id = str(actor["player_id"])
        score = int(action["score"])
        bidding = state["bidding"]
        bid_record = {
            "action": "bid",
            "player_id": player_id,
            "score": score,
            "bidding_round": int(bidding["round"]),
            "deal_number": int(state["deal_number"]),
        }
        bidding["actions"].append(deepcopy(bid_record))
        bidding["acted_player_ids"].append(player_id)
        if score > int(bidding["highest_score"]):
            bidding["highest_score"] = score
            bidding["highest_bidder_id"] = player_id
        state["flow"]["turn_number"] = int(state["flow"].get("turn_number", 0)) + 1

        bidding_complete = score == 3 or len(bidding["acted_player_ids"]) == 3
        if bidding_complete and int(bidding["highest_score"]) > 0:
            landlord_player_id = str(bidding["highest_bidder_id"])
            winning_score = int(bidding["highest_score"])
            self._finalize_landlord(state, landlord_player_id, winning_score)
            bid_record.update({
                "landlord_decided": True,
                "landlord_player_id": landlord_player_id,
                "winning_score": winning_score,
            })
            state["action_history"].append(deepcopy(bid_record))
            state["last_action"] = deepcopy(bid_record)
            return MoveResult(
                state=state,
                next_player_id=landlord_player_id,
                note=f"叫 {score} 分；{landlord_player_id} 以 {winning_score} 分成为地主。",
            )

        if bidding_complete:
            next_round = int(bidding["round"]) + 1
            next_deal = int(state["deal_number"]) + 1
            state["action_history"].append(deepcopy(bid_record))
            state["action_history"].append({
                "action": "all_pass_redeal",
                "player_id": player_id,
                "completed_bidding_round": int(bidding["round"]),
                "next_bidding_round": next_round,
                "next_deal_number": next_deal,
            })
            self._deal_round(
                state,
                player_id,
                bidding_round=next_round,
                deal_number=next_deal,
            )
            bid_record.update({
                "all_pass_redeal": True,
                "next_bidding_round": next_round,
                "next_deal_number": next_deal,
            })
            state["last_action"] = deepcopy(bid_record)
            return MoveResult(
                state=state,
                retain_turn=True,
                note="三人均不叫，已重新洗牌发牌；最后不叫者开始新一轮叫分。",
            )

        next_player = self._next_player(state, player_id)
        state["turn_player_id"] = next_player
        state["action_history"].append(deepcopy(bid_record))
        state["last_action"] = deepcopy(bid_record)
        return MoveResult(
            state=state,
            next_player_id=next_player,
            note="不叫。" if score == 0 else f"叫 {score} 分。",
        )

    def _apply_play(
        self,
        state: dict[str, Any],
        action: dict[str, Any],
        actor: dict[str, Any],
    ) -> MoveResult:
        player_id = str(actor["player_id"])
        cards = self._cards_for_ids(state, player_id, action["card_ids"])
        patterns = classify_ranks(str(card["rank"]) for card in cards)
        pattern = next(
            (
                candidate for candidate in patterns
                if candidate.type_code == action["pattern_type"]
                and candidate.main_rank == action["main_rank"]
            ),
            None,
        )
        if pattern is None:
            raise ValueError("服务端规则核心无法复核已发布牌型")
        discard_cards(state, player_id, cards)
        state["play_sequence"] = int(state.get("play_sequence", 0)) + 1
        play = {
            "sequence": state["play_sequence"],
            "player_id": player_id,
            "cards": deepcopy(sorted(cards, key=_card_sort_key)),
            "pattern": pattern.public(),
        }
        trick = state["trick"]
        trick["last_play"] = deepcopy(play)
        trick["pass_player_ids"] = []
        if pattern.is_bomb:
            state["bomb_count"] = int(state.get("bomb_count", 0)) + 1
            state["multiplier"] = int(state.get("multiplier", 1)) * 2
        state["flow"]["turn_number"] = int(state["flow"].get("turn_number", 0)) + 1
        record = {
            "action": "play",
            "trick": int(trick["number"]),
            **deepcopy(play),
            "multiplier": int(state["multiplier"]),
            "bomb_count": int(state["bomb_count"]),
        }
        state["action_history"].append(deepcopy(record))
        state["last_action"] = deepcopy(record)
        if not state["cards"]["hands"][player_id]:
            landlord = str(state["landlord_player_id"])
            winning_side = "landlord" if player_id == landlord else "farmers"
            winners = (
                [landlord]
                if winning_side == "landlord"
                else [
                    candidate for candidate in state["participant_order"]
                    if candidate != landlord
                ]
            )
            state.update({
                "winner_player_id": player_id,
                "winning_side": winning_side,
                "winning_player_ids": winners,
                "turn_player_id": None,
            })
            state["flow"]["phase"] = "finished"
            return MoveResult(
                state=state,
                note=(
                    f"{pattern.label}出牌成功并清空手牌，"
                    f"{'地主' if winning_side == 'landlord' else '农民'}阵营获胜。"
                ),
                result={
                    "winner_player_id": player_id,
                    "winning_side": winning_side,
                    "winning_player_ids": deepcopy(winners),
                    "draw": False,
                },
            )
        next_player = self._next_player(state, player_id)
        state["turn_player_id"] = next_player
        return MoveResult(
            state=state,
            next_player_id=next_player,
            note=(
                f"打出{pattern.label}（{len(cards)} 张）"
                + (f"，倍率升至 {state['multiplier']} 倍。" if pattern.is_bomb else "。")
            ),
        )

    def _apply_pass(
        self, state: dict[str, Any], actor: dict[str, Any]
    ) -> MoveResult:
        player_id = str(actor["player_id"])
        trick = state["trick"]
        trick["pass_player_ids"].append(player_id)
        state["flow"]["turn_number"] = int(state["flow"].get("turn_number", 0)) + 1
        last_player = str(trick["last_play"]["player_id"])
        record = {
            "action": "pass",
            "player_id": player_id,
            "trick": int(trick["number"]),
            "trick_ended": False,
        }
        other_players = {
            candidate for candidate in state["participant_order"]
            if candidate != last_player
        }
        if set(trick["pass_player_ids"]) >= other_players:
            completed_number = int(trick["number"])
            record.update({
                "trick_ended": True,
                "pass_player_ids": list(trick["pass_player_ids"]),
                "next_leader_player_id": last_player,
            })
            state["action_history"].append(deepcopy(record))
            state["trick"] = self._new_trick(completed_number + 1, last_player)
            state["turn_player_id"] = last_player
            state["flow"].update({
                "round_number": completed_number + 1,
                "turn_number": 0,
            })
            state["last_action"] = deepcopy(record)
            return MoveResult(
                state=state,
                next_player_id=last_player,
                note="其余两家均过牌，由最后出牌者重新领出。",
            )
        next_player = self._next_player(state, player_id)
        state["turn_player_id"] = next_player
        state["action_history"].append(deepcopy(record))
        state["last_action"] = deepcopy(record)
        return MoveResult(state=state, next_player_id=next_player, note="过。")

    def apply_action(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
    ) -> MoveResult:
        self.validate_action(state, move, actor)
        action = self._resolve_action(state, str(actor["player_id"]), move)
        if action["action"] == "bid":
            return self._apply_bid(state, action, actor)
        if action["action"] == "play":
            return self._apply_play(state, action, actor)
        return self._apply_pass(state, actor)

    def progress_after_action(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
        participants: list[dict[str, Any]],
        applied: dict[str, Any] | MoveResult,
    ) -> dict[str, Any] | MoveResult:
        del state, move, actor, participants
        if not isinstance(applied, MoveResult):
            return applied
        action = applied.state.get("last_action")
        if not isinstance(action, dict):
            return applied
        card_state = public_card_state(applied.state)
        delta: dict[str, Any] = {
            "action": action["action"],
            "player_id": action.get("player_id"),
            "next_actor_player_id": applied.state.get("turn_player_id"),
            "hand_counts": card_state["hand_counts"],
            "deal_number": int(applied.state.get("deal_number", 0)),
        }
        if action["action"] == "bid":
            delta.update({
                "score": int(action["score"]),
                "bidding": deepcopy(applied.state.get("bidding")),
                "all_pass_redeal": bool(action.get("all_pass_redeal", False)),
                "landlord_decided": bool(action.get("landlord_decided", False)),
            })
            if action.get("landlord_decided"):
                delta.update({
                    "landlord_player_id": applied.state["landlord_player_id"],
                    "roles_by_player": deepcopy(applied.state["roles_by_player"]),
                    "bottom_cards": deepcopy(applied.state["bottom_cards"]),
                    "base_score": int(applied.state["base_score"]),
                    "multiplier": int(applied.state["multiplier"]),
                })
        elif action["action"] == "play":
            delta.update({
                key: deepcopy(action[key])
                for key in (
                    "trick", "sequence", "cards", "pattern", "multiplier",
                    "bomb_count",
                )
            })
            if applied.state.get("winner_player_id"):
                delta.update({
                    "finished": True,
                    "winner_player_id": applied.state["winner_player_id"],
                    "winning_side": applied.state["winning_side"],
                    "winning_player_ids": deepcopy(applied.state["winning_player_ids"]),
                })
        elif action["action"] == "pass":
            delta.update({
                "trick": int(action["trick"]),
                "trick_ended": bool(action["trick_ended"]),
                "pass_player_ids": list(
                    (applied.state.get("trick") or {}).get("pass_player_ids", [])
                    if not action["trick_ended"]
                    else action.get("pass_player_ids", [])
                ),
            })
            if action.get("trick_ended"):
                delta["next_leader_player_id"] = action["next_leader_player_id"]
        applied.public_event = {"doudizhu_delta": delta}
        return applied

    def check_winner(self, state: dict[str, Any]) -> str | None:
        del state
        return None

    def result_for(
        self,
        state: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        del participants
        winner = state.get("winner_player_id")
        if not winner:
            return None
        return {
            "winner_player_id": winner,
            "winning_side": state.get("winning_side"),
            "winning_player_ids": deepcopy(state.get("winning_player_ids", [])),
            "draw": False,
        }

    def public_state(
        self,
        state: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any]:
        del participants
        card_state = public_card_state(state)
        trick = state.get("trick") or {}
        bottom_revealed = bool(state.get("bottom_revealed", False))
        return {
            "board_kind": "doudizhu",
            "flow": deepcopy(state["flow"]),
            "current_actor_player_id": state.get("turn_player_id"),
            "deal_number": int(state.get("deal_number", 0)),
            "bidding": deepcopy(state.get("bidding")),
            "landlord_player_id": state.get("landlord_player_id"),
            "roles_by_player": deepcopy(state.get("roles_by_player", {})),
            "bottom_revealed": bottom_revealed,
            "bottom_card_count": len(state.get("bottom_cards", [])),
            "bottom_cards": (
                deepcopy(state.get("bottom_cards", [])) if bottom_revealed else []
            ),
            "current_trick": {
                "number": trick.get("number"),
                "leader_player_id": trick.get("leader_player_id"),
                "last_play": deepcopy(trick.get("last_play")),
                "pass_player_ids": list(trick.get("pass_player_ids", [])),
            },
            "hand_counts": card_state["hand_counts"],
            "base_score": int(state.get("base_score", 0)),
            "multiplier": int(state.get("multiplier", 1)),
            "bomb_count": int(state.get("bomb_count", 0)),
            "winner_player_id": state.get("winner_player_id"),
            "winning_side": state.get("winning_side"),
            "winning_player_ids": deepcopy(state.get("winning_player_ids", [])),
            "last_action_note": state.get("last_action_note", ""),
        }

    def private_state(
        self,
        state: dict[str, Any],
        viewer: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any]:
        del participants
        player_id = str(viewer["player_id"])
        return {
            "hand": sorted(private_hand(state, player_id), key=_card_sort_key),
            "legal_actions": self.legal_actions_for(state, player_id),
        }

    def participant_summary(
        self,
        state: dict[str, Any],
        participant: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, str | int | None]:
        player_id = str(participant["player_id"])
        role = str(state.get("roles_by_player", {}).get(player_id, "unassigned"))
        landlord = state.get("landlord_player_id")
        partner = next(
            (
                str(item["player_id"])
                for item in participants
                if role == "farmer"
                and str(item["player_id"]) not in {player_id, str(landlord)}
            ),
            None,
        )
        return {
            "hand_count": int(state.get("hand_counts", {}).get(player_id, 0)),
            "identity": {"landlord": "地主", "farmer": "农民"}.get(role, "待定"),
            "team": "农民阵营" if role == "farmer" else "地主阵营" if role == "landlord" else "待定",
            "partner_player_id": partner,
        }

    def npc_compact_rules(
        self,
        state: dict[str, Any],
        actor: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> str:
        del state, actor, participants
        return (
            "三人斗地主，0/1/2/3 单轮递增叫分，地主取得并公开三张底牌后先出。"
            "同一细分牌型按主体点数比较，炸弹压普通牌，王炸最高；两家连续过牌后由"
            "最后出牌者重新领出。不要自行枚举、拆解或比较牌型，只能原样选择服务端"
            "提供的一项权威动作。"
        )

    def npc_public_actions(
        self,
        state: dict[str, Any],
        actor: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        del actor, participants
        return deepcopy(state.get("action_history", [])[-36:])

    def npc_legal_actions(
        self,
        state: dict[str, Any],
        actor: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        del participants
        return self.legal_actions_for(state, str(actor["player_id"]))

    def format_action(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
    ) -> str:
        action = self._resolve_action(state, str(actor["player_id"]), move)
        if action["action"] == "bid":
            return "不叫" if action["score"] == 0 else f"叫 {action['score']} 分"
        if action["action"] == "pass":
            return "过"
        cards = self._cards_for_ids(state, str(actor["player_id"]), action["card_ids"])
        labels = "、".join(card_label(card) for card in cards)
        return f"{action['pattern_label']}：{labels}"

    def format_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> str:
        del state, mark
        return str(move.get("action", "play"))
