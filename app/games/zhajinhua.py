from __future__ import annotations

import random
from copy import deepcopy
from typing import Any

from third_party.golden_flower_evaluator import compare_hands, evaluate_hand

from .base import GamePlugin, MoveResult
from .tools import advance_flow, draw_cards, ensure_card_zones, ensure_flow, private_hand


SUITS = ("spades", "hearts", "diamonds", "clubs")
SUIT_CODES = {"spades": "S", "hearts": "H", "diamonds": "D", "clubs": "C"}
RANKS = ("2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A")
ANTE = 1
RAISE_TIERS = (1, 2, 4, 8)
MAX_BLIND_UNIT = RAISE_TIERS[-1]
VIRTUAL_BUDGET = 32
MAX_ROUNDS = 20


def build_deck() -> list[dict[str, str]]:
    """Return one canonical 52-card deck without jokers."""
    return [
        {
            "id": f"{SUIT_CODES[suit]}{rank}",
            "suit": suit,
            "rank": rank,
        }
        for suit in SUITS
        for rank in RANKS
    ]


def _public_card(card: dict[str, Any]) -> dict[str, str]:
    rank = card.get("rank")
    suit = card.get("suit")
    if rank not in RANKS or suit not in SUITS:
        raise ValueError("牌面数据无效")
    return {"rank": str(rank), "suit": str(suit)}


def _hidden_hand() -> list[dict[str, bool]]:
    return [{"hidden": True} for _index in range(3)]


class Zhajinhua(GamePlugin):
    game_type = "zhajinhua"
    display_name = "炸金花"
    category = "card"
    min_players = 2
    max_players = 6
    allowed_player_counts = (2, 3, 4, 5, 6)
    recommended_players = 4
    supports_npcs = True
    supports_stakes = True
    supports_multiplayer_stakes = True
    uses_custom_stake_settlement = True
    mcp_immediate_public_events = True
    rules_text = (
        "【固定牌型版本】\n"
        "2–6 人，使用一副 52 张牌，不含大小王，每人 3 张暗牌。牌型从大到小固定为："
        "豹子（三张同点）＞同花顺＞金花（同花但不连续）＞顺子＞对子＞散牌。"
        "A-2-3 算顺子且是最小顺子，2-3-4 次之；Q-K-A（AKQ）是最大顺子。"
        "本版不采用“235 吃豹子”或任何花色大小规则。\n\n"
        "【同型比较】\n"
        "豹子比三张点数；同花顺和顺子比顺子序列；金花与散牌按 A>K>Q>J>10>…>2 "
        "从最高张逐级比较；对子先比对子点数，再比单张。花色永不决胜。"
        "若牌型和全部比较点数完全相同，评估结果为平手；主动发起比牌的一方按规则出局。\n\n"
        "【闷牌与行动】\n"
        "开局每人投入 1 个虚拟底注。未看牌称为闷牌：跟注按当前闷注档位支付，"
        "加注按所选新档位支付；看牌免费且不结束当前行动，看牌后自己的三张牌只对本人显示。"
        "已看牌玩家的跟注、加注和比牌费用都是相同闷注档位的 2 倍。"
        "每次非看牌行动后按座位轮转；第一轮所有仍在局玩家行动完毕后，已看牌玩家才可发起比牌，"
        "对手可为任意仍在局玩家。比牌不公开双方牌面，只公开双方、胜负和平手标记；负者出局。"
        "玩家也可随时弃牌；只剩一人时立即结束。\n\n"
        "【档位、封顶与终局】\n"
        "服务端只允许闷注 1、2、4、8 四档，8 为单注档位上限。每人本局最多投入 32 个虚拟单位，"
        "含底注；无法承担任何付费行动时仍可弃牌。每轮指所有仍在局玩家各完成一次跟、加、弃或比；"
        "看牌不计轮次。完成第 20 轮仍有多人时强制摊牌，仅此时公开仍在局玩家的三张牌；"
        "最高者获胜，最高牌完全相同则记为并列平局。\n\n"
        "【虚拟下注与真实筹码】\n"
        "底池始终等于所有玩家本局虚拟投入之和，且每人投入不超过 32。房间底注"
        "就是每个虚拟下注单位对应的真实娱乐筹码；"
        "单人最大真实亏损为 32 倍房间底注。牌局进行中只维护虚拟投入与虚拟底池，不读取、"
        "锁定或移动钱包筹码，也不存在钱包底池。唯一赢家产生后，每名负者的终局差额为"
        "－虚拟投入×房间底注，赢家获得其他所有人的同额总和；完成第 20 轮后的最高牌精确"
        "并列则退还全部虚拟下注，所有钱包差额均为 0。钱包只在终局通过零和结算一次性"
        "变动；0 筹码娱乐房始终不变动钱包。"
    )
    move_format = (
        '看牌：{"move":{"action":"peek"}}；'
        "跟注、加注、弃牌、比牌必须原样选择 private_state.legal_actions 中的对象，"
        "作为 params.move，包括服务端给出的 cost、unit 或 target_player_id。"
    )

    def __init__(self, rng: random.Random | None = None) -> None:
        self._rng = rng or random.SystemRandom()

    @staticmethod
    def _state_skeleton() -> dict[str, Any]:
        state: dict[str, Any] = {
            "board_kind": "zhajinhua",
            "participant_order": [],
            "turn_player_id": None,
            "cards": None,
            "player_state_by_player": {},
            "pot": 0,
            "ante": ANTE,
            "blind_unit": RAISE_TIERS[0],
            "raise_tiers": list(RAISE_TIERS),
            "max_blind_unit": MAX_BLIND_UNIT,
            "virtual_budget": VIRTUAL_BUDGET,
            "max_rounds": MAX_ROUNDS,
            "acted_player_ids_this_round": [],
            "action_history": [],
            "last_action": None,
            "last_compare": None,
            "revealed_hands": {},
            "winner_player_id": None,
            "game_result": None,
            "finish_reason": None,
        }
        ensure_flow(state, phase="betting")
        return state

    def initial_state(self) -> dict[str, Any]:
        return self._state_skeleton()

    def tokens_for(self, participants: list[dict[str, Any]]) -> list[str]:
        return [f"P{index + 1}" for index, _item in enumerate(participants)]

    def initialize(self, participants: list[dict[str, Any]]) -> dict[str, Any]:
        ordered = sorted(participants, key=lambda item: item.get("seat_index", 0))
        if not ordered:
            raise ValueError("炸金花至少需要一个等待席位")
        return self.initialize_for_first_player(
            participants, str(ordered[0]["player_id"])
        )

    def initialize_for_first_player(
        self,
        participants: list[dict[str, Any]],
        first_player_id: str,
    ) -> dict[str, Any]:
        if not 1 <= len(participants) <= self.max_players:
            raise ValueError("炸金花只支持 2–6 人，等待房允许先创建 1 个席位")
        ordered = sorted(participants, key=lambda item: item.get("seat_index", 0))
        order = [str(item["player_id"]) for item in ordered]
        if first_player_id not in order:
            raise ValueError("炸金花首位必须是本桌参与者")
        state = self._state_skeleton()
        state["participant_order"] = order
        state["turn_player_id"] = first_player_id
        ensure_card_zones(state, build_deck(), order, rng=self._rng)
        for _deal_round in range(3):
            for player_id in order:
                if len(draw_cards(state, player_id, 1)) != 1:
                    raise ValueError("牌堆不足，无法完成三张牌发牌")
        state["player_state_by_player"] = {
            player_id: {
                "seen": False,
                "status": "active",
                "contribution": ANTE,
            }
            for player_id in order
        }
        state["pot"] = ANTE * len(order)
        self._assert_virtual_conservation(state)
        return state

    def prepare_opening_state(
        self,
        state: dict[str, Any],
        first_player_id: str,
        participants: list[dict[str, Any]],
    ) -> dict[str, Any]:
        del participants
        if first_player_id not in state.get("participant_order", []):
            raise ValueError("炸金花开局行动者不属于本桌")
        state["turn_player_id"] = first_player_id
        return state

    @staticmethod
    def _player_state(state: dict[str, Any], player_id: str) -> dict[str, Any]:
        value = state.get("player_state_by_player", {}).get(player_id)
        if not isinstance(value, dict):
            raise ValueError("参与者不在炸金花状态中")
        return value

    @staticmethod
    def _hands(state: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        cards = state.get("cards")
        hands = cards.get("hands") if isinstance(cards, dict) else None
        if not isinstance(hands, dict):
            raise ValueError("炸金花牌区结构无效")
        return hands

    @classmethod
    def _hand(cls, state: dict[str, Any], player_id: str) -> list[dict[str, Any]]:
        hand = cls._hands(state).get(player_id)
        if not isinstance(hand, list) or len(hand) != 3:
            raise ValueError("炸金花参与者手牌必须恰为三张")
        return hand

    @classmethod
    def _active_player_ids(cls, state: dict[str, Any]) -> list[str]:
        return [
            player_id
            for player_id in state.get("participant_order", [])
            if cls._player_state(state, player_id).get("status") == "active"
        ]

    @classmethod
    def _next_active_after(cls, state: dict[str, Any], player_id: str) -> str:
        order = state["participant_order"]
        index = order.index(player_id)
        for offset in range(1, len(order) + 1):
            candidate = str(order[(index + offset) % len(order)])
            if cls._player_state(state, candidate).get("status") == "active":
                return candidate
        raise ValueError("没有可继续行动的炸金花参与者")

    @staticmethod
    def _assert_virtual_conservation(state: dict[str, Any]) -> None:
        players = state.get("player_state_by_player")
        if not isinstance(players, dict) or not players:
            raise ValueError("炸金花虚拟下注状态无效")
        contributions = []
        for player in players.values():
            contribution = player.get("contribution") if isinstance(player, dict) else None
            if (
                isinstance(contribution, bool)
                or not isinstance(contribution, int)
                or not ANTE <= contribution <= VIRTUAL_BUDGET
            ):
                raise ValueError("炸金花单人虚拟投入超出范围")
            contributions.append(contribution)
        pot = state.get("pot")
        if (
            isinstance(pot, bool)
            or not isinstance(pot, int)
            or pot != sum(contributions)
            or pot > len(contributions) * VIRTUAL_BUDGET
        ):
            raise ValueError("炸金花虚拟底池与投入不守恒")

    @classmethod
    def _cost_for(cls, state: dict[str, Any], player_id: str, unit: int) -> int:
        return unit * (2 if bool(cls._player_state(state, player_id)["seen"]) else 1)

    @classmethod
    def legal_actions_for(
        cls, state: dict[str, Any], player_id: str
    ) -> list[dict[str, Any]]:
        if (
            state.get("flow", {}).get("phase") != "betting"
            or state.get("game_result") is not None
            or state.get("turn_player_id") != player_id
            or cls._player_state(state, player_id).get("status") != "active"
        ):
            return []
        player = cls._player_state(state, player_id)
        contribution = int(player["contribution"])
        actions: list[dict[str, Any]] = []
        if not player["seen"]:
            actions.append({"action": "peek"})

        blind_unit = int(state["blind_unit"])
        call_cost = cls._cost_for(state, player_id, blind_unit)
        if contribution + call_cost <= VIRTUAL_BUDGET:
            actions.append({"action": "call", "cost": call_cost})
        for unit in RAISE_TIERS:
            if unit <= blind_unit:
                continue
            cost = cls._cost_for(state, player_id, unit)
            if contribution + cost <= VIRTUAL_BUDGET:
                actions.append({"action": "raise", "unit": unit, "cost": cost})

        round_number = int(state.get("flow", {}).get("round_number", 1))
        if player["seen"] and round_number >= 2 and contribution + call_cost <= VIRTUAL_BUDGET:
            for target_player_id in cls._active_player_ids(state):
                if target_player_id != player_id:
                    actions.append({
                        "action": "compare",
                        "target_player_id": target_player_id,
                        "cost": call_cost,
                    })
        actions.append({"action": "fold"})
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
        if player_id not in state.get("participant_order", []):
            raise ValueError("行动者不在本局参与者中")
        legal = self.legal_actions_for(state, player_id)
        if move not in legal:
            raise ValueError("动作不在当前服务端 authoritative legal_actions 中")

    def validate_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> None:
        del state, move, mark
        raise ValueError("炸金花需要 participant-aware action 接口")

    def apply_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> dict[str, Any]:
        del state, move, mark
        raise ValueError("炸金花需要 participant-aware action 接口")

    @classmethod
    def _charge(cls, state: dict[str, Any], player_id: str, cost: int) -> None:
        if isinstance(cost, bool) or not isinstance(cost, int) or cost <= 0:
            raise ValueError("炸金花付费必须是正整数")
        player = cls._player_state(state, player_id)
        if int(player["contribution"]) + cost > VIRTUAL_BUDGET:
            raise ValueError("本次行动超过单人 32 单位封顶")
        player["contribution"] = int(player["contribution"]) + cost
        state["pot"] = int(state["pot"]) + cost
        cls._assert_virtual_conservation(state)

    @staticmethod
    def _record_action(
        state: dict[str, Any], player_id: str, action: dict[str, Any]
    ) -> dict[str, Any]:
        record = {
            "sequence": len(state["action_history"]) + 1,
            "player_id": player_id,
            **deepcopy(action),
        }
        state["action_history"].append(record)
        state["last_action"] = record
        return record

    @classmethod
    def _finish_single_winner(
        cls, state: dict[str, Any], winner_player_id: str, reason: str
    ) -> dict[str, Any]:
        result_text = (
            f"仅剩一名未弃牌玩家；虚拟底池 {state['pot']} 单位归属赢家。若房间底注"
            "大于 0，终局按各席虚拟投入×房间底注一次性零和结算；牌局中没有钱包底池。"
        )
        result = {
            "winner_player_id": winner_player_id,
            "draw": False,
            "finish_reason": reason,
            "virtual_pot": int(state["pot"]),
            "virtual_contributions_by_player": {
                player_id: int(cls._player_state(state, player_id)["contribution"])
                for player_id in state["participant_order"]
            },
            "stake_settlement": {
                "virtual_unit_value": "room_stake",
                "timing": "terminal_only",
                "wallet_pot_during_hand": False,
            },
            "result_text": result_text,
        }
        state["winner_player_id"] = winner_player_id
        state["finish_reason"] = reason
        state["game_result"] = deepcopy(result)
        state["turn_player_id"] = None
        state["flow"]["phase"] = "finished"
        cls._assert_virtual_conservation(state)
        return result

    @classmethod
    def _forced_showdown(cls, state: dict[str, Any]) -> dict[str, Any]:
        active = cls._active_player_ids(state)
        if len(active) < 2:
            raise ValueError("强制摊牌至少需要两名仍在局玩家")
        values = {
            player_id: evaluate_hand(cls._hand(state, player_id))
            for player_id in active
        }
        best_key = max(value.comparison_key for value in values.values())
        winners = [
            player_id
            for player_id in active
            if values[player_id].comparison_key == best_key
        ]
        state["revealed_hands"] = {
            player_id: {
                "cards": [_public_card(card) for card in cls._hand(state, player_id)],
                "hand_type": values[player_id].hand_type,
                "hand_type_label": values[player_id].label,
            }
            for player_id in active
        }
        if len(winners) == 1:
            result: dict[str, Any] = {
                "winner_player_id": winners[0],
                "draw": False,
                "finish_reason": "round_cap_showdown",
                "virtual_pot": int(state["pot"]),
                "result_text": (
                    "完成第 20 轮，强制摊牌决出唯一最高牌；若房间底注大于 0，"
                    "终局按各席虚拟投入×房间底注一次性零和结算。"
                ),
            }
            state["winner_player_id"] = winners[0]
        else:
            result = {
                "draw": True,
                "tied_player_ids": winners,
                "finish_reason": "round_cap_tie",
                "virtual_pot": int(state["pot"]),
                "result_text": (
                    "完成第 20 轮，最高牌比较点数完全相同，并列平局；全部虚拟下注"
                    "视为退还，所有钱包结算差额均为 0。"
                ),
            }
            state["winner_player_id"] = None
        result["virtual_contributions_by_player"] = {
            player_id: int(cls._player_state(state, player_id)["contribution"])
            for player_id in state["participant_order"]
        }
        result["stake_settlement"] = {
            "virtual_unit_value": "room_stake",
            "timing": "terminal_only",
            "wallet_pot_during_hand": False,
        }
        state["finish_reason"] = str(result["finish_reason"])
        state["game_result"] = deepcopy(result)
        state["turn_player_id"] = None
        state["flow"]["phase"] = "finished"
        cls._assert_virtual_conservation(state)
        return result

    @classmethod
    def _complete_counted_turn(
        cls, state: dict[str, Any], player_id: str
    ) -> dict[str, Any] | None:
        advance_flow(state)
        active = cls._active_player_ids(state)
        if len(active) == 1:
            return cls._finish_single_winner(state, active[0], "last_player_standing")
        acted = {
            str(item)
            for item in state.get("acted_player_ids_this_round", [])
            if item in active
        }
        if player_id in active:
            acted.add(player_id)
        if set(active) <= acted:
            if int(state["flow"]["round_number"]) >= MAX_ROUNDS:
                state["acted_player_ids_this_round"] = sorted(acted)
                return cls._forced_showdown(state)
            advance_flow(state, next_round=True, turn_increment=0)
            state["acted_player_ids_this_round"] = []
        else:
            state["acted_player_ids_this_round"] = [
                candidate for candidate in state["participant_order"] if candidate in acted
            ]
        return None

    def apply_action(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
    ) -> MoveResult:
        self.validate_action(state, move, actor)
        player_id = str(actor["player_id"])
        action = str(move["action"])
        participant_activity: dict[str, str] = {}

        if action == "peek":
            self._player_state(state, player_id)["seen"] = True
            self._record_action(state, player_id, {"action": "peek"})
            return MoveResult(
                state=state,
                retain_turn=True,
                note="已看牌；三张牌只对本人显示，请继续选择下注、比牌或弃牌。",
            )

        if action == "call":
            cost = int(move["cost"])
            self._charge(state, player_id, cost)
            self._record_action(state, player_id, {"action": "call", "cost": cost})
            note = f"跟注 {cost} 个虚拟单位。"
        elif action == "raise":
            unit = int(move["unit"])
            cost = int(move["cost"])
            self._charge(state, player_id, cost)
            state["blind_unit"] = unit
            self._record_action(
                state, player_id, {"action": "raise", "unit": unit, "cost": cost}
            )
            note = f"加注到闷注 {unit} 档，投入 {cost} 个虚拟单位。"
        elif action == "fold":
            self._player_state(state, player_id)["status"] = "folded"
            self._record_action(state, player_id, {"action": "fold"})
            participant_activity[player_id] = "eliminated"
            note = "弃牌，本局不再行动。"
        else:
            target_player_id = str(move["target_player_id"])
            cost = int(move["cost"])
            self._charge(state, player_id, cost)
            comparison = compare_hands(
                self._hand(state, player_id), self._hand(state, target_player_id)
            )
            tied = comparison == 0
            loser_player_id = (
                player_id if comparison <= 0 else target_player_id
            )
            winner_player_id = (
                target_player_id if loser_player_id == player_id else player_id
            )
            self._player_state(state, loser_player_id)["status"] = "compare_lost"
            record = self._record_action(state, player_id, {
                "action": "compare",
                "target_player_id": target_player_id,
                "cost": cost,
            })
            state["last_compare"] = {
                "sequence": record["sequence"],
                "initiator_player_id": player_id,
                "target_player_id": target_player_id,
                "winner_player_id": winner_player_id,
                "loser_player_id": loser_player_id,
                "tied": tied,
                "cards_revealed": False,
            }
            participant_activity[loser_player_id] = "eliminated"
            note = (
                "比牌点数完全相同，主动比牌方按规则出局；双方牌面不公开。"
                if tied
                else "完成比牌，负者出局；双方牌面不公开。"
            )

        result = self._complete_counted_turn(state, player_id)
        if result is not None:
            return MoveResult(
                state=state,
                note=f"{note} {result['result_text']}",
                result=result,
                participant_activity=participant_activity,
            )
        next_player_id = self._next_active_after(state, player_id)
        state["turn_player_id"] = next_player_id
        return MoveResult(
            state=state,
            next_player_id=next_player_id,
            note=note,
            participant_activity=participant_activity,
        )

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
        delta: dict[str, Any] = {}
        if action.get("action") == "compare":
            delta["compare"] = deepcopy(applied.state.get("last_compare"))
        if applied.state.get("revealed_hands"):
            delta["showdown"] = deepcopy(applied.state["revealed_hands"])
        applied.public_event = {"zhajinhua_delta": delta} if delta else None
        return applied

    def result_for(
        self,
        state: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        del participants
        result = state.get("game_result")
        return deepcopy(result) if isinstance(result, dict) else None

    def settlement_deltas(
        self,
        state: dict[str, Any],
        result: dict[str, Any],
        participants: list[dict[str, Any]],
        stake: int,
    ) -> dict[str, int]:
        if isinstance(stake, bool) or not isinstance(stake, int) or stake <= 0:
            raise ValueError("炸金花房间 stake 必须是正整数")
        player_ids = [str(item["player_id"]) for item in participants]
        if len(player_ids) != len(set(player_ids)) or not 2 <= len(player_ids) <= 6:
            raise ValueError("炸金花结算参与者必须是 2–6 个唯一席位")
        self._assert_virtual_conservation(state)
        players = state.get("player_state_by_player", {})
        if set(players) != set(player_ids):
            raise ValueError("炸金花虚拟投入必须完整覆盖终局参与者")

        if result.get("draw"):
            if result.get("finish_reason") != "round_cap_tie":
                raise ValueError("炸金花仅允许轮次封顶的精确并列作为钱包平局")
            return {player_id: 0 for player_id in player_ids}

        winner = result.get("winner_player_id")
        if winner not in player_ids:
            raise ValueError("炸金花终局必须有一名有效唯一赢家")
        contributions = {
            player_id: int(self._player_state(state, player_id)["contribution"])
            for player_id in player_ids
        }
        deltas = {
            player_id: -contributions[player_id] * stake
            for player_id in player_ids
            if player_id != winner
        }
        deltas[str(winner)] = (
            int(state["pot"]) - contributions[str(winner)]
        ) * stake
        ordered = {player_id: int(deltas[player_id]) for player_id in player_ids}
        if sum(ordered.values()) != 0:
            raise ValueError("炸金花终局真实筹码结算不守恒")
        if any(delta < -VIRTUAL_BUDGET * stake for delta in ordered.values()):
            raise ValueError("炸金花终局真实亏损超过 32×stake")
        return ordered

    def check_winner(self, state: dict[str, Any]) -> str | None:
        result = state.get("game_result")
        return "draw" if isinstance(result, dict) and result.get("draw") else None

    def public_state(
        self,
        state: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any]:
        del participants
        self._assert_virtual_conservation(state)
        return {
            "board_kind": "zhajinhua",
            "flow": deepcopy(state["flow"]),
            "participant_order": list(state["participant_order"]),
            "turn_player_id": state.get("turn_player_id"),
            "players": deepcopy(state["player_state_by_player"]),
            "pot": int(state["pot"]),
            "ante": ANTE,
            "blind_unit": int(state["blind_unit"]),
            "raise_tiers": list(RAISE_TIERS),
            "max_blind_unit": MAX_BLIND_UNIT,
            "virtual_budget": VIRTUAL_BUDGET,
            "max_rounds": MAX_ROUNDS,
            "last_compare": deepcopy(state.get("last_compare")),
            "revealed_hands": deepcopy(state.get("revealed_hands", {})),
            "action_history": deepcopy(state.get("action_history", [])),
            "game_result": deepcopy(state.get("game_result")),
            "last_action_note": str(state.get("last_action_note") or ""),
        }

    def private_state(
        self,
        state: dict[str, Any],
        viewer: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any]:
        del participants
        player_id = str(viewer["player_id"])
        player = self._player_state(state, player_id)
        public_showdown = state.get("revealed_hands", {}).get(player_id)
        revealed = bool(player["seen"] or public_showdown)
        if revealed:
            hand = [_public_card(card) for card in private_hand(state, player_id)]
            value = evaluate_hand(self._hand(state, player_id))
            hand_type = value.hand_type
            hand_type_label = value.label
        else:
            hand = _hidden_hand()
            hand_type = None
            hand_type_label = None
        return {
            "player_id": player_id,
            "hand": hand,
            "hand_revealed": revealed,
            "hand_type": hand_type,
            "hand_type_label": hand_type_label,
            "seen": bool(player["seen"]),
            "status": str(player["status"]),
            "contribution": int(player["contribution"]),
            "legal_actions": self.legal_actions_for(state, player_id),
        }

    def participant_summary(
        self,
        state: dict[str, Any],
        participant: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, str | int | bool | None]:
        del participants
        player = state.get("players", {}).get(str(participant["player_id"]), {})
        return {
            "seen": bool(player.get("seen", False)),
            "hand_status": str(player.get("status", "active")),
            "virtual_bet": int(player.get("contribution", 0)),
        }

    def npc_compact_rules(
        self,
        state: dict[str, Any],
        actor: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> str:
        del state, actor, participants
        return (
            "固定炸金花：豹子>同花顺>金花>顺子>对子>散牌；A23 最小顺，AKQ 最大顺；"
            "不比花色、无 235 特权，完全同点数时主动比牌方出局。未看牌付闷注档，"
            "已看牌付双倍；虚拟投入每人封顶 32、闷注最高 8、20 轮强制摊牌。"
            "房间底注等于每个虚拟单位的真实价值，最大亏损 32 倍房间底注；仅在终局"
            "一次性零和结算，轮次封顶的最高牌精确并列则全部退款。"
            "不要自行计算下注档位或目标，只能原样选择 authoritative legal_actions。"
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
        del state, actor
        action = move.get("action")
        if action == "peek":
            return "看牌"
        if action == "call":
            return f"跟注 {move.get('cost')}"
        if action == "raise":
            return f"加注至 {move.get('unit')} 档"
        if action == "fold":
            return "弃牌"
        if action == "compare":
            return f"比牌 {move.get('target_player_id')}"
        return "炸金花行动"

    def format_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> str:
        del state, mark
        return str(move.get("action", "zhajinhua"))
