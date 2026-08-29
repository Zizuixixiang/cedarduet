from __future__ import annotations

import random
from copy import deepcopy
from typing import Any, Iterable

from .base import GamePlugin, MoveResult
from .tools import advance_flow, draw_cards, ensure_card_zones, ensure_flow


SHOE_DECKS = 4
DEALER_ID = "__dealer__"
SUITS = ("spades", "hearts", "diamonds", "clubs")
RANKS = ("A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K")
TEN_VALUE_RANKS = frozenset({"10", "J", "Q", "K"})


def build_shoe(decks: int = SHOE_DECKS) -> list[dict[str, str | int]]:
    """Return uniquely identified cards for a standard multi-deck shoe."""
    if isinstance(decks, bool) or not isinstance(decks, int) or decks < 1:
        raise ValueError("decks 必须是正整数")
    return [
        {
            "card_id": f"d{deck_index}-{suit}-{rank}",
            "deck": deck_index,
            "suit": suit,
            "rank": rank,
        }
        for deck_index in range(1, decks + 1)
        for suit in SUITS
        for rank in RANKS
    ]


def hand_value(hand: Iterable[dict[str, Any]]) -> dict[str, int | bool | str]:
    """Score a Blackjack hand, counting aces as 1 or 11 for the best total."""
    cards = list(hand)
    total = 0
    ace_count = 0
    for card in cards:
        if not isinstance(card, dict):
            raise ValueError("手牌必须由牌对象组成")
        rank = card.get("rank")
        if rank not in RANKS:
            raise ValueError("手牌包含无效点数")
        if rank == "A":
            total += 11
            ace_count += 1
        elif rank in TEN_VALUE_RANKS:
            total += 10
        else:
            total += int(rank)
    lowered_aces = 0
    while total > 21 and lowered_aces < ace_count:
        total -= 10
        lowered_aces += 1
    soft = ace_count > lowered_aces
    blackjack = len(cards) == 2 and total == 21
    bust = total > 21
    if blackjack:
        label = "Blackjack"
    elif bust:
        label = f"爆牌 {total}"
    else:
        label = f"{'软' if soft else '硬'} {total}"
    return {
        "total": total,
        "soft": soft,
        "hard": not soft,
        "blackjack": blackjack,
        "bust": bust,
        "label": label,
    }


class Blackjack(GamePlugin):
    """One persisted multiplayer table playing against a wallet-less dealer."""

    game_type = "blackjack"
    display_name = "21点"
    category = "card"
    min_players = 2
    max_players = 6
    allowed_player_counts = (2, 3, 4, 5, 6)
    recommended_players = 4
    supports_npcs = True
    # The shared settlement layer is participant-to-participant zero sum. The
    # virtual dealer deliberately has no participant record or wallet.
    supports_stakes = False
    supports_multiplayer_stakes = False
    rules_text = (
        "2–6 名参与者共同对抗虚拟庄家；庄家不是参与者，也没有钱包。本房间只进行一局。"
        "使用固定 4 副标准 52 张牌的 shoe，服务端洗牌、抽牌并持久化，刷新或重启不会重洗；"
        "若开始下一局前余牌不足完成所有席位与庄家的两张初始发牌，则把余牌与弃牌合并重洗。"
        "每位参与者发 2 张，庄家发 2 张且第二张为暗牌。参与者按座位依次选择要牌（hit）"
        "或停牌（stand）；爆牌立即结束该手。A 自动按 1 或 11 计为不爆牌的最优点数。"
        "所有参与者结束后庄家翻开暗牌并自动补牌；庄家在任意 17 点停牌，包括软 17（S17），"
        "16 点及以下要牌。庄家爆牌时所有未爆参与者获胜。首两张 A 加任意 10 值牌是自然"
        "Blackjack：它胜过庄家的非自然 21；双方同为自然 Blackjack 时推和。其余按点数"
        "高低决定胜、负或推和。第一版不支持 split、double、insurance 或 surrender。"
        "本版仅支持 0 筹码娱乐局，不 mint/burn 任何筹码。"
    )
    move_format = (
        '要牌：{"move":{"action":"hit"},"revision":当前版本}；'
        '停牌：{"move":{"action":"stand"},"revision":当前版本}。'
        "只能从 private_state.legal_actions 选择。"
    )

    def __init__(self, rng: random.Random | None = None) -> None:
        self._rng = rng or random.SystemRandom()

    @staticmethod
    def _state_skeleton() -> dict[str, Any]:
        state: dict[str, Any] = {
            "board_kind": "blackjack",
            "participant_order": [],
            "player_status_by_player": {},
            "turn_player_id": None,
            "dealer_hole_revealed": False,
            "dealer_status": "hidden",
            "shoe_decks": SHOE_DECKS,
            "shoe_shuffle_count": 1,
            "action_history": [],
            "outcomes_by_player": {},
            "game_result": None,
            "result_text": "",
        }
        ensure_flow(state, phase="player_turns")
        return state

    def initial_state(self) -> dict[str, Any]:
        return self._state_skeleton()

    @staticmethod
    def cards_required_to_deal(player_count: int) -> int:
        if (
            isinstance(player_count, bool)
            or not isinstance(player_count, int)
            or not 2 <= player_count <= 6
        ):
            raise ValueError("21点只支持 2–6 名参与者")
        return 2 * (player_count + 1)

    def prepare_round_shoe(
        self, state: dict[str, Any], player_ids: Iterable[str]
    ) -> bool:
        """Clear old hands and reshuffle iff the next initial deal needs it."""
        ordered_ids = [str(player_id) for player_id in player_ids]
        if len(ordered_ids) != len(set(ordered_ids)) or not ordered_ids:
            raise ValueError("player_ids 必须非空且不能重复")
        zones = state.get("cards")
        if not isinstance(zones, dict):
            raise ValueError("牌区尚未初始化")
        hands = zones.get("hands")
        deck = zones.get("deck")
        discard = zones.get("discard")
        expected_hand_ids = {*ordered_ids, DEALER_ID}
        if (
            not isinstance(hands, dict)
            or set(hands) != expected_hand_ids
            or not isinstance(deck, list)
            or not isinstance(discard, list)
            or any(not isinstance(hand, list) for hand in hands.values())
        ):
            raise ValueError("21点牌区结构无效")

        for hand in hands.values():
            discard.extend(hand)
            hand.clear()
        required = self.cards_required_to_deal(len(ordered_ids))
        reshuffled = len(deck) < required
        if reshuffled:
            deck.extend(discard)
            discard.clear()
            self._rng.shuffle(deck)
            state["shoe_shuffle_count"] = int(state.get("shoe_shuffle_count", 1)) + 1
        return reshuffled

    def initialize(self, participants: list[dict[str, Any]]) -> dict[str, Any]:
        if not self.accepts_player_count(len(participants)):
            raise ValueError("21点只支持 2–6 名参与者")
        state = self._state_skeleton()
        order = [str(participant["player_id"]) for participant in participants]
        state["participant_order"] = order
        ensure_card_zones(
            state,
            build_shoe(),
            [*order, DEALER_ID],
            rng=self._rng,
        )
        self.prepare_round_shoe(state, order)

        # Casino-style round-robin initial deal: one face-up card per seat and
        # dealer, followed by the second card in the same order.
        for _deal_index in range(2):
            for player_id in order:
                self._draw_one(state, player_id)
            self._draw_one(state, DEALER_ID)

        for player_id in order:
            value = self._value_for(state, player_id)
            state["player_status_by_player"][player_id] = (
                "blackjack" if value["blackjack"] else "playing"
            )
        return state

    def prepare_opening_state(
        self,
        state: dict[str, Any],
        first_player_id: str,
        participants: list[dict[str, Any]],
    ) -> dict[str, Any]:
        participant_ids = {str(participant["player_id"]) for participant in participants}
        if first_player_id not in participant_ids:
            raise ValueError("21点开局行动者不属于本桌")
        state["turn_player_id"] = first_player_id
        return state

    def resolve_opening_player_id(
        self,
        state: dict[str, Any],
        proposed_player_id: str,
        participants: list[dict[str, Any]],
    ) -> str:
        del participants
        order = state["participant_order"]
        start = order.index(proposed_player_id)
        for offset in range(len(order)):
            candidate = order[(start + offset) % len(order)]
            if state["player_status_by_player"].get(candidate) == "playing":
                return candidate
        # An all-natural table needs one harmless stand acknowledgement so the
        # normal transactional move path can reveal and settle the dealer.
        return proposed_player_id

    @staticmethod
    def _hands(state: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        zones = state.get("cards")
        hands = zones.get("hands") if isinstance(zones, dict) else None
        if not isinstance(hands, dict):
            raise ValueError("21点牌区尚未初始化")
        return hands

    @classmethod
    def _hand_for(cls, state: dict[str, Any], player_id: str) -> list[dict[str, Any]]:
        hand = cls._hands(state).get(player_id)
        if not isinstance(hand, list):
            raise ValueError("参与者不在 21 点牌区中")
        return hand

    @classmethod
    def _value_for(cls, state: dict[str, Any], player_id: str) -> dict[str, Any]:
        return hand_value(cls._hand_for(state, player_id))

    @staticmethod
    def _public_card(card: dict[str, Any]) -> dict[str, str]:
        rank = card.get("rank")
        suit = card.get("suit")
        if rank not in RANKS or suit not in SUITS:
            raise ValueError("牌面数据无效")
        return {"rank": str(rank), "suit": str(suit)}

    def _draw_one(self, state: dict[str, Any], player_id: str) -> dict[str, Any]:
        drawn = draw_cards(state, player_id, 1)
        if len(drawn) != 1:
            raise ValueError("shoe 已无可抽取的牌")
        return drawn[0]

    @staticmethod
    def _status_label(status: str) -> str:
        return {
            "playing": "行动中",
            "stood": "已停牌",
            "bust": "爆牌",
            "blackjack": "Blackjack",
        }.get(status, status)

    def _legal_actions_for(self, state: dict[str, Any], player_id: str) -> list[dict[str, str]]:
        if state.get("flow", {}).get("phase") != "player_turns":
            return []
        turn_player_id = state.get("turn_player_id")
        if turn_player_id is not None and turn_player_id != player_id:
            return []
        status = state.get("player_status_by_player", {}).get(player_id)
        if status == "blackjack":
            # Only relevant for the all-natural edge case; otherwise natural
            # seats are skipped before the first action and during rotation.
            return [{"action": "stand"}]
        if status != "playing":
            return []
        value = self._value_for(state, player_id)
        actions = [{"action": "stand"}]
        if int(value["total"]) < 21:
            actions.insert(0, {"action": "hit"})
        return actions

    def validate_action(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
    ) -> None:
        if not isinstance(move, dict) or set(move) != {"action"}:
            raise ValueError("动作只接受 action 字段")
        player_id = str(actor["player_id"])
        if player_id not in state.get("participant_order", []):
            raise ValueError("行动者不属于本桌")
        if state.get("flow", {}).get("phase") != "player_turns":
            raise ValueError("参与者行动阶段已经结束")
        turn_player_id = state.get("turn_player_id")
        if turn_player_id is not None and turn_player_id != player_id:
            raise ValueError("当前手牌属于另一名参与者的回合")
        legal = self._legal_actions_for(state, player_id)
        if move not in legal:
            raise ValueError("action 不是当前服务端允许的 hit/stand")

    def validate_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> None:
        del state, move, mark
        raise ValueError("21点需要 participant-aware action 接口")

    def apply_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> dict[str, Any]:
        del state, move, mark
        raise ValueError("21点需要 participant-aware action 接口")

    @staticmethod
    def _next_playing_after(state: dict[str, Any], player_id: str) -> str | None:
        order = state["participant_order"]
        current_index = order.index(player_id)
        for offset in range(1, len(order)):
            candidate = order[(current_index + offset) % len(order)]
            if state["player_status_by_player"].get(candidate) == "playing":
                return candidate
        return None

    def _dealer_play(self, state: dict[str, Any]) -> None:
        flow = state["flow"]
        flow["phase"] = "dealer_turn"
        state["dealer_hole_revealed"] = True
        state["dealer_status"] = "playing"
        while True:
            value = self._value_for(state, DEALER_ID)
            if value["bust"]:
                state["dealer_status"] = "bust"
                break
            # S17: all 17s, including a soft 17, stand.
            if int(value["total"]) >= 17:
                state["dealer_status"] = (
                    "blackjack" if value["blackjack"] else "stood"
                )
                break
            self._draw_one(state, DEALER_ID)

    def _outcome_for(self, state: dict[str, Any], player_id: str) -> dict[str, Any]:
        player_value = self._value_for(state, player_id)
        dealer_value = self._value_for(state, DEALER_ID)
        if player_value["bust"]:
            outcome, text = "loss", "爆牌，负"
        elif player_value["blackjack"]:
            if dealer_value["blackjack"]:
                outcome, text = "push", "双方自然 Blackjack，推和"
            else:
                outcome, text = "win", "自然 Blackjack，胜"
        elif dealer_value["blackjack"]:
            outcome, text = "loss", "庄家自然 Blackjack，负"
        elif dealer_value["bust"]:
            outcome, text = "win", "庄家爆牌，胜"
        elif int(player_value["total"]) > int(dealer_value["total"]):
            outcome, text = "win", "点数高于庄家，胜"
        elif int(player_value["total"]) < int(dealer_value["total"]):
            outcome, text = "loss", "点数低于庄家，负"
        else:
            outcome, text = "push", "与庄家同点，推和"
        return {
            "player_id": player_id,
            "outcome": outcome,
            "result_text": text,
            "total": int(player_value["total"]),
            "soft": bool(player_value["soft"]),
            "natural_blackjack": bool(player_value["blackjack"]),
            "bust": bool(player_value["bust"]),
        }

    def _settle(self, state: dict[str, Any]) -> dict[str, Any]:
        self._dealer_play(state)
        outcomes = [
            self._outcome_for(state, player_id)
            for player_id in state["participant_order"]
        ]
        state["outcomes_by_player"] = {
            outcome["player_id"]: deepcopy(outcome) for outcome in outcomes
        }
        counts = {
            key: sum(outcome["outcome"] == key for outcome in outcomes)
            for key in ("win", "loss", "push")
        }
        result_text = (
            f"21点结算：{counts['win']} 胜 · {counts['loss']} 负 · {counts['push']} 推和"
        )
        dealer_value = self._value_for(state, DEALER_ID)
        result = {
            # The common winner field cannot represent independent outcomes
            # against a non-participant dealer, so finish compatibly as a draw.
            "draw": True,
            "terminal_result": "blackjack_dealer_comparison",
            "result_text": result_text,
            "dealer": {
                "total": int(dealer_value["total"]),
                "soft": bool(dealer_value["soft"]),
                "natural_blackjack": bool(dealer_value["blackjack"]),
                "bust": bool(dealer_value["bust"]),
            },
            "outcomes": deepcopy(outcomes),
            "outcomes_by_player": deepcopy(state["outcomes_by_player"]),
        }
        state["game_result"] = deepcopy(result)
        state["result_text"] = result_text
        state["turn_player_id"] = None
        state["flow"]["phase"] = "finished"
        return result

    def apply_action(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
    ) -> MoveResult:
        player_id = str(actor["player_id"])
        state["turn_player_id"] = player_id
        action = move["action"]
        if action == "hit":
            self._draw_one(state, player_id)
            value = self._value_for(state, player_id)
            if value["bust"]:
                state["player_status_by_player"][player_id] = "bust"
                note = f"要牌后爆牌（{value['total']} 点）。"
            else:
                note = f"要牌，当前 {value['label']}。"
        else:
            current_status = state["player_status_by_player"][player_id]
            if current_status != "blackjack":
                state["player_status_by_player"][player_id] = "stood"
            value = self._value_for(state, player_id)
            note = (
                "自然 Blackjack，等待庄家结算。"
                if current_status == "blackjack"
                else f"停牌于 {value['label']}。"
            )

        advance_flow(state)
        state["action_history"].append({
            "sequence": len(state["action_history"]) + 1,
            "player_id": player_id,
            "action": action,
            "total_after": int(value["total"]),
            "status_after": state["player_status_by_player"][player_id],
        })

        if state["player_status_by_player"][player_id] == "playing":
            return MoveResult(state=state, retain_turn=True, note=note)

        next_player_id = self._next_playing_after(state, player_id)
        if next_player_id is not None:
            state["turn_player_id"] = next_player_id
            return MoveResult(
                state=state,
                next_player_id=next_player_id,
                note=note,
            )

        result = self._settle(state)
        return MoveResult(state=state, note=f"{note} {result['result_text']}", result=result)

    def progress_after_action(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
        participants: list[dict[str, Any]],
        applied: dict[str, Any] | MoveResult,
    ) -> dict[str, Any] | MoveResult:
        del state, move, actor, participants
        return applied

    def result_for(
        self,
        state: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        del participants
        if state.get("flow", {}).get("phase") != "finished":
            return None
        result = state.get("game_result")
        if not isinstance(result, dict):
            raise ValueError("终局缺少 Blackjack game_result")
        return deepcopy(result)

    def check_winner(self, state: dict[str, Any]) -> str | None:
        return "draw" if state.get("flow", {}).get("phase") == "finished" else None

    def format_action(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
    ) -> str:
        del state, actor
        return {"hit": "要牌", "stand": "停牌"}.get(move.get("action"), "21点行动")

    def public_state(
        self,
        state: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any]:
        del participants
        revealed = bool(state.get("dealer_hole_revealed"))
        dealer_hand = self._hand_for(state, DEALER_ID)
        if revealed:
            public_dealer_hand: list[dict[str, Any]] = [
                self._public_card(card) for card in dealer_hand
            ]
            dealer_value = self._value_for(state, DEALER_ID)
        else:
            public_dealer_hand = []
            if dealer_hand:
                public_dealer_hand.append(self._public_card(dealer_hand[0]))
            if len(dealer_hand) >= 2:
                # A uniform object: no value, ID, suit, rank, or count delta.
                public_dealer_hand.append({"hidden": True})
            dealer_value = hand_value(dealer_hand[:1]) if dealer_hand else None

        players: dict[str, Any] = {}
        for player_id in state["participant_order"]:
            hand = self._hand_for(state, player_id)
            value = self._value_for(state, player_id)
            outcome = state.get("outcomes_by_player", {}).get(player_id)
            players[player_id] = {
                "hand": [self._public_card(card) for card in hand],
                "value": deepcopy(value),
                "status": state["player_status_by_player"][player_id],
                "status_label": self._status_label(
                    state["player_status_by_player"][player_id]
                ),
                "outcome": deepcopy(outcome),
            }
        return {
            "board_kind": "blackjack",
            "shoe_decks": SHOE_DECKS,
            "flow": deepcopy(state["flow"]),
            "participant_order": list(state["participant_order"]),
            "turn_player_id": state.get("turn_player_id"),
            "players": players,
            "dealer": {
                "hand": public_dealer_hand,
                "value": deepcopy(dealer_value),
                "hole_hidden": not revealed,
                "status": state.get("dealer_status", "hidden"),
            },
            "action_history": deepcopy(state.get("action_history", [])),
            "game_result": deepcopy(state.get("game_result")),
            "result_text": str(state.get("result_text") or ""),
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
        hand = self._hand_for(state, player_id)
        return {
            "player_id": player_id,
            "hand": [self._public_card(card) for card in hand],
            "value": self._value_for(state, player_id),
            "status": state["player_status_by_player"][player_id],
            "legal_actions": self._legal_actions_for(state, player_id),
        }

    def participant_summary(
        self,
        state: dict[str, Any],
        participant: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, str | int | bool | None]:
        del participants
        player = state.get("players", {}).get(str(participant["player_id"]), {})
        value = player.get("value") or {}
        outcome = player.get("outcome") or {}
        return {
            "points": value.get("total"),
            "cards": len(player.get("hand", [])),
            "hand_status": player.get("status_label"),
            "outcome": outcome.get("outcome"),
        }

    def npc_compact_rules(
        self,
        state: dict[str, Any],
        actor: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> str:
        del state, actor, participants
        return (
            "从服务端 legal_actions 中选择 hit 或 stand。目标是不超过 21 点并击败庄家；"
            "A 自动按 1/11 最优计点。庄家 S17。没有 split/double/insurance/surrender。"
        )

    def npc_public_actions(
        self,
        state: dict[str, Any],
        actor: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        del actor, participants
        return deepcopy(state.get("action_history", []))

    def npc_legal_actions(
        self,
        state: dict[str, Any],
        actor: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        del participants
        player_id = str(actor["player_id"])
        actions = self._legal_actions_for(state, player_id)
        if not actions:
            return []
        value = self._value_for(state, player_id)
        preferred = "stand" if int(value["total"]) >= 17 else "hit"
        return sorted(actions, key=lambda action: action["action"] != preferred)
