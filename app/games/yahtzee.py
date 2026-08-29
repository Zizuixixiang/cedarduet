from __future__ import annotations

import itertools
import random
from copy import deepcopy
from typing import Any

from .base import GamePlugin, MoveResult
from .tools import ensure_flow, roll_dice


UPPER_CATEGORIES = (
    "ones",
    "twos",
    "threes",
    "fours",
    "fives",
    "sixes",
)
LOWER_CATEGORIES = (
    "three_of_a_kind",
    "four_of_a_kind",
    "full_house",
    "small_straight",
    "large_straight",
    "yahtzee",
    "chance",
)
CATEGORIES = UPPER_CATEGORIES + LOWER_CATEGORIES
CATEGORY_LABELS = {
    "ones": "一点",
    "twos": "二点",
    "threes": "三点",
    "fours": "四点",
    "fives": "五点",
    "sixes": "六点",
    "three_of_a_kind": "三条",
    "four_of_a_kind": "四条",
    "full_house": "葫芦",
    "small_straight": "小顺",
    "large_straight": "大顺",
    "yahtzee": "快艇 / 五同",
    "chance": "机会",
}
UPPER_BONUS_THRESHOLD = 63
UPPER_BONUS_SCORE = 35
MAX_ROLLS = 3


def score_category(category: str, dice: list[int]) -> int:
    """Return the standard score for one category and exactly five d6."""
    if category not in CATEGORIES:
        raise ValueError("未知计分类")
    if len(dice) != 5 or any(
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= 6
        for value in dice
    ):
        raise ValueError("计分必须使用 5 枚 1–6 点骰子")
    counts = {face: dice.count(face) for face in range(1, 7)}
    if category in UPPER_CATEGORIES:
        face = UPPER_CATEGORIES.index(category) + 1
        return counts[face] * face
    if category == "three_of_a_kind":
        return sum(dice) if max(counts.values()) >= 3 else 0
    if category == "four_of_a_kind":
        return sum(dice) if max(counts.values()) >= 4 else 0
    if category == "full_house":
        return 25 if sorted(count for count in counts.values() if count) == [2, 3] else 0
    unique = set(dice)
    if category == "small_straight":
        return 30 if any(
            run <= unique
            for run in ({1, 2, 3, 4}, {2, 3, 4, 5}, {3, 4, 5, 6})
        ) else 0
    if category == "large_straight":
        return 40 if unique in ({1, 2, 3, 4, 5}, {2, 3, 4, 5, 6}) else 0
    if category == "yahtzee":
        return 50 if max(counts.values()) == 5 else 0
    return sum(dice)


class Yahtzee(GamePlugin):
    game_type = "yahtzee"
    display_name = "快艇骰子"
    category = "dice"
    min_players = 2
    max_players = 6
    allowed_player_counts = (2, 3, 4, 5, 6)
    recommended_players = 4
    supports_npcs = True
    supports_stakes = False
    supports_multiplayer_stakes = False
    rules_text = (
        "使用 5 枚六面骰，支持 2–6 人。每回合先掷骰，最多共掷 3 次；第二、三次前"
        "可以保留任意骰子，只重掷其余骰子，也可以提前计分。每回合必须在一个尚未填写"
        "的类别记分；不符合组合时记 0 分，也可明确选择把任意未用类别划掉记 0 分。"
        "13 类依次为一点至六点、三条、四条、葫芦、小顺、大顺、快艇/五同和机会。"
        "三条、四条与机会按骰子总和；葫芦 25 分，小顺 30 分，大顺 40 分，快艇 50 分。"
        "上半区一点至六点合计达到 63 分时另加 35 分。每类整局只能填写一次；所有人"
        "各完成 13 回合后总分最高者获胜，最高总分多人并列即为和局，不随机破同分。"
        "第一版不实现重复快艇额外 100 分，也不实现 Joker 规则；快艇栏填过后再掷出"
        "五同，只能按普通骰型填入其他未用类别，或划掉一类记 0 分。本游戏暂为娱乐局，"
        "不支持筹码下注。所有掷骰结果在服务端生成并随局面持久保存，刷新不会重掷。"
    )
    move_format = (
        '掷骰：{"move":{"action":"roll","hold_indices":[0,2]},"revision":当前版本}，'
        "也可用长度为 5 的 held_mask；首次掷骰保留项必须为空。"
        '计分：{"move":{"action":"score","category":"full_house"},"revision":当前版本}；'
        "明确划掉可另传 zero:true。"
    )

    def __init__(self, rng: random.Random | None = None) -> None:
        self._rng = rng or random.SystemRandom()

    @staticmethod
    def _state_skeleton() -> dict[str, Any]:
        state: dict[str, Any] = {
            "board_kind": "yahtzee",
            "participant_order": [],
            "scorecards": {},
            "turns_completed_by_player": {},
            "turn_player_id": None,
            "dice": [],
            "held_mask": [False] * 5,
            "rolls_used": 0,
            "dice_rolls": [],
            "action_history": [],
            "last_scoring": None,
        }
        ensure_flow(state, phase="awaiting_roll")
        return state

    def initial_state(self) -> dict[str, Any]:
        return self._state_skeleton()

    def initialize(self, participants: list[dict[str, Any]]) -> dict[str, Any]:
        state = self._state_skeleton()
        order = [str(item["player_id"]) for item in participants]
        state["participant_order"] = order
        state["scorecards"] = {player_id: {} for player_id in order}
        state["turns_completed_by_player"] = {player_id: 0 for player_id in order}
        return state

    @staticmethod
    def _card_totals(card: dict[str, int]) -> dict[str, int]:
        upper_subtotal = sum(int(card.get(category, 0)) for category in UPPER_CATEGORIES)
        upper_bonus = UPPER_BONUS_SCORE if upper_subtotal >= UPPER_BONUS_THRESHOLD else 0
        lower_subtotal = sum(int(card.get(category, 0)) for category in LOWER_CATEGORIES)
        return {
            "upper_subtotal": upper_subtotal,
            "upper_bonus": upper_bonus,
            "lower_subtotal": lower_subtotal,
            "total": upper_subtotal + upper_bonus + lower_subtotal,
        }

    @classmethod
    def _totals_by_player(cls, state: dict[str, Any]) -> dict[str, dict[str, int]]:
        return {
            player_id: cls._card_totals(state["scorecards"][player_id])
            for player_id in state["participant_order"]
        }

    @staticmethod
    def _parse_hold_mask(state: dict[str, Any], move: dict[str, Any]) -> list[bool]:
        allowed_fields = {"action"}
        if "held_mask" in move and "hold_indices" in move:
            raise ValueError("held_mask 和 hold_indices 只能使用一种")
        if "held_mask" in move:
            allowed_fields.add("held_mask")
            mask = move["held_mask"]
            if (
                not isinstance(mask, list)
                or len(mask) != 5
                or any(not isinstance(value, bool) for value in mask)
            ):
                raise ValueError("held_mask 必须是长度为 5 的布尔数组")
            parsed = list(mask)
        elif "hold_indices" in move:
            allowed_fields.add("hold_indices")
            indices = move["hold_indices"]
            if not isinstance(indices, list) or any(
                isinstance(index, bool)
                or not isinstance(index, int)
                or not 0 <= index < 5
                for index in indices
            ):
                raise ValueError("hold_indices 必须是 0–4 的整数数组")
            if len(indices) != len(set(indices)):
                raise ValueError("hold_indices 不能重复")
            parsed = [index in indices for index in range(5)]
        else:
            parsed = [False] * 5
        if set(move) != allowed_fields:
            raise ValueError("roll 只接受 action 以及 held_mask 或 hold_indices")
        if not state.get("dice") and any(parsed):
            raise ValueError("首次掷骰前没有可保留的骰子")
        return parsed

    @staticmethod
    def _validate_turn_state(state: dict[str, Any], player_id: str) -> None:
        turn_player_id = state.get("turn_player_id")
        if turn_player_id is not None and turn_player_id != player_id:
            raise ValueError("当前骰子属于另一名参与者的回合")

    def validate_action(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
    ) -> None:
        if state.get("flow", {}).get("phase") == "finished":
            raise ValueError("对局已经结束")
        player_id = str(actor["player_id"])
        if player_id not in state.get("scorecards", {}):
            raise ValueError("行动者不在本局计分卡中")
        self._validate_turn_state(state, player_id)
        action = move.get("action")
        if action == "roll":
            if int(state.get("rolls_used", 0)) >= MAX_ROLLS:
                raise ValueError("本回合最多掷 3 次，请选择计分类")
            self._parse_hold_mask(state, move)
            return
        if action != "score":
            raise ValueError("action 必须是 roll 或 score")
        allowed_fields = {"action", "category"}
        if "zero" in move:
            allowed_fields.add("zero")
            if not isinstance(move["zero"], bool):
                raise ValueError("zero 必须是布尔值")
        if set(move) != allowed_fields:
            raise ValueError("score 只接受 action、category 和可选 zero")
        category = move.get("category")
        if category not in CATEGORIES:
            raise ValueError("category 不是有效计分类")
        if len(state.get("dice", [])) != 5 or int(state.get("rolls_used", 0)) < 1:
            raise ValueError("每回合至少掷一次骰后才能计分")
        if category in state["scorecards"][player_id]:
            raise ValueError(f"{CATEGORY_LABELS[category]}已经填写过")

    def validate_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> None:
        del state, move, mark
        raise ValueError("快艇骰子需要 participant-aware action 接口")

    def apply_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> dict[str, Any]:
        del state, move, mark
        raise ValueError("快艇骰子需要 participant-aware action 接口")

    def _apply_roll(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
    ) -> MoveResult:
        player_id = str(actor["player_id"])
        held_mask = self._parse_hold_mask(state, move)
        old_dice = list(state.get("dice", []))
        rerolled_indices = [index for index, held in enumerate(held_mask) if not held]
        if not old_dice:
            rerolled_indices = list(range(5))
            held_mask = [False] * 5
        new_values: list[int] = []
        if rerolled_indices:
            record = roll_dice(
                state,
                roller_player_id=player_id,
                count=len(rerolled_indices),
                sides=6,
                key="dice_rolls",
                rng=self._rng,
            )
            new_values = list(record["values"])
            if any(
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 1 <= value <= 6
                for value in new_values
            ):
                raise ValueError("骰子随机源必须返回 1–6 的整数")
        else:
            state["dice_rolls"].append({
                "sequence": len(state["dice_rolls"]) + 1,
                "roller_player_id": player_id,
                "count": 0,
                "sides": 6,
                "values": [],
                "visible_to_player_ids": None,
            })
        rolled = iter(new_values)
        dice = [
            old_dice[index] if old_dice and held_mask[index] else next(rolled)
            for index in range(5)
        ]
        state["turn_player_id"] = player_id
        state["dice"] = dice
        state["held_mask"] = held_mask
        state["rolls_used"] = int(state.get("rolls_used", 0)) + 1
        state["flow"]["phase"] = (
            "choosing_score" if state["rolls_used"] >= MAX_ROLLS else "rolling_or_scoring"
        )
        state["flow"]["turn_number"] = state["rolls_used"]
        persisted_roll = state["dice_rolls"][-1]
        persisted_roll.update({
            "roll_number": state["rolls_used"],
            "held_mask": list(held_mask),
            "rerolled_indices": rerolled_indices,
            "result": list(dice),
        })
        action = {
            "round": state["flow"]["round_number"],
            "player_id": player_id,
            "action": "roll",
            "roll_number": state["rolls_used"],
            "held_mask": list(held_mask),
            "dice": list(dice),
        }
        state["action_history"].append(action)
        held_count = sum(held_mask)
        note = f"第 {state['rolls_used']} 次掷骰：{'、'.join(map(str, dice))}"
        if held_count:
            note += f"（保留 {held_count} 枚）"
        return MoveResult(state=state, retain_turn=True, note=note)

    def _apply_score(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
    ) -> MoveResult:
        player_id = str(actor["player_id"])
        category = str(move["category"])
        scratched = bool(move.get("zero", False))
        points = 0 if scratched else score_category(category, state["dice"])
        state["scorecards"][player_id][category] = points
        completed = len(state["scorecards"][player_id])
        state["turns_completed_by_player"][player_id] = completed
        scoring = {
            "round": state["flow"]["round_number"],
            "player_id": player_id,
            "action": "score",
            "category": category,
            "category_label": CATEGORY_LABELS[category],
            "score": points,
            "scratched": scratched,
            "dice": list(state["dice"]),
        }
        state["last_scoring"] = scoring
        state["action_history"].append(deepcopy(scoring))
        finished = all(
            len(card) == len(CATEGORIES) for card in state["scorecards"].values()
        )
        minimum_completed = min(state["turns_completed_by_player"].values())
        state["flow"]["round_number"] = min(len(CATEGORIES), minimum_completed + 1)
        state["flow"]["turn_number"] = 0
        state["flow"]["phase"] = "finished" if finished else "awaiting_roll"
        state["turn_player_id"] = None
        state["dice"] = []
        state["held_mask"] = [False] * 5
        state["rolls_used"] = 0
        verb = "划掉" if scratched else "填写"
        note = f"{verb}{CATEGORY_LABELS[category]}：{points} 分。"
        return MoveResult(
            state=state,
            note=note,
            result=self._terminal_result(state) if finished else None,
        )

    def apply_action(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
    ) -> MoveResult:
        if move["action"] == "roll":
            return self._apply_roll(state, move, actor)
        return self._apply_score(state, move, actor)

    @classmethod
    def _terminal_result(cls, state: dict[str, Any]) -> dict[str, Any]:
        totals = cls._totals_by_player(state)
        order_index = {
            player_id: index for index, player_id in enumerate(state["participant_order"])
        }
        ranked_ids = sorted(
            state["participant_order"],
            key=lambda player_id: (-totals[player_id]["total"], order_index[player_id]),
        )
        best = totals[ranked_ids[0]]["total"]
        leaders = [
            player_id for player_id in state["participant_order"]
            if totals[player_id]["total"] == best
        ]
        placements: list[dict[str, int | str]] = []
        previous_total: int | None = None
        previous_rank = 0
        for index, player_id in enumerate(ranked_ids, start=1):
            total = totals[player_id]["total"]
            rank = previous_rank if total == previous_total else index
            placements.append({"rank": rank, "player_id": player_id, "total": total})
            previous_total, previous_rank = total, rank
        result: dict[str, Any] = {
            "draw": len(leaders) > 1,
            "tied_player_ids": leaders if len(leaders) > 1 else [],
            "totals_by_player": totals,
            "placements": placements,
            "tie_policy": "最高总分并列即和局；并列名单按稳定座位顺序展示",
        }
        if len(leaders) == 1:
            result["winner_player_id"] = leaders[0]
        return result

    def result_for(
        self,
        state: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        del participants
        if not state.get("scorecards") or not all(
            len(card) == len(CATEGORIES) for card in state["scorecards"].values()
        ):
            return None
        return self._terminal_result(state)

    def check_winner(self, state: dict[str, Any]) -> str | None:
        del state
        return None

    def public_state(
        self,
        state: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any]:
        del participants
        projected = {
            key: deepcopy(value)
            for key, value in state.items()
            if key not in {"dice_rolls", "action_history"}
        }
        projected["categories"] = [
            {
                "key": category,
                "label": CATEGORY_LABELS[category],
                "section": "upper" if category in UPPER_CATEGORIES else "lower",
            }
            for category in CATEGORIES
        ]
        projected["upper_bonus_threshold"] = UPPER_BONUS_THRESHOLD
        projected["upper_bonus_score"] = UPPER_BONUS_SCORE
        projected["max_rolls"] = MAX_ROLLS
        projected["totals_by_player"] = self._totals_by_player(state)
        turn_player_id = state.get("turn_player_id")
        card = state.get("scorecards", {}).get(turn_player_id, {})
        projected["score_previews"] = (
            {
                category: score_category(category, state["dice"])
                for category in CATEGORIES
                if category not in card
            }
            if len(state.get("dice", [])) == 5 else {}
        )
        if state.get("flow", {}).get("phase") == "finished":
            projected["legal_actions"] = []
        elif turn_player_id is None:
            projected["legal_actions"] = [
                {"action": "roll", "held_mask": [False] * 5}
            ]
        else:
            projected["legal_actions"] = self.npc_legal_actions(
                state,
                {"player_id": turn_player_id},
                [],
            )
        return projected

    def participant_summary(
        self,
        state: dict[str, Any],
        participant: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, int]:
        del participants
        player_id = str(participant["player_id"])
        totals = state.get("totals_by_player") or self._totals_by_player(state)
        card = state["scorecards"].get(player_id, {})
        return {
            "score": int(totals[player_id]["total"]),
            "filled": len(card),
        }

    def private_state(
        self,
        state: dict[str, Any],
        viewer: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any]:
        del participants
        player_id = str(viewer["player_id"])
        if (
            viewer.get("role") != "ai"
            or state.get("turn_player_id") != player_id
            or len(state.get("dice", [])) != 5
        ):
            return {}
        card = state["scorecards"][player_id]
        return {
            "dice": list(state["dice"]),
            "legal_categories": [
                category for category in CATEGORIES if category not in card
            ],
        }

    def npc_compact_rules(
        self,
        state: dict[str, Any],
        actor: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> str:
        del state, actor, participants
        return (
            "5d6，每回合至少掷一次、最多三次；roll 的 held_mask 保留对应位置，"
            "score 填一个未用类别。上半区 63 加 35；三/四条按总和，葫芦25，"
            "小顺30，大顺40，快艇50，机会按总和。无重复快艇 bonus/Joker。"
            "公开计分卡、当前骰子和 score_previews 可用于决策；只能选权威合法行动。"
        )

    def npc_public_actions(
        self,
        state: dict[str, Any],
        actor: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        del actor, participants
        return deepcopy(state.get("action_history", [])[-20:])

    @staticmethod
    def _recommended_hold_mask(dice: list[int]) -> list[bool]:
        counts = {face: dice.count(face) for face in range(1, 7)}
        repeated_face = max(counts, key=lambda face: (counts[face], face))
        if counts[repeated_face] >= 2:
            return [value == repeated_face for value in dice]
        unique = set(dice)
        best_run = max(
            ({1, 2, 3, 4}, {2, 3, 4, 5}, {3, 4, 5, 6}),
            key=lambda run: (len(run & unique), sum(run & unique)),
        )
        if len(best_run & unique) >= 3:
            kept: set[int] = set()
            mask: list[bool] = []
            for value in dice:
                hold = value in best_run and value not in kept
                mask.append(hold)
                if hold:
                    kept.add(value)
            return mask
        highest = max(dice)
        return [value == highest for value in dice]

    @staticmethod
    def _score_action_priority(category: str, score: int, dice: list[int]) -> tuple[int, int]:
        bonuses = {
            "yahtzee": 80,
            "large_straight": 65,
            "small_straight": 50,
            "full_house": 42,
            "four_of_a_kind": 30,
            "three_of_a_kind": 22,
            "chance": 5,
        }
        if category in UPPER_CATEGORIES:
            face = UPPER_CATEGORIES.index(category) + 1
            bonus = dice.count(face) * 7 + face
        else:
            bonus = bonuses.get(category, 0) if score else -CATEGORIES.index(category)
        return score + bonus, -CATEGORIES.index(category)

    def npc_legal_actions(
        self,
        state: dict[str, Any],
        actor: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        del participants
        if state.get("flow", {}).get("phase") == "finished":
            return []
        player_id = str(actor["player_id"])
        if state.get("turn_player_id") not in {None, player_id}:
            return []
        rolls_used = int(state.get("rolls_used", 0))
        dice = list(state.get("dice", []))
        if rolls_used == 0:
            return [{"action": "roll", "held_mask": [False] * 5}]
        card = state["scorecards"][player_id]
        score_actions = [
            {"action": "score", "category": category}
            for category in CATEGORIES if category not in card
        ]
        score_actions.sort(
            key=lambda action: self._score_action_priority(
                action["category"], score_category(action["category"], dice), dice
            ),
            reverse=True,
        )
        if rolls_used >= MAX_ROLLS:
            return score_actions
        recommended = self._recommended_hold_mask(dice)
        masks = [list(mask) for mask in itertools.product((False, True), repeat=5)]
        masks.sort(key=lambda mask: (
            mask != recommended,
            -sum(mask),
            tuple(not value for value in mask),
        ))
        roll_actions = [
            {"action": "roll", "held_mask": mask} for mask in masks
        ]
        return roll_actions + score_actions

    def format_action(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
    ) -> str:
        del actor
        if move.get("action") == "roll":
            mask = self._parse_hold_mask(state, move)
            held = [str(index + 1) for index, value in enumerate(mask) if value]
            suffix = f"，保留第 {'、'.join(held)} 枚" if held else ""
            return f"第 {int(state.get('rolls_used', 0)) + 1} 次掷骰{suffix}"
        category = move.get("category")
        label = CATEGORY_LABELS.get(category, str(category))
        return f"{label}记 0 分" if move.get("zero") else f"计分：{label}"

    def format_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> str:
        del mark
        return self.format_action(state, move, {})
