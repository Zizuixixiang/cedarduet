from __future__ import annotations

import itertools
import random
from copy import deepcopy
from typing import Any, Iterable

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
    "joker": "王",
}
RANKS = ("3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A", "2")
SEQUENCE_RANKS = RANKS[:-1]
RANK_VALUE = {rank: index for index, rank in enumerate(RANKS)}
JOKER_RANKS = ("small_joker", "big_joker")
PATTERN_LABELS = {
    "single": "单张",
    "pair": "对子",
    "straight": "顺子",
    "consecutive_pairs": "连对",
    "three_bomb": "三炸",
    "four_bomb": "深水炸弹",
    "joker_bomb": "王炸",
}
BOMB_STRENGTH = {
    "three_bomb": 1,
    "four_bomb": 2,
    "joker_bomb": 3,
}
MAX_MULTIPLIER = 16


def build_deck() -> list[dict[str, str]]:
    """Return one canonical, uniquely identified 54-card deck."""
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


def _card_sort_key(card: dict[str, Any]) -> tuple[int, int]:
    rank = card.get("rank")
    if rank in RANK_VALUE:
        return RANK_VALUE[str(rank)], SUITS.index(str(card.get("suit")))
    if rank == "small_joker":
        return len(RANKS), 0
    if rank == "big_joker":
        return len(RANKS), 1
    raise ValueError("牌张包含未知点数或花色")


def card_label(card: dict[str, Any]) -> str:
    rank = card.get("rank")
    if rank == "small_joker":
        return "小王"
    if rank == "big_joker":
        return "大王"
    suit = str(card.get("suit"))
    if rank not in RANK_VALUE or suit not in SUIT_LABELS:
        raise ValueError("牌张包含未知点数或花色")
    return f"{SUIT_LABELS[suit]}{rank}"


def _pattern(
    pattern_type: str,
    cards: list[dict[str, Any]],
    *,
    rank: str | None = None,
    start_rank: str | None = None,
    top_rank: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "type": pattern_type,
        "label": PATTERN_LABELS[pattern_type],
        "count": len(cards),
        "is_bomb": pattern_type in BOMB_STRENGTH,
    }
    if rank is not None:
        result["rank"] = rank
        result["rank_value"] = RANK_VALUE.get(rank, len(RANKS))
    if start_rank is not None and top_rank is not None:
        result.update({
            "start_rank": start_rank,
            "top_rank": top_rank,
            "rank_value": RANK_VALUE[top_rank],
        })
    if result["is_bomb"]:
        result["bomb_strength"] = BOMB_STRENGTH[pattern_type]
    return result


def classify_cards(cards: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    """Recognize exactly the fixed-version legal card patterns."""
    selected = list(cards)
    if not selected or len(selected) != len({card.get("id") for card in selected}):
        return None
    ranks = [card.get("rank") for card in selected]
    if len(selected) == 2 and set(ranks) == set(JOKER_RANKS):
        return _pattern("joker_bomb", selected, rank="big_joker")
    if any(rank in JOKER_RANKS or rank not in RANK_VALUE for rank in ranks):
        return None

    counts = {rank: ranks.count(rank) for rank in set(ranks)}
    if len(counts) == 1:
        rank = str(ranks[0])
        pattern_type = {
            1: "single",
            2: "pair",
            3: "three_bomb",
            4: "four_bomb",
        }.get(len(selected))
        return _pattern(pattern_type, selected, rank=rank) if pattern_type else None

    if "2" in counts:
        return None
    ordered_values = sorted(RANK_VALUE[str(rank)] for rank in counts)
    consecutive = ordered_values == list(
        range(ordered_values[0], ordered_values[-1] + 1)
    )
    if not consecutive:
        return None
    ordered_ranks = [RANKS[value] for value in ordered_values]
    if len(selected) >= 3 and all(count == 1 for count in counts.values()):
        return _pattern(
            "straight",
            selected,
            start_rank=ordered_ranks[0],
            top_rank=ordered_ranks[-1],
        )
    if len(counts) >= 2 and all(count == 2 for count in counts.values()):
        return _pattern(
            "consecutive_pairs",
            selected,
            start_rank=ordered_ranks[0],
            top_rank=ordered_ranks[-1],
        )
    return None


def can_beat(candidate: dict[str, Any], target: dict[str, Any]) -> bool:
    """Apply exact-step ordinary comparison and unrestricted bomb comparison."""
    candidate_bomb = bool(candidate.get("is_bomb"))
    target_bomb = bool(target.get("is_bomb"))
    if candidate_bomb:
        if not target_bomb:
            return True
        candidate_strength = int(candidate["bomb_strength"])
        target_strength = int(target["bomb_strength"])
        if candidate_strength != target_strength:
            return candidate_strength > target_strength
        return int(candidate["rank_value"]) > int(target["rank_value"])
    if target_bomb:
        return False
    if (
        candidate.get("type") != target.get("type")
        or candidate.get("count") != target.get("count")
    ):
        return False
    pattern_type = str(candidate["type"])
    candidate_value = int(candidate["rank_value"])
    target_value = int(target["rank_value"])
    if pattern_type in {"single", "pair"} and candidate.get("rank") == "2":
        return target.get("rank") != "2"
    return candidate_value == target_value + 1


def _all_combinations(hand: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Generate every legal physical-card combination from one hand."""
    by_rank = {
        rank: sorted(
            [card for card in hand if card.get("rank") == rank],
            key=_card_sort_key,
        )
        for rank in RANKS
    }
    combinations: list[list[dict[str, Any]]] = []
    for rank in RANKS:
        cards = by_rank[rank]
        combinations.extend([[card] for card in cards])
        for count in (2, 3, 4):
            combinations.extend(
                list(group) for group in itertools.combinations(cards, count)
            )

    jokers = [card for card in hand if card.get("rank") in JOKER_RANKS]
    if {card.get("rank") for card in jokers} == set(JOKER_RANKS):
        combinations.append(sorted(jokers, key=_card_sort_key))

    available = [rank for rank in SEQUENCE_RANKS if by_rank[rank]]
    available_set = set(available)
    for start in range(len(SEQUENCE_RANKS)):
        for stop in range(start + 3, len(SEQUENCE_RANKS) + 1):
            run = SEQUENCE_RANKS[start:stop]
            if set(run) <= available_set:
                combinations.extend(
                    list(group)
                    for group in itertools.product(
                        *(by_rank[rank] for rank in run)
                    )
                )

    pair_choices = {
        rank: list(itertools.combinations(by_rank[rank], 2))
        for rank in SEQUENCE_RANKS
    }
    for start in range(len(SEQUENCE_RANKS)):
        for stop in range(start + 2, len(SEQUENCE_RANKS) + 1):
            run = SEQUENCE_RANKS[start:stop]
            if all(pair_choices[rank] for rank in run):
                for groups in itertools.product(*(pair_choices[rank] for rank in run)):
                    combinations.append([card for group in groups for card in group])
    return combinations


def _public_pattern(pattern: dict[str, Any]) -> dict[str, Any]:
    return deepcopy(pattern)


class Gandengyan(GamePlugin):
    game_type = "gandengyan"
    display_name = "干瞪眼"
    category = "card"
    min_players = 2
    max_players = 4
    allowed_player_counts = (2, 3, 4)
    recommended_players = 3
    supports_npcs = True
    supports_stakes = True
    supports_multiplayer_stakes = True
    uses_custom_stake_settlement = True
    rules_text = (
        "【牌局】\n"
        "本项目固定的四川常见 54 张版本：支持 2–4 人，使用一副含大小王的 54 张牌；"
        "首位（庄家）发 6 张，其余每人 5 张。点数顺序固定为 "
        "3<4<5<6<7<8<9<10<J<Q<K<A<2。\n\n"
        "【牌型】\n"
        "普通牌型只有单张、对子、至少 3 张连续且"
        "不含 2 的顺子、至少 2 对连续且不含 2 的连对。三张同点为三炸，四张同点为"
        "深水炸弹，双王为最高王炸；炸弹可以越级压任何普通牌，炸弹之间先按三炸、"
        "深水炸弹、王炸的强度比较，同强度再按点数比较，不要求只大一级。\n\n"
        "【跟牌】\n"
        "普通跟牌"
        "必须同牌型、同张数并且恰好高一级；单 2 可以压任意普通单张，对 2 可以压任意"
        "普通对子，2 不得进入顺子或连对。大小王不能单出；为消除地区差异，第一版"
        "明确锁定大小王不是万能赖子，只能由双王组成王炸，不采用广告牌或癞子变体。\n\n"
        "【回合】\n"
        "每墩由引牌者出任意合法牌，之后按座位依次跟牌或过；一次成功出牌会重新开始"
        "统计其他人的过牌。当最后出牌者之外的所有仍在局玩家都过牌，该墩结束，由"
        "最后成功出牌者成为下墩引牌者，并从该玩家开始按座位顺序每人摸 1 张；牌堆"
        "耗尽后，后续席位不再摸牌。\n\n"
        "【胜负与结算】\n"
        "任一玩家出完手牌立即获胜。筹码按底注、剩余手牌"
        "和倍率进行多人零和结算：每名输家承担 底注×剩余手牌张数×最终倍率 的负值，"
        "赢家获得所有负值的绝对值之和。每出现一次三炸、深水炸弹或王炸，最终倍率"
        "乘 2，最高 16 倍。不采用春天、天胡或其他地区附加翻倍。"
    )
    move_format = (
        '出牌：{"move":{"action":"play","card_ids":["S3"]},"revision":当前版本}；'
        '过牌：{"move":{"action":"pass"},"revision":当前版本}。只能从 private_state.'
        "legal_actions 中选择服务端发布的组合。"
    )

    def __init__(self, rng: random.Random | None = None) -> None:
        self._rng = rng or random.SystemRandom()

    @staticmethod
    def _state_skeleton() -> dict[str, Any]:
        state: dict[str, Any] = {
            "board_kind": "gandengyan",
            "participant_order": [],
            "turn_player_id": None,
            "cards": None,
            "trick": None,
            "multiplier": 1,
            "max_multiplier": MAX_MULTIPLIER,
            "bomb_count": 0,
            "play_sequence": 0,
            "action_history": [],
            "last_action": None,
            "winner_player_id": None,
        }
        ensure_flow(state, phase="leading")
        return state

    def initial_state(self) -> dict[str, Any]:
        return self._state_skeleton()

    def tokens_for(self, participants: list[dict[str, Any]]) -> list[str]:
        return [f"P{index + 1}" for index, _item in enumerate(participants)]

    def initialize(self, participants: list[dict[str, Any]]) -> dict[str, Any]:
        ordered = sorted(participants, key=lambda item: item.get("seat_index", 0))
        if not ordered:
            raise ValueError("干瞪眼至少需要一个等待席位")
        return self.initialize_for_first_player(
            participants, str(ordered[0]["player_id"])
        )

    def initialize_for_first_player(
        self,
        participants: list[dict[str, Any]],
        first_player_id: str,
    ) -> dict[str, Any]:
        if not 1 <= len(participants) <= 4:
            raise ValueError("干瞪眼只支持 2–4 人，等待房允许先创建 1 个席位")
        state = self._state_skeleton()
        ordered = sorted(participants, key=lambda item: item.get("seat_index", 0))
        order = [str(item["player_id"]) for item in ordered]
        if first_player_id not in order:
            raise ValueError("干瞪眼首位必须是本桌参与者")
        state["participant_order"] = order
        ensure_card_zones(state, build_deck(), order, rng=self._rng)
        for _round in range(5):
            for player_id in order:
                draw_cards(state, player_id)
        draw_cards(state, first_player_id)
        state["turn_player_id"] = first_player_id
        state["trick"] = self._new_trick(1, first_player_id)
        return state

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
        hand = state["cards"]["hands"].get(player_id)
        if not isinstance(hand, list):
            raise ValueError("行动者不在本局手牌区")
        by_id = {card.get("id"): card for card in hand}
        if len(by_id) != len(hand):
            raise ValueError("持久化手牌 ID 不唯一")
        try:
            return [by_id[card_id] for card_id in card_ids]
        except KeyError as exc:
            raise ValueError("所选牌不全在行动者手中") from exc

    @classmethod
    def _play_actions_for(
        cls, state: dict[str, Any], player_id: str
    ) -> list[dict[str, Any]]:
        hand = state["cards"]["hands"].get(player_id, [])
        target = (state.get("trick") or {}).get("last_play")
        target_pattern = target.get("pattern") if isinstance(target, dict) else None
        actions: list[dict[str, Any]] = []
        for cards in _all_combinations(hand):
            pattern = classify_cards(cards)
            if pattern is None:
                continue
            if target_pattern is not None and not can_beat(pattern, target_pattern):
                continue
            ordered_cards = sorted(cards, key=_card_sort_key)
            actions.append({
                "action": "play",
                "card_ids": [str(card["id"]) for card in ordered_cards],
                "pattern_type": pattern["type"],
                "pattern_label": pattern["label"],
            })
        actions.sort(key=lambda action: (
            BOMB_STRENGTH.get(str(action["pattern_type"]), 0),
            len(action["card_ids"]),
            tuple(action["card_ids"]),
        ))
        return actions

    @classmethod
    def legal_actions_for(
        cls, state: dict[str, Any], player_id: str
    ) -> list[dict[str, Any]]:
        if (
            state.get("winner_player_id") is not None
            or state.get("flow", {}).get("phase") == "finished"
            or state.get("turn_player_id") != player_id
        ):
            return []
        actions = cls._play_actions_for(state, player_id)
        if (state.get("trick") or {}).get("last_play") is not None:
            actions.append({"action": "pass"})
        return actions

    @staticmethod
    def _parse_card_ids(move: dict[str, Any]) -> list[str]:
        card_ids = move.get("card_ids")
        if (
            not isinstance(card_ids, list)
            or not card_ids
            or any(not isinstance(card_id, str) or not card_id for card_id in card_ids)
            or len(card_ids) != len(set(card_ids))
        ):
            raise ValueError("card_ids 必须是非空且不重复的牌 ID 数组")
        return card_ids

    def validate_action(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
    ) -> None:
        if not isinstance(move, dict):
            raise ValueError("move 必须是对象")
        if state.get("flow", {}).get("phase") == "finished":
            raise ValueError("对局已经结束")
        player_id = str(actor["player_id"])
        if player_id not in state.get("participant_order", []):
            raise ValueError("行动者不在本局参与者中")
        if state.get("turn_player_id") != player_id:
            raise ValueError("当前行动权属于另一名参与者")
        action = move.get("action")
        if action == "pass":
            if set(move) != {"action"}:
                raise ValueError("pass 只接受 action 字段")
            if (state.get("trick") or {}).get("last_play") is None:
                raise ValueError("引牌者不能过牌")
            return
        if action != "play":
            raise ValueError("action 必须是 play 或 pass")
        allowed = {"action", "card_ids", "pattern_type", "pattern_label"}
        if set(move) - allowed:
            raise ValueError("play 只接受 action、card_ids 和服务端牌型提示")
        card_ids = self._parse_card_ids(move)
        selected = self._cards_for_ids(state, player_id, card_ids)
        pattern = classify_cards(selected)
        target = (state.get("trick") or {}).get("last_play")
        target_pattern = target.get("pattern") if isinstance(target, dict) else None
        if pattern is None or (
            target_pattern is not None and not can_beat(pattern, target_pattern)
        ):
            raise ValueError("所选组合不是服务端当前发布的合法出牌")
        for key in ("pattern_type", "pattern_label"):
            expected = pattern["type" if key == "pattern_type" else "label"]
            if key in move and move[key] != expected:
                raise ValueError("客户端牌型提示与服务端识别不一致")

    def validate_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> None:
        del state, move, mark
        raise ValueError("干瞪眼需要 participant-aware action 接口")

    def apply_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> dict[str, Any]:
        del state, move, mark
        raise ValueError("干瞪眼需要 participant-aware action 接口")

    def _apply_play(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
    ) -> MoveResult:
        player_id = str(actor["player_id"])
        card_ids = self._parse_card_ids(move)
        cards = self._cards_for_ids(state, player_id, card_ids)
        cards = sorted(cards, key=_card_sort_key)
        pattern = classify_cards(cards)
        if pattern is None:
            raise ValueError("服务端无法识别所选牌型")
        discard_cards(state, player_id, cards)
        state["play_sequence"] = int(state.get("play_sequence", 0)) + 1
        play = {
            "sequence": state["play_sequence"],
            "player_id": player_id,
            "cards": deepcopy(cards),
            "pattern": _public_pattern(pattern),
        }
        trick = state["trick"]
        trick["last_play"] = play
        trick["pass_player_ids"] = []
        state["flow"]["phase"] = "following"
        state["flow"]["turn_number"] = int(state["flow"].get("turn_number", 0)) + 1
        if pattern["is_bomb"]:
            state["bomb_count"] = int(state.get("bomb_count", 0)) + 1
            state["multiplier"] = min(
                int(state.get("max_multiplier", MAX_MULTIPLIER)),
                int(state.get("multiplier", 1)) * 2,
            )
        record = {
            "trick": trick["number"],
            "action": "play",
            **deepcopy(play),
            "multiplier": state["multiplier"],
        }
        state["action_history"].append(record)
        state["last_action"] = deepcopy(record)
        remaining = len(state["cards"]["hands"][player_id])
        if remaining == 0:
            state["winner_player_id"] = player_id
            state["turn_player_id"] = None
            state["flow"]["phase"] = "finished"
            return MoveResult(
                state=state,
                note=f"{pattern['label']}出牌成功并清空手牌，赢得本局。",
                result={"winner_player_id": player_id, "draw": False},
            )
        next_player = self._next_player(state, player_id)
        state["turn_player_id"] = next_player
        multiplier_note = (
            f"，倍率升至 {state['multiplier']} 倍" if pattern["is_bomb"] else ""
        )
        return MoveResult(
            state=state,
            next_player_id=next_player,
            note=f"打出{pattern['label']}（{len(cards)} 张）{multiplier_note}。",
        )

    def _apply_pass(
        self,
        state: dict[str, Any],
        actor: dict[str, Any],
    ) -> MoveResult:
        player_id = str(actor["player_id"])
        trick = state["trick"]
        if player_id in trick["pass_player_ids"]:
            raise ValueError("本轮跟牌已经过牌")
        trick["pass_player_ids"].append(player_id)
        state["flow"]["turn_number"] = int(state["flow"].get("turn_number", 0)) + 1
        pass_record = {
            "trick": trick["number"],
            "action": "pass",
            "player_id": player_id,
        }
        state["action_history"].append(pass_record)
        state["last_action"] = deepcopy(pass_record)
        last_player = str(trick["last_play"]["player_id"])
        other_players = {
            candidate for candidate in state["participant_order"]
            if candidate != last_player
        }
        if set(trick["pass_player_ids"]) >= other_players:
            draw_counts = {candidate: 0 for candidate in state["participant_order"]}
            draw_order: list[str] = []
            candidate = last_player
            for _index in state["participant_order"]:
                draw_order.append(candidate)
                candidate = self._next_player(state, candidate)
            for candidate in draw_order:
                if not state["cards"]["deck"]:
                    break
                draw_counts[candidate] = len(draw_cards(state, candidate, 1))
            completed_number = int(trick["number"])
            summary = {
                "trick": completed_number,
                "action": "trick_end",
                "winner_player_id": last_player,
                "pass_player_ids": list(trick["pass_player_ids"]),
                "draw_order": draw_order,
                "draw_counts": draw_counts,
                "deck_count": len(state["cards"]["deck"]),
            }
            state["action_history"].append(summary)
            state["last_action"] = deepcopy(summary)
            state["flow"].update({
                "phase": "leading",
                "round_number": completed_number + 1,
                "turn_number": 0,
            })
            state["trick"] = self._new_trick(completed_number + 1, last_player)
            state["turn_player_id"] = last_player
            drawn_total = sum(draw_counts.values())
            return MoveResult(
                state=state,
                next_player_id=last_player,
                note=f"其余玩家均过牌，第 {completed_number} 墩结束；按顺序摸 {drawn_total} 张。",
            )
        next_player = self._next_player(state, player_id)
        state["turn_player_id"] = next_player
        return MoveResult(
            state=state,
            next_player_id=next_player,
            note="过。",
        )

    def apply_action(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
    ) -> MoveResult:
        self.validate_action(state, move, actor)
        if move["action"] == "play":
            return self._apply_play(state, move, actor)
        return self._apply_pass(state, actor)

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
        return {"winner_player_id": winner, "draw": False} if winner else None

    def settlement_deltas(
        self,
        state: dict[str, Any],
        result: dict[str, Any],
        participants: list[dict[str, Any]],
        stake: int,
    ) -> dict[str, int]:
        player_ids = [str(item["player_id"]) for item in participants]
        winner = result.get("winner_player_id")
        if winner not in player_ids or result.get("draw"):
            raise ValueError("干瞪眼终局必须有一名有效赢家")
        if isinstance(stake, bool) or not isinstance(stake, int) or stake <= 0:
            raise ValueError("干瞪眼筹码底注必须是正整数")
        multiplier = int(state.get("multiplier", 1))
        hands = state.get("cards", {}).get("hands", {})
        deltas = {
            player_id: -stake * len(hands[player_id]) * multiplier
            for player_id in player_ids
            if player_id != winner
        }
        deltas[str(winner)] = -sum(deltas.values())
        return {player_id: int(deltas[player_id]) for player_id in player_ids}

    def public_state(
        self,
        state: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any]:
        del participants
        card_state = public_card_state(state)
        trick = state.get("trick") or {}
        last_play = trick.get("last_play")
        return {
            "board_kind": "gandengyan",
            "flow": deepcopy(state["flow"]),
            "current_trick": {
                "number": trick.get("number"),
                "leader_player_id": trick.get("leader_player_id"),
                "last_play": deepcopy(last_play),
                "pass_player_ids": list(trick.get("pass_player_ids", [])),
            },
            "deck_count": card_state["deck_count"],
            "hand_counts": card_state["hand_counts"],
            "multiplier": int(state.get("multiplier", 1)),
            "max_multiplier": int(state.get("max_multiplier", MAX_MULTIPLIER)),
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
    ) -> dict[str, int]:
        del participants
        return {
            "hand_count": int(
                state.get("hand_counts", {}).get(participant["player_id"], 0)
            )
        }

    def npc_compact_rules(
        self,
        state: dict[str, Any],
        actor: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> str:
        del state, actor, participants
        return (
            "固定四川 54 张干瞪眼：普通跟牌同型同数且恰高一级，单/对 2 可越级；"
            "顺子和连对不含 2。三炸、四炸、双王炸可按强度或点数越级压制。"
            "大小王不能单出且不作赖子。不要自行推导组合，只能原样选择服务端的"
            " authoritative legal_actions。"
        )

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

    def format_action(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
    ) -> str:
        if move.get("action") == "pass":
            return "过"
        card_ids = self._parse_card_ids(move)
        cards = self._cards_for_ids(state, str(actor["player_id"]), card_ids)
        pattern = classify_cards(cards)
        if pattern is None:
            raise ValueError("无法识别出牌牌型")
        labels = "、".join(
            card_label(card) for card in sorted(cards, key=_card_sort_key)
        )
        return f"{pattern['label']}：{labels}"

    def format_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> str:
        del state, mark
        return str(move.get("action", "play"))
