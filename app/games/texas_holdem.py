from __future__ import annotations

import random
from copy import deepcopy
from typing import Any

from third_party.pypokerengine.engine.card import Card
from third_party.pypokerengine.engine.deck import Deck
from third_party.pypokerengine.engine.game_evaluator import GameEvaluator
from third_party.pypokerengine.engine.hand_evaluator import HandEvaluator
from third_party.pypokerengine.engine.pay_info import PayInfo
from third_party.pypokerengine.engine.player import Player
from third_party.pypokerengine.engine.poker_constants import PokerConstants as Const
from third_party.pypokerengine.engine.round_manager import RoundManager
from third_party.pypokerengine.engine.table import Table

from .base import GamePlugin, MoveResult


INITIAL_STACK = 200
SMALL_BLIND = 5
BIG_BLIND = 10
STREET_NAMES = {
    Const.Street.PREFLOP: "preflop",
    Const.Street.FLOP: "flop",
    Const.Street.TURN: "turn",
    Const.Street.RIVER: "river",
    Const.Street.SHOWDOWN: "showdown",
    Const.Street.FINISHED: "finished",
}
SUIT_NAMES = {
    Card.SPADE: "spades",
    Card.HEART: "hearts",
    Card.DIAMOND: "diamonds",
    Card.CLUB: "clubs",
}
HAND_LABELS = {
    "HIGHCARD": "高牌",
    "ONEPAIR": "一对",
    "TWOPAIR": "两对",
    "THREECARD": "三条",
    "STRAIGHT": "顺子",
    "FLASH": "同花",
    "FULLHOUSE": "葫芦",
    "FOURCARD": "四条",
    "STRAIGHTFLASH": "同花顺",
}


class TexasHoldem(GamePlugin):
    """One production-style no-limit Texas Hold'em hand."""

    game_type = "texas_holdem"
    display_name = "德州扑克"
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
        "【单手牌与内部筹码】\n"
        "2–6 人，只进行一手 No-Limit Texas Hold'em。每席固定 200 内部筹码，"
        "小盲 5、大盲 10、无 ante；牌局结算后房间立即结束。这 200 枚是归一化的"
        "内部牌局筹码；房间 stake 是每名参与者投入真实娱乐筹码池的完整买入额，"
        "不是每枚内部筹码的单价，因此每席最大真实亏损仅为 stake。牌局进行中只维护"
        "内部栈与内部底池，不读取、锁定或移动钱包筹码，也不存在钱包底池。\n\n"
        "【按钮、盲注与行动顺序】\n"
        "多人桌按钮左侧依次为小盲、大盲，preflop 从大盲左侧开始；flop、turn、"
        "river 均从按钮左侧首个仍可行动席位开始。Heads-up 时按钮同时是小盲并"
        "在 preflop 先行动，大盲在所有 postflop 街先行动。\n\n"
        "【下注】\n"
        "完整支持 fold/check/call/bet/raise/all-in。开池最小下注为 10；加注至少"
        "等于本街最后一次完整 bet/raise 的增量。小于完整增量的 short all-in 合法，"
        "但单次不足额加注不会为已经行动者重新开放加注权；连续不足额加注累计达到"
        "一个完整增量时重新开放。下注额均由服务端给出的权威合法行动边界裁定。"
        "边界裁定。\n\n"
        "【发牌、底池与摊牌】\n"
        "依次完成 preflop、flop、turn、river；无人可继续行动时自动发完公共牌。"
        "弃牌只剩一人时立即结算且不公开任何底牌。多人 all-in 按总投入自动分 main/"
        "side pots，弃牌者无获奖资格；平分底池，无法整除的 odd chip 从按钮左侧首位"
        "符合资格的赢家开始分配。摊牌比较每人七张牌中的最佳五张；A 可作顺子低端。"
        "摊牌仅公开仍未弃牌且参与裁判的底牌，folded 手牌永不公开。\n\n"
        "【真实买入终局结算】\n"
        "引擎派奖后，各席最终内部栈总和必须为人数×200；真实总买入池人数×stake 按"
        "最终栈持有比例分配，即理想实收为 final_stack×stake/200。整数筹码采用确定性"
        "最大余数法分配，余数并列时按参与者座位顺序；因此平分、多赢家、边池和全下"
        "都以最终引擎栈归属为准，不能因 result.draw 而退款。钱包只在终局通过零和"
        "settlement_deltas 一次性变动。"
    )
    move_format = (
        "只能依据 private_state.legal_actions 行动。check/fold 原样提交；call/all_in "
        "使用服务端对象；bet/raise 的 amount 表示本街下注总额，必须位于服务端对象的 "
        "min_amount..max_amount（含端点）内。"
    )

    def __init__(self, rng: random.Random | None = None) -> None:
        self._rng = rng or random.SystemRandom()

    @staticmethod
    def _waiting_state(participants: list[dict[str, Any]]) -> dict[str, Any]:
        order = [str(item["player_id"]) for item in participants]
        return {
            "board_kind": "texas_holdem",
            "participant_order": order,
            "engine_state": None,
            "street": "waiting",
            "turn_player_id": order[0] if order else None,
            "dealer_player_id": order[0] if order else None,
            "small_blind_player_id": None,
            "big_blind_player_id": None,
            "visible_board": [],
            "betting": {
                "street": "waiting",
                "last_full_raise_size": BIG_BLIND,
                "acted_at_bet_by_player": {},
                "acted_facing_wager_by_player": {},
            },
            "action_history": [],
            "last_action": None,
            "game_result": None,
            "showdown": {},
            "initial_chip_total": INITIAL_STACK * len(order),
        }

    def initial_state(self) -> dict[str, Any]:
        return self._waiting_state([])

    def tokens_for(self, participants: list[dict[str, Any]]) -> list[str]:
        return [f"P{index + 1}" for index, _item in enumerate(participants)]

    def initialize(self, participants: list[dict[str, Any]]) -> dict[str, Any]:
        ordered = sorted(participants, key=lambda item: item.get("seat_index", 0))
        if not ordered:
            return self._waiting_state([])
        return self.initialize_for_first_player(
            participants, str(ordered[0]["player_id"])
        )

    def initialize_for_first_player(
        self,
        participants: list[dict[str, Any]],
        first_player_id: str,
    ) -> dict[str, Any]:
        ordered = sorted(participants, key=lambda item: item.get("seat_index", 0))
        if not 1 <= len(ordered) <= self.max_players:
            raise ValueError("德州扑克只支持 2–6 人，等待房允许先创建 1 个席位")
        state = self._waiting_state(ordered)
        if len(ordered) == 1:
            return state

        order = state["participant_order"]
        if first_player_id not in order:
            raise ValueError("德州扑克开局行动者必须属于本桌")
        opener_pos = order.index(first_player_id)
        if len(order) == 2:
            dealer_pos = opener_pos
            sb_pos = dealer_pos
            bb_pos = (dealer_pos + 1) % 2
        else:
            dealer_pos = (opener_pos - 3) % len(order)
            sb_pos = (dealer_pos + 1) % len(order)
            bb_pos = (dealer_pos + 2) % len(order)

        deck_ids = list(range(1, 53))
        self._rng.shuffle(deck_ids)
        table = Table(cheat_deck=Deck(deck_ids=deck_ids, cheat=True))
        table.dealer_btn = dealer_pos
        table.set_blind_pos(sb_pos, bb_pos)
        for participant in ordered:
            table.seats.sitdown(Player(
                str(participant["player_id"]),
                INITIAL_STACK,
                str(participant.get("display_name") or participant["player_id"]),
            ))

        engine, _messages = RoundManager.start_new_round(
            1, SMALL_BLIND, 0, table
        )
        state["engine_state"] = self._serialize_engine(engine)
        state["street"] = "preflop"
        state["turn_player_id"] = self._engine_turn_player_id(engine)
        state["dealer_player_id"] = order[dealer_pos]
        state["small_blind_player_id"] = order[sb_pos]
        state["big_blind_player_id"] = order[bb_pos]
        state["betting"]["street"] = "preflop"
        state["visible_board"] = []
        self._assert_chip_conservation(state)
        return state

    def resolve_opening_player_id(
        self,
        state: dict[str, Any],
        proposed_player_id: str,
        participants: list[dict[str, Any]],
    ) -> str:
        del participants
        return str(state.get("turn_player_id") or proposed_player_id)

    @staticmethod
    def _serialize_engine(engine: dict[str, Any]) -> dict[str, Any]:
        result = {
            "round_count": int(engine["round_count"]),
            "small_blind_amount": int(engine["small_blind_amount"]),
            "street": int(engine["street"]),
            "next_player": engine["next_player"],
            "table": engine["table"].serialize(),
        }
        if isinstance(engine.get("round_result"), dict):
            result["round_result"] = deepcopy(engine["round_result"])
        return result

    @staticmethod
    def _deserialize_engine(state: dict[str, Any]) -> dict[str, Any]:
        raw = state.get("engine_state")
        if not isinstance(raw, dict):
            raise ValueError("德州扑克引擎尚未开始")
        engine = {
            "round_count": int(raw["round_count"]),
            "small_blind_amount": int(raw["small_blind_amount"]),
            "street": int(raw["street"]),
            "next_player": raw["next_player"],
            "table": Table.deserialize(raw["table"]),
        }
        if isinstance(raw.get("round_result"), dict):
            engine["round_result"] = deepcopy(raw["round_result"])
        return engine

    @staticmethod
    def _engine_turn_player_id(engine: dict[str, Any]) -> str | None:
        if engine["street"] == Const.Street.FINISHED:
            return None
        position = engine["next_player"]
        if isinstance(position, bool) or not isinstance(position, int):
            raise ValueError("扑克引擎未给出有效的下一行动席位")
        return str(engine["table"].seats.players[position].uuid)

    @staticmethod
    def _card(card: Card) -> dict[str, str]:
        return {
            "rank": "10" if card.rank == 10 else Card.RANK_MAP[card.rank],
            "suit": SUIT_NAMES[card.suit],
        }

    @classmethod
    def _cards(cls, cards: list[Card]) -> list[dict[str, str]]:
        return [cls._card(card) for card in cards]

    @staticmethod
    def _player(engine: dict[str, Any], player_id: str) -> Player:
        player = next(
            (
                item for item in engine["table"].seats.players
                if str(item.uuid) == player_id
            ),
            None,
        )
        if player is None:
            raise ValueError("参与者不在德州扑克引擎席位中")
        return player

    @staticmethod
    def _current_bet(engine: dict[str, Any]) -> int:
        return max(
            (player.paid_sum() for player in engine["table"].seats.players),
            default=0,
        )

    @staticmethod
    def _status(player: Player) -> str:
        if player.pay_info.status == PayInfo.FOLDED:
            return "folded"
        if player.pay_info.status == PayInfo.ALLIN:
            return "all_in"
        return "active"

    @classmethod
    def _pot_layers(cls, engine: dict[str, Any]) -> list[dict[str, Any]]:
        layers = []
        for index, pot in enumerate(
            GameEvaluator.create_pot(engine["table"].seats.players)
        ):
            if int(pot["amount"]) <= 0:
                continue
            layers.append({
                "name": "main" if not layers else f"side_{len(layers)}",
                "amount": int(pot["amount"]),
                "eligible_player_ids": [str(player.uuid) for player in pot["eligibles"]],
                "engine_layer_index": index,
            })
        return layers

    @classmethod
    def _legal_actions_for(
        cls, state: dict[str, Any], player_id: str
    ) -> list[dict[str, Any]]:
        if state.get("game_result") is not None:
            return []
        engine = cls._deserialize_engine(state)
        if cls._engine_turn_player_id(engine) != player_id:
            return []
        player = cls._player(engine, player_id)
        if not player.is_waiting_ask():
            return []

        paid = int(player.paid_sum())
        current_bet = cls._current_bet(engine)
        to_call = max(0, current_bet - paid)
        max_to = paid + int(player.stack)
        actions: list[dict[str, Any]] = [{"action": "fold"}]
        if to_call == 0:
            actions.insert(0, {"action": "check"})
        else:
            actions.insert(0, {
                "action": "call",
                "amount": min(to_call, int(player.stack)),
                "to_amount": min(current_bet, max_to),
                "all_in": int(player.stack) <= to_call,
            })

        betting = state.get("betting", {})
        last_full_raise = int(betting.get("last_full_raise_size", BIG_BLIND))
        acted_at = betting.get("acted_at_bet_by_player", {})
        faced_wager = betting.get("acted_facing_wager_by_player", {})
        previous_bet = acted_at.get(player_id) if isinstance(acted_at, dict) else None
        raise_reopened = (
            previous_bet is None
            or not bool(faced_wager.get(player_id, True))
            or current_bet - int(previous_bet) >= last_full_raise
        )
        if max_to > current_bet and raise_reopened:
            minimum_to = (
                BIG_BLIND
                if current_bet < BIG_BLIND
                else current_bet + last_full_raise
            )
            action_name = "bet" if current_bet == 0 else "raise"
            if max_to >= minimum_to:
                actions.append({
                    "action": action_name,
                    "amount": minimum_to,
                    "min_amount": minimum_to,
                    "max_amount": max_to,
                })
            actions.append({
                "action": "all_in",
                "amount": max_to,
                "cost": int(player.stack),
                "short_raise": max_to < minimum_to,
            })
        elif 0 < int(player.stack) <= to_call:
            actions.append({
                "action": "all_in",
                "amount": max_to,
                "cost": int(player.stack),
                "short_raise": False,
            })
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
        if state.get("turn_player_id") != player_id:
            raise ValueError("当前不是该参与者的回合")
        legal = self._legal_actions_for(state, player_id)
        action = move.get("action")
        if action in {"bet", "raise"}:
            template = next(
                (item for item in legal if item["action"] == action), None
            )
            amount = move.get("amount")
            if template is None:
                raise ValueError("当前未开放该下注或加注动作")
            if (
                isinstance(amount, bool)
                or not isinstance(amount, int)
                or not int(template["min_amount"])
                <= amount
                <= int(template["max_amount"])
            ):
                raise ValueError("amount 超出 authoritative 最小/最大加注边界")
            allowed_keys = {"action", "amount", "min_amount", "max_amount"}
            if set(move) - allowed_keys:
                raise ValueError("下注动作包含未知字段")
            for key in ("min_amount", "max_amount"):
                if key in move and move[key] != template[key]:
                    raise ValueError("客户端不得改写 authoritative 加注边界")
            return
        template = next((item for item in legal if item == move), None)
        if template is None:
            raise ValueError("动作不在当前 authoritative legal_actions 中")

    def validate_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> None:
        del state, move, mark
        raise ValueError("德州扑克需要 participant-aware action 接口")

    def apply_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> dict[str, Any]:
        del state, move, mark
        raise ValueError("德州扑克需要 participant-aware action 接口")

    def apply_action(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
    ) -> MoveResult:
        self.validate_action(state, move, actor)
        player_id = str(actor["player_id"])
        engine = self._deserialize_engine(state)
        player = self._player(engine, player_id)
        old_street = int(engine["street"])
        old_street_name = STREET_NAMES[old_street]
        old_board = self._cards(engine["table"].get_community_card())
        old_current_bet = self._current_bet(engine)
        old_paid = int(player.paid_sum())
        old_contribution = int(player.pay_info.amount)
        semantic_action = str(move["action"])

        if semantic_action in {"check", "call"}:
            engine_action = "call"
            engine_amount = old_current_bet
        elif semantic_action in {"bet", "raise"}:
            engine_action = "raise"
            engine_amount = int(move["amount"])
        elif semantic_action == "all_in":
            all_in_to = old_paid + int(player.stack)
            engine_action = "raise" if all_in_to > old_current_bet else "call"
            engine_amount = all_in_to if engine_action == "raise" else old_current_bet
        else:
            engine_action = "fold"
            engine_amount = 0

        applied_engine, _messages = RoundManager.apply_action(
            engine, engine_action, engine_amount
        )
        applied_player = self._player(applied_engine, player_id)
        paid_delta = int(applied_player.pay_info.amount) - old_contribution
        new_current_bet = self._current_bet(applied_engine)
        finished = applied_engine["street"] == Const.Street.FINISHED
        active_players = [
            item for item in applied_engine["table"].seats.players
            if item.pay_info.status != PayInfo.FOLDED
        ]

        record = {
            "sequence": len(state.get("action_history", [])) + 1,
            "player_id": player_id,
            "street": old_street_name,
            "action": semantic_action,
            "paid": paid_delta,
            "to_amount": (
                int(move.get("amount", old_paid + paid_delta))
                if semantic_action in {"bet", "raise", "all_in"}
                else old_paid + paid_delta
            ),
            "all_in": applied_player.pay_info.status == PayInfo.ALLIN,
        }
        state.setdefault("action_history", []).append(record)
        state["last_action"] = deepcopy(record)

        if not finished and int(applied_engine["street"]) == old_street:
            betting = state["betting"]
            if new_current_bet > old_current_bet:
                increment = new_current_bet - old_current_bet
                if increment >= int(betting["last_full_raise_size"]):
                    betting["last_full_raise_size"] = increment
            betting["acted_at_bet_by_player"][player_id] = new_current_bet
            betting.setdefault("acted_facing_wager_by_player", {})[player_id] = (
                old_current_bet > 0 or semantic_action != "check"
            )
        elif not finished:
            new_street_name = STREET_NAMES[int(applied_engine["street"])]
            state["betting"] = {
                "street": new_street_name,
                "last_full_raise_size": BIG_BLIND,
                "acted_at_bet_by_player": {},
                "acted_facing_wager_by_player": {},
            }

        state["engine_state"] = self._serialize_engine(applied_engine)
        state["street"] = STREET_NAMES[int(applied_engine["street"])]
        state["turn_player_id"] = self._engine_turn_player_id(applied_engine)
        if finished and len(active_players) == 1:
            # Upstream runs its board drawing recursion before settlement even
            # when everyone else folded. Those unneeded cards remain private.
            state["visible_board"] = old_board
        else:
            state["visible_board"] = self._cards(
                applied_engine["table"].get_community_card()
            )

        result = None
        note = self._action_note(record)
        if finished:
            result = self._finish(state, applied_engine, active_players)
            note = f"{note} {result['result_text']}"
        self._assert_chip_conservation(state)
        return MoveResult(
            state=state,
            next_player_id=state.get("turn_player_id"),
            note=note,
            result=result,
            participant_activity=(
                {player_id: "eliminated"}
                if semantic_action == "fold" else {}
            ),
        )

    @staticmethod
    def _action_note(action: dict[str, Any]) -> str:
        labels = {
            "fold": "弃牌",
            "check": "过牌",
            "call": "跟注",
            "bet": "下注",
            "raise": "加注",
            "all_in": "全下",
        }
        paid = int(action.get("paid", 0))
        suffix = f" {paid}" if paid else ""
        return f"{labels.get(str(action.get('action')), '行动')}{suffix}。"

    def _finish(
        self,
        state: dict[str, Any],
        engine: dict[str, Any],
        active_players: list[Player],
    ) -> dict[str, Any]:
        round_result = engine.get("round_result")
        if not isinstance(round_result, dict):
            raise ValueError("扑克引擎终局缺少 round_result")
        prize_by_player = {
            str(player_id): int(amount)
            for player_id, amount in round_result["prize_map"].items()
        }
        total_pot = sum(
            int(player.pay_info.amount) for player in engine["table"].seats.players
        )
        contributions = {
            str(player.uuid): int(player.pay_info.amount)
            for player in engine["table"].seats.players
        }
        winner_ids = [str(item) for item in round_result["winner_uuids"]]
        showdown: dict[str, Any] = {}
        finish_reason = "last_player_standing"
        if len(active_players) > 1:
            finish_reason = "showdown"
            board = engine["table"].get_community_card()
            for player in active_players:
                info = HandEvaluator.gen_hand_rank_info(player.hole_card, board)
                strength = str(info["hand"]["strength"])
                showdown[str(player.uuid)] = {
                    "cards": self._cards(player.hole_card),
                    "hand_type": strength,
                    "hand_type_label": HAND_LABELS.get(strength, strength),
                    "rank": deepcopy(info["hand"]),
                }
        state["showdown"] = showdown
        if len(winner_ids) == 1:
            result: dict[str, Any] = {
                "winner_player_id": winner_ids[0],
                "draw": False,
            }
        else:
            result = {"draw": True, "tied_player_ids": winner_ids}
        result.update({
            "terminal_result": "texas_holdem_hand",
            "finish_reason": finish_reason,
            "winner_player_ids": winner_ids,
            "total_pot": total_pot,
            "contributions_by_player": contributions,
            "payout_by_player": prize_by_player,
            "final_internal_stacks_by_player": {
                str(player.uuid): int(player.stack)
                for player in engine["table"].seats.players
            },
            "stake_settlement": {
                "real_buy_in_per_player": "room_stake",
                "ideal_payout_formula": "final_internal_stack*room_stake/200",
                "rounding": "largest_remainder",
                "remainder_tie_break": "participant_seat_order",
                "timing": "terminal_only",
                "wallet_pot_during_hand": False,
            },
            "pots": deepcopy(round_result["pots"]),
            "result_text": (
                f"摊牌结算 {total_pot} 内部筹码；终局按最终内部栈比例分配真实总买入池，"
                "房间 stake 是每席完整买入（单席最多亏 stake，并非内部筹码单价）；"
                "以座位顺序确定最大余数取整，钱包仅在此时一次性零和变动。"
                if finish_reason == "showdown"
                else f"其余玩家均弃牌，未公开底牌；结算 {total_pot} 内部筹码。终局按"
                "最终内部栈比例分配真实总买入池；stake 是每席完整买入且单席最多亏"
                " stake，钱包仅在此时一次性零和变动。"
            ),
        })
        state["game_result"] = deepcopy(result)
        state["turn_player_id"] = None
        return result

    @classmethod
    def _assert_chip_conservation(cls, state: dict[str, Any]) -> None:
        raw = state.get("engine_state")
        if raw is None:
            return
        engine = cls._deserialize_engine(state)
        players = engine["table"].seats.players
        expected = int(state["initial_chip_total"])
        stacks = sum(int(player.stack) for player in players)
        contributions = sum(int(player.pay_info.amount) for player in players)
        if state.get("game_result") is None:
            if stacks + contributions != expected:
                raise ValueError("德州扑克内部筹码与底池不守恒")
        elif stacks != expected:
            raise ValueError("德州扑克结算后内部筹码不守恒")
        result = state.get("game_result")
        if isinstance(result, dict) and sum(result["payout_by_player"].values()) != contributions:
            raise ValueError("德州扑克派奖总额与底池不一致")

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
        delta: dict[str, Any] = {
            "action": deepcopy(applied.state["last_action"]),
            "street": public["street"],
            "board": deepcopy(public["board"]),
            "pot": public["pot"],
            "total_pot": public["total_pot"],
            "pots": deepcopy(public["pots"]),
            "players": deepcopy(public["players"]),
            "next_player_id": public["turn_player_id"],
        }
        if applied.state.get("game_result") is not None:
            delta["showdown"] = deepcopy(public["showdown"])
            delta["result"] = deepcopy(public["game_result"])
        applied.public_event = {"texas_holdem_delta": delta}
        return applied

    def result_for(
        self,
        state: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        del participants
        result = state.get("game_result")
        return deepcopy(result) if isinstance(result, dict) else None

    @staticmethod
    def _participant_ids_in_seat_order(
        participants: list[dict[str, Any]],
    ) -> list[str]:
        indexed = list(enumerate(participants))

        def seat_key(item: tuple[int, dict[str, Any]]) -> tuple[int, int]:
            index, participant = item
            seat = participant.get("seat_index")
            return (
                seat
                if isinstance(seat, int) and not isinstance(seat, bool)
                else index,
                index,
            )

        return [
            str(participant["player_id"])
            for _index, participant in sorted(indexed, key=seat_key)
        ]

    @classmethod
    def _apportion_real_payouts(
        cls,
        final_stacks_by_player: dict[str, int],
        participants: list[dict[str, Any]],
        stake: int,
    ) -> dict[str, int]:
        if isinstance(stake, bool) or not isinstance(stake, int) or stake <= 0:
            raise ValueError("德州扑克房间 stake 必须是正整数买入额")
        player_ids = cls._participant_ids_in_seat_order(participants)
        if len(player_ids) != len(set(player_ids)) or not 2 <= len(player_ids) <= 6:
            raise ValueError("德州扑克结算参与者必须是 2–6 个唯一席位")
        if set(final_stacks_by_player) != set(player_ids):
            raise ValueError("德州扑克终局内部栈必须完整覆盖所有参与者")
        if any(
            isinstance(stack, bool) or not isinstance(stack, int) or stack < 0
            for stack in final_stacks_by_player.values()
        ):
            raise ValueError("德州扑克终局内部栈必须是非负整数")
        expected_internal_total = len(player_ids) * INITIAL_STACK
        if sum(final_stacks_by_player.values()) != expected_internal_total:
            raise ValueError("德州扑克终局内部栈总和必须等于人数×200")

        payouts: dict[str, int] = {}
        remainders: dict[str, int] = {}
        for player_id in player_ids:
            payout, remainder = divmod(
                final_stacks_by_player[player_id] * stake,
                INITIAL_STACK,
            )
            payouts[player_id] = payout
            remainders[player_id] = remainder
        real_pool = len(player_ids) * stake
        remaining = real_pool - sum(payouts.values())
        if not 0 <= remaining < len(player_ids):
            raise ValueError("德州扑克真实买入池取整余数无效")
        seat_position = {
            player_id: index for index, player_id in enumerate(player_ids)
        }
        ranked = sorted(
            player_ids,
            key=lambda player_id: (
                -remainders[player_id], seat_position[player_id]
            ),
        )
        for player_id in ranked[:remaining]:
            payouts[player_id] += 1
        if sum(payouts.values()) != real_pool:
            raise ValueError("德州扑克真实买入池分配不守恒")
        return {player_id: payouts[player_id] for player_id in player_ids}

    def settlement_deltas(
        self,
        state: dict[str, Any],
        result: dict[str, Any],
        participants: list[dict[str, Any]],
        stake: int,
    ) -> dict[str, int]:
        engine = self._deserialize_engine(state)
        final_stacks = {
            str(player.uuid): player.stack
            for player in engine["table"].seats.players
        }
        expected_internal_total = len(participants) * INITIAL_STACK
        if sum(final_stacks.values()) != expected_internal_total:
            contributions = {
                str(player.uuid): player.pay_info.amount
                for player in engine["table"].seats.players
            }
            if (
                any(
                    isinstance(amount, bool) or not isinstance(amount, int)
                    or amount < 0
                    for amount in contributions.values()
                )
                or sum(final_stacks.values()) + sum(contributions.values())
                != expected_internal_total
            ):
                raise ValueError("德州扑克终局前内部栈与底池不守恒")
            winner = result.get("winner_player_id")
            if result.get("draw") or winner not in final_stacks:
                raise ValueError("未完成引擎派奖的德州扑克终局必须有唯一赢家")
            # Framework-level resignation/leave can end a hand without asking
            # PyPokerEngine to apply one final fold.  Its exact engine-equivalent
            # payout is the whole committed pot to the sole remaining player;
            # every seat retains its uncommitted internal stack.
            final_stacks[str(winner)] += sum(contributions.values())
        payouts = self._apportion_real_payouts(
            final_stacks, participants, stake
        )
        deltas = {
            player_id: payout - stake
            for player_id, payout in payouts.items()
        }
        if sum(deltas.values()) != 0:
            raise ValueError("德州扑克终局真实筹码差额不守恒")
        if any(delta < -stake for delta in deltas.values()):
            raise ValueError("德州扑克单席真实亏损超过 stake 买入额")
        return deltas

    def check_winner(self, state: dict[str, Any]) -> str | None:
        result = state.get("game_result")
        if not isinstance(result, dict):
            return None
        return "draw" if result.get("draw") else None

    def public_state(
        self,
        state: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any]:
        del participants
        if state.get("engine_state") is None:
            return {
                "board_kind": "texas_holdem",
                "street": "waiting",
                "participant_order": list(state.get("participant_order", [])),
                "turn_player_id": state.get("turn_player_id"),
                "players": {
                    player_id: {
                        "stack": INITIAL_STACK,
                        "current_bet": 0,
                        "contribution": 0,
                        "status": "waiting",
                    }
                    for player_id in state.get("participant_order", [])
                },
                "board": [],
                "pot": 0,
                "total_pot": 0,
                "pots": [],
                "showdown": {},
                "action_history": [],
                "game_result": None,
            }
        self._assert_chip_conservation(state)
        engine = self._deserialize_engine(state)
        finished = state.get("game_result") is not None
        players = {
            str(player.uuid): {
                "stack": int(player.stack),
                "current_bet": 0 if finished else int(player.paid_sum()),
                "contribution": int(player.pay_info.amount),
                "status": self._status(player),
            }
            for player in engine["table"].seats.players
        }
        total_pot = sum(item["contribution"] for item in players.values())
        if finished:
            result_pots = state["game_result"].get("pots", [])
            pots = [
                {
                    "name": "main" if index == 0 else f"side_{index}",
                    "amount": int(pot["amount"]),
                    "eligible_player_ids": list(pot["eligible_uuids"]),
                    "winner_player_ids": list(pot["winner_uuids"]),
                }
                for index, pot in enumerate(result_pots)
                if int(pot["amount"]) > 0
            ]
        else:
            pots = self._pot_layers(engine)
            for pot in pots:
                pot.pop("engine_layer_index", None)
        return {
            "board_kind": "texas_holdem",
            "street": str(state["street"]),
            "participant_order": list(state["participant_order"]),
            "turn_player_id": state.get("turn_player_id"),
            "dealer_player_id": state.get("dealer_player_id"),
            "small_blind_player_id": state.get("small_blind_player_id"),
            "big_blind_player_id": state.get("big_blind_player_id"),
            "initial_stack": INITIAL_STACK,
            "small_blind": SMALL_BLIND,
            "big_blind": BIG_BLIND,
            "players": players,
            "board": deepcopy(state.get("visible_board", [])),
            "pot": 0 if finished else total_pot,
            "total_pot": total_pot,
            "pots": pots,
            "showdown": deepcopy(state.get("showdown", {})),
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
        if state.get("engine_state") is None:
            return {"player_id": player_id, "hand": [], "legal_actions": []}
        engine = self._deserialize_engine(state)
        player = self._player(engine, player_id)
        return {
            "player_id": player_id,
            "hand": self._cards(player.hole_card),
            "stack": int(player.stack),
            "current_bet": int(player.paid_sum()),
            "contribution": int(player.pay_info.amount),
            "status": self._status(player),
            "legal_actions": self._legal_actions_for(state, player_id),
        }

    def mcp_snapshot_state(
        self,
        public_state: dict[str, Any],
        viewer: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any]:
        snapshot = super().mcp_snapshot_state(
            public_state, viewer, participants
        )
        return snapshot

    def participant_summary(
        self,
        state: dict[str, Any],
        participant: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, str | int | bool | None]:
        del participants
        player = state.get("players", {}).get(str(participant["player_id"]), {})
        return {
            "stack": player.get("stack"),
            "bet": player.get("current_bet"),
            "hand_status": player.get("status"),
        }

    def npc_compact_rules(
        self,
        state: dict[str, Any],
        actor: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> str:
        del state, actor, participants
        return (
            "单手牌 NLHE，200 内部筹码，盲注 5/10。房间 stake 是每席完整真实买入，"
            "不是内部筹码单价，最大亏损为 stake；仅在终局按最终内部栈比例和座位顺序"
            "最大余数取整做一次性零和结算。不要自行推导下注边界；"
            "只能原样选择 authoritative legal_actions 中的对象。"
        )

    def npc_public_actions(
        self,
        state: dict[str, Any],
        actor: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        del actor, participants
        return deepcopy(state.get("action_history", [])[-32:])

    def npc_legal_actions(
        self,
        state: dict[str, Any],
        actor: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        del participants
        return self._legal_actions_for(state, str(actor["player_id"]))

    def choose_local_npc_action(
        self,
        state: dict[str, Any],
        actor: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        legal = self.npc_legal_actions(state, actor, participants)
        for preferred in ("check", "call", "fold"):
            chosen = next(
                (item for item in legal if item.get("action") == preferred), None
            )
            if chosen is not None:
                return deepcopy(chosen)
        return deepcopy(legal[0]) if legal else None

    def format_action(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
    ) -> str:
        del state, actor
        labels = {
            "fold": "弃牌",
            "check": "过牌",
            "call": "跟注",
            "bet": "下注",
            "raise": "加注",
            "all_in": "全下",
        }
        label = labels.get(str(move.get("action")), "德州扑克行动")
        amount = move.get("amount")
        return f"{label} {amount}" if isinstance(amount, int) else label
