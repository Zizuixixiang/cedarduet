from __future__ import annotations

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
    private_hand,
    public_card_state,
)


COLORS = ("red", "yellow", "green", "blue")
COLOR_LABELS = {
    "red": "红色",
    "yellow": "黄色",
    "green": "绿色",
    "blue": "蓝色",
}
ACTION_KINDS = ("skip", "reverse", "draw_two")
KIND_LABELS = {
    "skip": "Skip",
    "reverse": "Reverse",
    "draw_two": "Draw Two",
    "wild": "Wild",
    "wild_draw_four": "Wild Draw Four",
}


def build_uno_deck() -> list[dict[str, Any]]:
    """Return the canonical 108-card deck with stable duplicate identities."""
    cards: list[dict[str, Any]] = []
    for color in COLORS:
        cards.append({
            "id": f"{color}-number-0-1",
            "color": color,
            "kind": "number",
            "value": 0,
        })
        for value in range(1, 10):
            for copy_number in (1, 2):
                cards.append({
                    "id": f"{color}-number-{value}-{copy_number}",
                    "color": color,
                    "kind": "number",
                    "value": value,
                })
        for kind in ACTION_KINDS:
            for copy_number in (1, 2):
                cards.append({
                    "id": f"{color}-{kind}-{copy_number}",
                    "color": color,
                    "kind": kind,
                })
    for copy_number in range(1, 5):
        cards.append({
            "id": f"wild-{copy_number}",
            "color": None,
            "kind": "wild",
        })
        cards.append({
            "id": f"wild-draw-four-{copy_number}",
            "color": None,
            "kind": "wild_draw_four",
        })
    return cards


class Uno(GamePlugin):
    game_type = "uno"
    display_name = "UNO"
    category = "card"
    min_players = 2
    max_players = 6
    allowed_player_counts = (2, 3, 4, 5, 6)
    recommended_players = 4
    supports_npcs = True
    supports_stakes = True
    supports_multiplayer_stakes = True
    mcp_immediate_public_events = True
    rules_text = (
        "【牌局】\n"
        "经典 108 张 UNO，支持 2–6 人，每人 7 张；开局翻出的第一张牌固定为数字牌，"
        "避免把功能牌效果无归属地施加给首位玩家。\n\n"
        "【行动】\n"
        "轮到自己时可出同颜色、同数字或同"
        "功能符号的牌，也可出 Wild；无牌可出或主动不出时摸 1 张，只有这张刚摸到的牌"
        "可在当前回合立即打出，否则回合结束。不采用 +2/+4 叠加。\n\n"
        "【功能牌】\n"
        "Skip 跳过下一位；"
        "Reverse 改变方向，2 人时等价于 Skip；Draw Two 令下一位摸 2 并失去回合。"
        "Wild 与 Wild Draw Four 出牌时都必须明确选择红、黄、绿、蓝之一。Wild Draw "
        "Four 若出牌者当时仍持有与当前颜色相同的非 Wild 牌即属违规；下一位可选择质疑"
        "或不质疑。质疑成功由出牌者摸 4，质疑者继续回合；质疑失败时质疑者共摸 6 并"
        "失去回合；不质疑则摸 4 并失去回合。打出牌后剩 1 张可在同一动作声明 UNO；"
        "未声明时，下一名实际获得行动权的玩家可在自己的首次其他动作前抓 UNO，成功令"
        "漏报者摸 2，之后窗口关闭。\n\n"
        "【胜负】\n"
        "先出完手牌者获胜；最后一张若为 Draw Two，会先让"
        "目标玩家摸牌再终局；最后一张若为 Wild Draw Four，必须等下一位完成不质疑或"
        "质疑结算后才终局，质疑成功则原出牌者因摸回 4 张而不能获胜。\n\n"
        "【牌堆】\n"
        "摸牌堆耗尽时保留"
        "弃牌堆顶牌，把其余弃牌洗回摸牌堆；洗牌结果随局面持久化，刷新或重启不会重洗。"
    )
    move_format = (
        '出牌：{"move":{"action":"play","card_id":"red-number-5-1"},'
        '"revision":当前版本}；Wild 必须另传 color，剩 1 张时可传 uno:true。'
        '摸牌：{"move":{"action":"draw"}}；摸到可出的牌后可 play 或 pass。'
        'WDF 响应为 challenge_wild_draw_four 或 accept_draw_four；抓漏报为 catch_uno。'
    )

    def __init__(self, rng: random.Random | None = None) -> None:
        self._rng = rng or random.SystemRandom()

    @staticmethod
    def tokens_for(participants: list[dict[str, Any]]) -> list[str]:
        return [f"P{index + 1}" for index, _item in enumerate(participants)]

    def first_player_id(
        self, participants: list[dict[str, Any]], mode: str
    ) -> str:
        opener = super().first_player_id(participants, mode)
        for participant in participants:
            participant["_uno_opener"] = str(participant["player_id"]) == opener
        return opener

    @staticmethod
    def _fixture(count: int = 2) -> list[dict[str, Any]]:
        return [
            {
                "player_id": f"player-{index + 1}",
                "token": f"P{index + 1}",
                "role": "human" if index == 0 else "ai",
                "_uno_opener": index == 0,
            }
            for index in range(count)
        ]

    def initial_state(self) -> dict[str, Any]:
        return self.initialize(self._fixture())

    def initialize(self, participants: list[dict[str, Any]]) -> dict[str, Any]:
        if len(participants) not in self.allowed_player_counts:
            raise ValueError("UNO 只支持 2–6 人")
        order = [str(item["player_id"]) for item in participants]
        opener = next(
            (
                str(item["player_id"])
                for item in participants
                if item.get("_uno_opener") or item.get("_opening_player")
            ),
            order[0],
        )
        state: dict[str, Any] = {
            "board_kind": "uno",
            "participant_order": order,
            "turn_player_id": opener,
            "direction": 1,
            "current_color": None,
            "drawn_card": None,
            "pending_wild_draw_four": None,
            "uno_window": None,
            "last_uno": None,
            "last_penalty": None,
            "last_challenge": None,
            "last_action": None,
            "action_history": [],
            "winner_player_id": None,
        }
        ensure_flow(state, phase="playing")
        zones = ensure_card_zones(
            state, build_uno_deck(), order, rng=self._rng
        )
        for _round in range(7):
            for player_id in order:
                draw_cards(state, player_id)

        # Starting with a number card avoids ambiguous ownership for an opening
        # action penalty. Moving one already-shuffled card does not rerandomize.
        deck = zones["deck"]
        starting_index = next(
            (
                index for index in range(len(deck) - 1, -1, -1)
                if deck[index].get("kind") == "number"
            ),
            None,
        )
        if starting_index is None:
            raise ValueError("UNO 牌堆缺少开局数字牌")
        starting_card = deck.pop(starting_index)
        zones["discard"].append(starting_card)
        state["current_color"] = starting_card["color"]
        state["last_action"] = {
            "action": "start",
            "card": deepcopy(starting_card),
        }
        return state

    @staticmethod
    def _card_label(card: dict[str, Any]) -> str:
        if card.get("kind") == "number":
            symbol = str(card.get("value"))
        else:
            symbol = KIND_LABELS.get(str(card.get("kind")), str(card.get("kind")))
        color = card.get("color")
        return f"{COLOR_LABELS[color]} {symbol}" if color in COLOR_LABELS else symbol

    @staticmethod
    def _top_card(state: dict[str, Any]) -> dict[str, Any]:
        discard = state.get("cards", {}).get("discard", [])
        if not discard:
            raise ValueError("弃牌堆缺少顶牌")
        return discard[-1]

    @classmethod
    def _can_play(cls, state: dict[str, Any], card: dict[str, Any]) -> bool:
        kind = card.get("kind")
        if kind in {"wild", "wild_draw_four"}:
            return True
        if card.get("color") == state.get("current_color"):
            return True
        top = cls._top_card(state)
        if kind == "number" and top.get("kind") == "number":
            return card.get("value") == top.get("value")
        return kind == top.get("kind")

    @staticmethod
    def _next_player_id(
        state: dict[str, Any], player_id: str, steps: int = 1
    ) -> str:
        order = state["participant_order"]
        index = order.index(player_id)
        direction = int(state.get("direction", 1))
        return str(order[(index + direction * steps) % len(order)])

    def _reshuffle_if_needed(self, state: dict[str, Any]) -> bool:
        zones = state["cards"]
        if zones["deck"]:
            return False
        discard = zones["discard"]
        if len(discard) <= 1:
            return False
        top = discard[-1]
        recycled = discard[:-1]
        self._rng.shuffle(recycled)
        zones["deck"] = recycled
        zones["discard"] = [top]
        return True

    def _draw_many(
        self, state: dict[str, Any], player_id: str, count: int
    ) -> list[dict[str, Any]]:
        drawn: list[dict[str, Any]] = []
        for _index in range(count):
            self._reshuffle_if_needed(state)
            card = draw_cards(state, player_id)
            if not card:
                break
            drawn.extend(card)
        return drawn

    @staticmethod
    def _play_variants(
        card: dict[str, Any], *, leaves_one: bool
    ) -> list[dict[str, Any]]:
        colors = COLORS if card.get("kind") in {"wild", "wild_draw_four"} else (None,)
        actions: list[dict[str, Any]] = []
        for color in colors:
            action = {"action": "play", "card_id": card["id"]}
            if color is not None:
                action["color"] = color
            actions.append(action)
            if leaves_one:
                actions.append({**action, "uno": True})
        return actions

    def _legal_actions_for(
        self, state: dict[str, Any], player_id: str
    ) -> list[dict[str, Any]]:
        if (
            state.get("winner_player_id") is not None
            or state.get("flow", {}).get("phase") == "finished"
            or state.get("turn_player_id") != player_id
        ):
            return []
        hand = state.get("cards", {}).get("hands", {}).get(player_id)
        if not isinstance(hand, list):
            return []
        actions: list[dict[str, Any]] = []
        window = state.get("uno_window")
        if isinstance(window, dict) and window.get("catcher_player_id") == player_id:
            actions.append({"action": "catch_uno"})

        pending = state.get("pending_wild_draw_four")
        if isinstance(pending, dict):
            if pending.get("challenger_player_id") == player_id:
                actions.extend([
                    {"action": "challenge_wild_draw_four"},
                    {"action": "accept_draw_four"},
                ])
            return actions

        drawn = state.get("drawn_card")
        if isinstance(drawn, dict) and drawn.get("player_id") == player_id:
            card = next(
                (item for item in hand if item.get("id") == drawn.get("card_id")),
                None,
            )
            if card is not None and self._can_play(state, card):
                actions.extend(self._play_variants(card, leaves_one=len(hand) == 2))
            actions.append({"action": "pass"})
            return actions

        for card in hand:
            if self._can_play(state, card):
                actions.extend(self._play_variants(card, leaves_one=len(hand) == 2))
        actions.append({"action": "draw"})
        return actions

    def validate_action(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
    ) -> None:
        if not isinstance(move, dict):
            raise ValueError("move 必须是对象")
        player_id = str(actor["player_id"])
        if move not in self._legal_actions_for(state, player_id):
            raise ValueError("该动作不在服务端权威 legal_actions 中")

    def validate_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> None:
        del state, move, mark
        raise ValueError("UNO 需要 participant-aware action 接口")

    def apply_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> dict[str, Any]:
        del state, move, mark
        raise ValueError("UNO 需要 participant-aware action 接口")

    @staticmethod
    def _close_uno_window_for_regular_action(
        state: dict[str, Any], player_id: str
    ) -> None:
        window = state.get("uno_window")
        if not isinstance(window, dict) or window.get("catcher_player_id") != player_id:
            return
        state["last_uno"] = {
            "status": "escaped",
            "offender_player_id": window["offender_player_id"],
            "catcher_player_id": player_id,
        }
        state["uno_window"] = None

    @staticmethod
    def _finish(
        state: dict[str, Any], winner_player_id: str, note: str
    ) -> MoveResult:
        state["winner_player_id"] = winner_player_id
        state["turn_player_id"] = None
        state["drawn_card"] = None
        state["pending_wild_draw_four"] = None
        state["uno_window"] = None
        state["flow"]["phase"] = "finished"
        return MoveResult(
            state=state,
            note=note,
            result={"winner_player_id": winner_player_id, "draw": False},
        )

    def _apply_catch_uno(
        self, state: dict[str, Any], actor_id: str
    ) -> MoveResult:
        window = deepcopy(state["uno_window"])
        offender = str(window["offender_player_id"])
        drawn = self._draw_many(state, offender, 2)
        state["uno_window"] = None
        state["last_uno"] = {
            "status": "caught",
            "offender_player_id": offender,
            "catcher_player_id": actor_id,
            "draw_count": len(drawn),
        }
        state["last_action"] = deepcopy(state["last_uno"])
        state["action_history"].append({
            "action": "catch_uno",
            **deepcopy(state["last_uno"]),
        })
        advance_flow(state)
        return MoveResult(
            state=state,
            retain_turn=True,
            note=f"抓 UNO 成功，{offender} 摸 {len(drawn)} 张。",
        )

    def _apply_draw(self, state: dict[str, Any], actor_id: str) -> MoveResult:
        self._close_uno_window_for_regular_action(state, actor_id)
        drawn = self._draw_many(state, actor_id, 1)
        record: dict[str, Any] = {
            "action": "draw",
            "player_id": actor_id,
            "draw_count": len(drawn),
        }
        state["last_action"] = deepcopy(record)
        state["action_history"].append(record)
        advance_flow(state)
        if drawn and self._can_play(state, drawn[0]):
            state["drawn_card"] = {
                "player_id": actor_id,
                "card_id": drawn[0]["id"],
            }
            state["flow"]["phase"] = "drawn_card_play"
            return MoveResult(
                state=state,
                retain_turn=True,
                note="摸到 1 张可出的牌，可选择立即出牌或结束回合。",
            )
        next_player = self._next_player_id(state, actor_id)
        state["drawn_card"] = None
        state["turn_player_id"] = next_player
        state["flow"]["phase"] = "playing"
        return MoveResult(
            state=state,
            next_player_id=next_player,
            note="摸到 1 张牌，本回合结束。" if drawn else "摸牌堆无可用牌，本回合结束。",
        )

    def _apply_pass(self, state: dict[str, Any], actor_id: str) -> MoveResult:
        self._close_uno_window_for_regular_action(state, actor_id)
        next_player = self._next_player_id(state, actor_id)
        state["drawn_card"] = None
        state["turn_player_id"] = next_player
        state["flow"]["phase"] = "playing"
        state["last_action"] = {"action": "pass", "player_id": actor_id}
        state["action_history"].append(deepcopy(state["last_action"]))
        advance_flow(state)
        return MoveResult(
            state=state,
            next_player_id=next_player,
            note="选择不打出刚摸到的牌，回合结束。",
        )

    def _apply_play(
        self, state: dict[str, Any], move: dict[str, Any], actor_id: str
    ) -> MoveResult:
        self._close_uno_window_for_regular_action(state, actor_id)
        hand = state["cards"]["hands"][actor_id]
        card = next(item for item in hand if item["id"] == move["card_id"])
        old_color = str(state["current_color"])
        wdf_was_legal = not any(
            item.get("color") == old_color and item.get("kind") not in {"wild", "wild_draw_four"}
            for item in hand
            if item["id"] != card["id"]
        )
        discard_cards(state, actor_id, [card])
        chosen_color = move.get("color")
        state["current_color"] = chosen_color or card["color"]
        state["drawn_card"] = None
        played = {
            "action": "play",
            "player_id": actor_id,
            "card": deepcopy(card),
            "chosen_color": chosen_color,
            "declared_uno": bool(move.get("uno")),
        }
        state["last_action"] = deepcopy(played)
        state["action_history"].append(deepcopy(played))
        advance_flow(state)

        kind = card["kind"]
        order_size = len(state["participant_order"])
        if kind == "reverse" and order_size > 2:
            state["direction"] = -int(state["direction"])
            next_player = self._next_player_id(state, actor_id)
        elif kind in {"skip", "reverse"}:
            next_player = self._next_player_id(state, actor_id, 2)
        elif kind == "draw_two":
            penalized = self._next_player_id(state, actor_id)
            drawn = self._draw_many(state, penalized, 2)
            next_player = self._next_player_id(state, actor_id, 2)
            state["last_penalty"] = {
                "kind": "draw_two",
                "source_player_id": actor_id,
                "target_player_id": penalized,
                "draw_count": len(drawn),
                "resolved": True,
            }
        elif kind == "wild_draw_four":
            challenger = self._next_player_id(state, actor_id)
            pending_winner = actor_id if not hand else None
            state["pending_wild_draw_four"] = {
                "offender_player_id": actor_id,
                "challenger_player_id": challenger,
                "chosen_color": chosen_color,
                "was_legal": wdf_was_legal,
                "pending_winner_player_id": pending_winner,
            }
            state["last_penalty"] = {
                "kind": "wild_draw_four",
                "source_player_id": actor_id,
                "target_player_id": challenger,
                "draw_count": 4,
                "resolved": False,
            }
            next_player = challenger
        else:
            next_player = self._next_player_id(state, actor_id)

        if len(hand) == 1:
            if move.get("uno"):
                state["last_uno"] = {
                    "status": "declared",
                    "player_id": actor_id,
                }
                state["uno_window"] = None
            elif next_player != actor_id:
                state["uno_window"] = {
                    "offender_player_id": actor_id,
                    "catcher_player_id": next_player,
                }
                state["last_uno"] = {
                    "status": "catchable",
                    "offender_player_id": actor_id,
                    "catcher_player_id": next_player,
                }
        elif not hand:
            state["uno_window"] = None

        state["turn_player_id"] = next_player
        if kind == "wild_draw_four":
            state["flow"]["phase"] = "wild_draw_four_response"
            return MoveResult(
                state=state,
                next_player_id=next_player,
                note=(
                    f"打出 {self._card_label(card)}，指定{COLOR_LABELS[state['current_color']]}；"
                    "等待下一位选择是否质疑。"
                ),
            )

        if not hand:
            return self._finish(
                state,
                actor_id,
                f"打出最后一张 {self._card_label(card)}，功能牌效果结算完毕，获得胜利。",
            )
        state["flow"]["phase"] = "playing"
        suffix = "并宣告 UNO" if move.get("uno") else ""
        return MoveResult(
            state=state,
            next_player_id=next_player,
            note=f"打出 {self._card_label(card)}{suffix}。",
        )

    def _apply_wdf_response(
        self, state: dict[str, Any], action: str, actor_id: str
    ) -> MoveResult:
        self._close_uno_window_for_regular_action(state, actor_id)
        pending = deepcopy(state["pending_wild_draw_four"])
        offender = str(pending["offender_player_id"])
        pending_winner = pending.get("pending_winner_player_id")
        if action == "challenge_wild_draw_four" and not pending["was_legal"]:
            drawn = self._draw_many(state, offender, 4)
            outcome = {
                "challenger_player_id": actor_id,
                "offender_player_id": offender,
                "challenged": True,
                "challenge_succeeded": True,
                "penalized_player_id": offender,
                "draw_count": len(drawn),
            }
            next_player = actor_id
            retain_turn = True
            note = f"质疑成功：{offender} 违规打出 Wild Draw Four，摸 {len(drawn)} 张。"
            pending_winner = None
        else:
            challenged = action == "challenge_wild_draw_four"
            requested = 6 if challenged else 4
            drawn = self._draw_many(state, actor_id, requested)
            outcome = {
                "challenger_player_id": actor_id,
                "offender_player_id": offender,
                "challenged": challenged,
                "challenge_succeeded": False if challenged else None,
                "penalized_player_id": actor_id,
                "draw_count": len(drawn),
            }
            next_player = self._next_player_id(state, actor_id)
            retain_turn = False
            note = (
                f"质疑失败：Wild Draw Four 使用合法，{actor_id} 共摸 {len(drawn)} 张并失去回合。"
                if challenged
                else f"选择不质疑，{actor_id} 摸 {len(drawn)} 张并失去回合。"
            )
        state["pending_wild_draw_four"] = None
        state["last_challenge"] = outcome
        state["last_penalty"] = {
            "kind": "wild_draw_four",
            "source_player_id": offender,
            "target_player_id": outcome["penalized_player_id"],
            "draw_count": outcome["draw_count"],
            "resolved": True,
            "challenged": outcome["challenged"],
            "challenge_succeeded": outcome["challenge_succeeded"],
        }
        record = {"action": action, **deepcopy(outcome)}
        state["last_action"] = deepcopy(record)
        state["action_history"].append(record)
        advance_flow(state)
        if pending_winner is not None:
            return self._finish(
                state,
                str(pending_winner),
                f"{note} Wild Draw Four 惩罚与质疑语义结算完毕，{pending_winner} 获胜。",
            )
        state["turn_player_id"] = next_player
        state["flow"]["phase"] = "playing"
        return MoveResult(
            state=state,
            retain_turn=retain_turn,
            next_player_id=None if retain_turn else next_player,
            note=note,
        )

    def apply_action(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
    ) -> MoveResult:
        self.validate_action(state, move, actor)
        actor_id = str(actor["player_id"])
        action = move["action"]
        if action == "catch_uno":
            return self._apply_catch_uno(state, actor_id)
        if action == "draw":
            return self._apply_draw(state, actor_id)
        if action == "pass":
            return self._apply_pass(state, actor_id)
        if action == "play":
            return self._apply_play(state, move, actor_id)
        return self._apply_wdf_response(state, action, actor_id)

    def progress_after_action(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
        participants: list[dict[str, Any]],
        applied: dict[str, Any] | MoveResult,
    ) -> dict[str, Any] | MoveResult:
        if not isinstance(applied, MoveResult):
            return applied
        action = str(move.get("action"))
        if action == "pass":
            return applied
        del state
        public = self.public_state(applied.state, participants)
        delta: dict[str, Any] = {
            "action": action,
            "phase": public["flow"]["phase"],
            "hand_counts": public["hand_counts"],
            "deck_count": public["deck_count"],
        }
        if action == "play":
            delta.update({
                "top_discard": public["top_discard"],
                "current_color": public["current_color"],
                "direction": public["direction"],
            })
        if action in {
            "play", "challenge_wild_draw_four", "accept_draw_four",
        }:
            delta["penalty_state"] = public["penalty_state"]
        if action in {"play", "draw", "catch_uno"}:
            delta["uno_state"] = public["uno_state"]
        applied.public_event = {"uno_delta": delta}
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
            raise ValueError("UNO 终局缺少有效唯一赢家")
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
        terminal = (
            state.get("flow", {}).get("phase") == "finished"
            and state.get("winner_player_id") is not None
        )
        return self._project_public_state(state, participants, terminal=terminal)

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
        pending = deepcopy(state.get("pending_wild_draw_four"))
        if isinstance(pending, dict):
            pending.pop("was_legal", None)
        last_action = state.get("last_action")
        public_note = state.get("last_action_note", "")
        if isinstance(last_action, dict) and last_action.get("action") == "draw":
            public_note = "摸了 1 张牌。" if last_action.get("draw_count") else "摸牌堆无可用牌。"
        projected = {
            "board_kind": "uno",
            "flow": deepcopy(state["flow"]),
            "hand_counts": cards["hand_counts"],
            "deck_count": cards["deck_count"],
            "top_discard": deepcopy(self._top_card(state)),
            "current_color": state["current_color"],
            "direction": int(state["direction"]),
            "penalty_state": {
                "pending_wild_draw_four": pending,
                "last_penalty": deepcopy(state.get("last_penalty")),
                "last_challenge": deepcopy(state.get("last_challenge")),
            },
            "uno_state": {
                "window": deepcopy(state.get("uno_window")),
                "last": deepcopy(state.get("last_uno")),
            },
            "last_action_note": public_note,
        }
        if terminal:
            hands = (state.get("cards") or {}).get("hands", {})
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
        player_id = str(viewer["player_id"])
        return {
            "hand": private_hand(state, player_id),
            "legal_actions": self._legal_actions_for(state, player_id),
        }

    def participant_summary(
        self,
        state: dict[str, Any],
        participant: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, int]:
        del participants
        return {
            "hand_count": int(state["hand_counts"][participant["player_id"]])
        }

    def npc_compact_rules(
        self,
        state: dict[str, Any],
        actor: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> str:
        del state, actor, participants
        return (
            "经典 UNO：同色/同数字/同符号或 Wild；摸 1 后只能打刚摸牌，不叠加惩罚。"
            "2 人 Reverse 等价 Skip。Wild 必须选色。WDF 可被下一位质疑：违规则出牌者摸4，"
            "合法则质疑者摸6；不质疑摸4。剩1张应随 play 传 uno:true；有 catch_uno 时可"
            "先抓漏报。只能原样选择服务端 legal_actions 中的一项。"
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
        del participants
        return deepcopy(self._legal_actions_for(state, str(actor["player_id"])))

    def format_action(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
    ) -> str:
        del actor
        action = move.get("action")
        if action == "play":
            hand = state.get("cards", {}).get("hands", {}).get(
                state.get("turn_player_id"), []
            )
            card = next(
                (item for item in hand if item.get("id") == move.get("card_id")),
                None,
            )
            label = self._card_label(card) if card else str(move.get("card_id"))
            color = move.get("color")
            suffix = f"，选{COLOR_LABELS[color]}" if color in COLOR_LABELS else ""
            if move.get("uno"):
                suffix += "，宣告 UNO"
            return f"出 {label}{suffix}"
        return {
            "draw": "摸 1 张",
            "pass": "结束摸牌回合",
            "catch_uno": "抓 UNO",
            "challenge_wild_draw_four": "质疑 Wild Draw Four",
            "accept_draw_four": "不质疑并摸 4 张",
        }.get(str(action), str(action))

    def format_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> str:
        del mark
        return self.format_action(state, move, {})
