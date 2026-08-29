from __future__ import annotations

import random
from copy import deepcopy
from typing import Any

from .base import GamePlugin, MoveResult
from .tools import advance_flow, ensure_flow


class LiarsDice(GamePlugin):
    game_type = "liars_dice"
    display_name = "吹牛骰子"
    min_players = 2
    max_players = 6
    allowed_player_counts = (2, 3, 4, 5, 6)
    recommended_players = 4
    supports_npcs = True
    supports_stakes = True
    supports_multiplayer_stakes = True
    rules_text = (
        "每人初始 5 枚六面骰，本版 1 点不作万能点。每轮所有人只看自己的当前骰子；"
        "首位行动者先叫点，之后叫点必须更高：数量更大，或数量相同而点数更大。"
        "叫点数量不得超过场上当前骰子总数。除首叫外可以质疑上一手。质疑后公开本轮"
        "全部骰子：实际数量达到叫点，质疑者失去一枚骰；否则上一位叫点者失去一枚。"
        "失去全部骰子即淘汰；失骰者仍存活则由其开启下一轮，否则由其后下一位存活者"
        "开启。最后一人获胜。质疑、揭骰、减骰、淘汰与下一轮重掷在一次权威动作中"
        "原子完成，公开区会保留上一轮结果。"
    )
    move_format = (
        '叫点：{"move":{"action":"bid","quantity":3,"face":4},"revision":当前版本}；'
        '质疑：{"move":{"action":"challenge"},"revision":当前版本}。'
    )

    def __init__(self, rng: random.Random | None = None) -> None:
        # Tests inject random.Random(seed) or a randint-compatible fixture.
        self._rng = rng or random.SystemRandom()

    @staticmethod
    def _state_skeleton() -> dict[str, Any]:
        state: dict[str, Any] = {
            "board_kind": "liars_dice",
            "participant_order": [],
            "dice_by_player": {},
            "dice_counts": {},
            "current_bid": None,
            "round_actions": [],
            "action_history": [],
            "eliminated_player_ids": [],
            "last_round_result": None,
            "winner_player_id": None,
        }
        ensure_flow(state, phase="bidding")
        return state

    def initial_state(self) -> dict[str, Any]:
        return self._state_skeleton()

    def _roll(self, count: int) -> list[int]:
        values = [self._rng.randint(1, 6) for _ in range(count)]
        if len(values) != count or any(
            isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 6
            for value in values
        ):
            raise ValueError("骰子随机源必须返回 1–6 的整数")
        return values

    def initialize(self, participants: list[dict[str, Any]]) -> dict[str, Any]:
        state = self._state_skeleton()
        order = [str(item["player_id"]) for item in participants]
        state["participant_order"] = order
        state["dice_counts"] = {player_id: 5 for player_id in order}
        state["dice_by_player"] = {
            player_id: self._roll(5) for player_id in order
        }
        return state

    @staticmethod
    def _active_ids(state: dict[str, Any]) -> list[str]:
        return [
            player_id for player_id in state["participant_order"]
            if state["dice_counts"].get(player_id, 0) > 0
        ]

    @staticmethod
    def _next_survivor_after(state: dict[str, Any], player_id: str) -> str:
        order = state["participant_order"]
        start = order.index(player_id)
        for offset in range(1, len(order) + 1):
            candidate = order[(start + offset) % len(order)]
            if state["dice_counts"].get(candidate, 0) > 0:
                return candidate
        raise ValueError("没有下一名存活参与者")

    @staticmethod
    def _bid_is_higher(
        current_bid: dict[str, Any] | None, quantity: int, face: int
    ) -> bool:
        if current_bid is None:
            return True
        return quantity > current_bid["quantity"] or (
            quantity == current_bid["quantity"] and face > current_bid["face"]
        )

    @staticmethod
    def _parse_bid(move: dict[str, Any]) -> tuple[int, int]:
        quantity, face = move.get("quantity"), move.get("face")
        if (
            isinstance(quantity, bool) or not isinstance(quantity, int)
            or isinstance(face, bool) or not isinstance(face, int)
        ):
            raise ValueError("quantity 和 face 必须是整数")
        return quantity, face

    def validate_action(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
    ) -> None:
        if state.get("flow", {}).get("phase") != "bidding":
            raise ValueError("当前阶段不能叫点或质疑")
        player_id = str(actor["player_id"])
        if state["dice_counts"].get(player_id, 0) <= 0:
            raise ValueError("已淘汰参与者不能行动")
        action = move.get("action")
        if action == "challenge":
            if set(move) != {"action"}:
                raise ValueError("challenge 只接受 action 字段")
            bid = state.get("current_bid")
            if bid is None:
                raise ValueError("首叫之前不能质疑")
            if bid.get("bidder_player_id") == player_id:
                raise ValueError("不能质疑自己的叫点")
            return
        if action != "bid":
            raise ValueError("action 必须是 bid 或 challenge")
        if set(move) != {"action", "quantity", "face"}:
            raise ValueError("bid 只接受 action、quantity、face 字段")
        quantity, face = self._parse_bid(move)
        max_quantity = sum(state["dice_counts"].values())
        if not 1 <= quantity <= max_quantity:
            raise ValueError(f"quantity 必须在 1–{max_quantity} 之间")
        if not 1 <= face <= 6:
            raise ValueError("face 必须在 1–6 之间；1 点不作万能点")
        if not self._bid_is_higher(state.get("current_bid"), quantity, face):
            raise ValueError("叫点必须比上一手更高")

    def validate_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> None:
        del state, move, mark
        raise ValueError("吹牛骰子需要 participant-aware action 接口")

    def apply_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> dict[str, Any]:
        del state, move, mark
        raise ValueError("吹牛骰子需要 participant-aware action 接口")

    def _apply_bid(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
    ) -> MoveResult:
        quantity, face = self._parse_bid(move)
        bid = {
            "quantity": quantity,
            "face": face,
            "bidder_player_id": str(actor["player_id"]),
        }
        state["current_bid"] = bid
        record = {
            "round": state["flow"]["round_number"],
            "action": "bid",
            **bid,
        }
        state["round_actions"].append(record)
        state["action_history"].append(deepcopy(record))
        advance_flow(state)
        return MoveResult(state=state, note=f"叫 {quantity} 个 {face} 点。")

    def _apply_challenge(
        self,
        state: dict[str, Any],
        actor: dict[str, Any],
    ) -> MoveResult:
        flow = state["flow"]
        flow["phase"] = "revealing"
        bid = deepcopy(state["current_bid"])
        challenger = str(actor["player_id"])
        bidder = str(bid["bidder_player_id"])
        revealed = deepcopy(state["dice_by_player"])
        actual_count = sum(
            value == bid["face"]
            for dice in revealed.values()
            for value in dice
        )
        bid_holds = actual_count >= bid["quantity"]
        loser = challenger if bid_holds else bidder
        state["dice_counts"][loser] -= 1
        loser_remaining = state["dice_counts"][loser]
        eliminated = loser_remaining == 0
        if eliminated:
            state["dice_by_player"][loser] = []
            state["eliminated_player_ids"].append(loser)
        result = {
            "phase": "revealed",
            "round": flow["round_number"],
            "bid": bid,
            "bidder_player_id": bidder,
            "challenger_player_id": challenger,
            "actual_count": actual_count,
            "bid_holds": bid_holds,
            "loser_player_id": loser,
            "loser_remaining_dice": loser_remaining,
            "eliminated": eliminated,
            "eliminated_player_id": loser if eliminated else None,
            "next_round": None,
            "next_starter_player_id": None,
            "revealed_dice_by_player": revealed,
        }
        state["last_round_result"] = result
        state["current_bid"] = None
        record = {
            "round": flow["round_number"],
            "action": "challenge",
            "challenger_player_id": challenger,
            "bidder_player_id": bidder,
            "actual_count": actual_count,
            "bid_holds": bid_holds,
            "loser_player_id": loser,
        }
        state["action_history"].append(record)
        state["round_actions"] = []
        active_ids = self._active_ids(state)
        activity = {loser: "eliminated"} if eliminated else {}
        if len(active_ids) == 1:
            winner = active_ids[0]
            flow["phase"] = "finished"
            state["winner_player_id"] = winner
            return MoveResult(
                state=state,
                note=f"质疑结算：实际有 {actual_count} 个 {bid['face']} 点；比赛结束。",
                participant_activity=activity,
                result={"winner_player_id": winner, "draw": False},
            )

        starter = loser if loser_remaining > 0 else self._next_survivor_after(state, loser)
        for player_id in state["participant_order"]:
            count = state["dice_counts"][player_id]
            state["dice_by_player"][player_id] = self._roll(count) if count else []
        advance_flow(state, phase="bidding", next_round=True)
        result["next_round"] = flow["round_number"]
        result["next_starter_player_id"] = starter
        return MoveResult(
            state=state,
            next_player_id=starter,
            participant_activity=activity,
            note=(
                f"质疑结算：实际有 {actual_count} 个 {bid['face']} 点；"
                f"{loser} 失去 1 枚骰，下一轮开始。"
            ),
        )

    def apply_action(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
    ) -> MoveResult:
        if move["action"] == "bid":
            return self._apply_bid(state, move, actor)
        return self._apply_challenge(state, actor)

    def progress_after_action(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
        participants: list[dict[str, Any]],
        applied: dict[str, Any] | MoveResult,
    ) -> dict[str, Any] | MoveResult:
        del state, actor
        if move.get("action") != "challenge" or not isinstance(applied, MoveResult):
            return applied
        outcome = applied.state.get("last_round_result")
        if not isinstance(outcome, dict):
            return applied
        names = {
            str(participant["player_id"]): str(
                participant.get("display_name") or participant["player_id"]
            )
            for participant in participants
        }
        challenger_id = str(outcome["challenger_player_id"])
        bidder_id = str(outcome["bidder_player_id"])
        loser_id = str(outcome["loser_player_id"])
        starter_id = outcome.get("next_starter_player_id")
        challenger_name = names.get(challenger_id, challenger_id)
        bidder_name = names.get(bidder_id, bidder_id)
        loser_name = names.get(loser_id, loser_id)
        outcome["challenger_display_name"] = challenger_name
        outcome["bidder_display_name"] = bidder_name
        outcome["loser_display_name"] = loser_name
        if starter_id is not None:
            starter_id = str(starter_id)
            outcome["next_starter_display_name"] = names.get(starter_id, starter_id)
            outcome["next_round_summary"] = (
                f"第 {outcome['next_round']} 轮 · "
                f"由 {outcome['next_starter_display_name']} 开叫"
            )
        bid = outcome["bid"]
        elimination = "，已淘汰" if outcome["eliminated"] else "，未淘汰"
        result_summary = (
            f"{challenger_name} 质疑 {bidder_name} 的叫点"
            f"“{bid['quantity']} 个 {bid['face']} 点”；实际有 "
            f"{outcome['actual_count']} 个 {bid['face']} 点，"
            f"叫点{'成立' if outcome['bid_holds'] else '失败'}；"
            f"{loser_name} 输掉 1 枚骰，剩余 {outcome['loser_remaining_dice']} 枚"
            f"{elimination}。"
        )
        outcome["result_summary"] = result_summary
        summary = f"第 {outcome['round']} 轮：{result_summary}"
        if outcome.get("next_round_summary"):
            summary += outcome["next_round_summary"] + "。"
        else:
            summary += "对局结束。"
        outcome["summary"] = summary
        applied.note = summary
        return applied

    def result_for(
        self,
        state: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        del participants
        winner = state.get("winner_player_id")
        return {"winner_player_id": winner, "draw": False} if winner else None

    def check_winner(self, state: dict[str, Any]) -> str | None:
        del state
        return None

    def settlement_deltas(
        self,
        state: dict[str, Any],
        result: dict[str, Any],
        participants: list[dict[str, Any]],
        stake: int,
    ) -> dict[str, int]:
        del state
        player_ids = [str(item["player_id"]) for item in participants]
        winner = result.get("winner_player_id")
        if winner not in player_ids:
            raise ValueError("吹牛骰子终局缺少有效唯一赢家")
        return {
            player_id: stake * (len(player_ids) - 1)
            if player_id == winner else -stake
            for player_id in player_ids
        }

    def public_state(
        self,
        state: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any]:
        del participants
        return {
            "board_kind": "liars_dice",
            "flow": deepcopy(state["flow"]),
            "dice_counts": deepcopy(state["dice_counts"]),
            "max_bid_quantity": sum(state["dice_counts"].values()),
            "current_bid": deepcopy(state.get("current_bid")),
            "eliminated_player_ids": list(state.get("eliminated_player_ids", [])),
            "last_round_result": deepcopy(state.get("last_round_result")),
            "last_action_note": state.get("last_action_note", ""),
        }

    def private_state(
        self,
        state: dict[str, Any],
        viewer: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any]:
        del participants
        return {"dice": deepcopy(state["dice_by_player"][viewer["player_id"]])}

    def participant_summary(
        self,
        state: dict[str, Any],
        participant: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, int]:
        del participants
        return {"dice_count": int(state["dice_counts"][participant["player_id"]])}

    def npc_compact_rules(
        self,
        state: dict[str, Any],
        actor: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> str:
        del state, actor, participants
        return (
            "每人初始 5d6，1 不万能。叫点按(数量,点数)严格升高且数量不超过场上骰子数；"
            "已有叫点时也可质疑。实际数达到叫点则质疑者失骰，否则叫点者失骰；"
            "零骰淘汰，最后一人胜。只能选权威合法行动。"
        )

    def npc_public_actions(
        self,
        state: dict[str, Any],
        actor: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        del actor, participants
        return deepcopy(state.get("action_history", [])[-20:])

    def npc_legal_actions(
        self,
        state: dict[str, Any],
        actor: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        del actor, participants
        current = state.get("current_bid")
        max_quantity = sum(state["dice_counts"].values())
        actions = [
            {"action": "bid", "quantity": quantity, "face": face}
            for quantity in range(1, max_quantity + 1)
            for face in range(1, 7)
            if self._bid_is_higher(current, quantity, face)
        ]
        if current is not None:
            actions.append({"action": "challenge"})
        return actions

    def format_action(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
    ) -> str:
        del state, actor
        if move.get("action") == "challenge":
            return "质疑"
        quantity, face = self._parse_bid(move)
        return f"叫 {quantity} 个 {face} 点"

    def format_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> str:
        del mark
        return self.format_action(state, move, {})
