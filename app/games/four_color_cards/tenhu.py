"""Unregistered BGA ten-hu single-hand adapter for isolated integration tests.

Release gates and digital conventions: docs/FOUR_COLOR_CARDS.md. This is NOT a
fallback implementation for Jiuzhi or Qunhu. All state is JSON serializable.
"""

from collections import Counter
from copy import deepcopy
import random

from ..base import GamePlugin, MoveResult
from .cards import DECK, card_view, code, label
from .rules import TENHU
from .tenhu_rules import (
    RULES_VERSION, best_partition, claims, meld_info, response_windows, score_hand,
)


class BgaTenHu(GamePlugin):
    game_type = "four_color_tenhu"
    display_name = "四色牌·十胡"
    category = "card"
    min_players = 2
    max_players = 4
    recommended_players = 4
    supports_npcs = True
    uses_local_npc_strategy = True
    supports_stakes = False
    mcp_immediate_public_events = True
    mcp_event_key = "four_color_state"
    rules_text = TENHU
    move_format = '原样提交 private_state.legal_actions 中的 {"action":"act","action_id":"…"}。'

    def __init__(self, rng=None):
        self.rng = rng or random.SystemRandom()

    def initial_state(self):
        return {"board_kind": self.game_type, "rules_version": RULES_VERSION,
                "participant_order": [], "hands": {}, "melds": {}, "wall": [],
                "discards": [], "first_discard": None, "flower": None,
                "pending": None, "turn_player_id": None, "result": None,
                "flow": {"phase": "waiting", "round_number": 1, "turn_number": 0}}

    def tokens_for(self, participants):
        return [f"seat-{i}" for i, _ in enumerate(participants)]

    def initialize(self, participants):
        return self.initialize_for_first_player(participants, str(participants[0]["player_id"]))

    def initialize_for_first_player(self, participants, first_player_id):
        if not 1 <= len(participants) <= 4:
            raise ValueError("十胡支持2–4人；等待房可有1人")
        seated = sorted(participants, key=lambda p: p.get("seat_index", 0))
        order = [str(p["player_id"]) for p in seated]
        if len(set(order)) != len(order) or first_player_id not in order:
            raise ValueError("无效座位或庄家")
        offset = order.index(first_player_id)
        order = order[offset:] + order[:offset]
        state = self.initial_state()
        state.update(participant_order=order, hands={p: [] for p in order},
                     melds={p: [] for p in order}, turn_player_id=first_player_id,
                     dealer_player_id=first_player_id)
        if len(order) == 1:
            return state
        state["wall"] = list(DECK)
        self.rng.shuffle(state["wall"])
        # Exact 7 / 7 / (7 for dealer, 6 otherwise) packet deal.
        for packet in range(3):
            for pid in order:
                count = 7 if packet < 2 or pid == first_player_id else 6
                state["hands"][pid].extend(state["wall"][:count])
                del state["wall"][:count]
        state["flow"]["phase"] = "discard"
        return state

    def _next(self, state, pid):
        order = state["participant_order"]
        return order[(order.index(pid) + 1) % len(order)]

    @staticmethod
    def _action(action_id):
        return {"action": "act", "action_id": action_id}

    def _options(self, state, pid):
        """Only reads this player's hand plus public information.

        Window scheduling is server-side, but candidate generation and NPC
        choice cannot consult another hand, wall order or future flower.
        """
        if state["result"] or state["turn_player_id"] != pid:
            return []
        phase = state["flow"]["phase"]
        hand = state["hands"].get(pid, [])
        if phase == "draw":
            return [{"id": "draw", "kind": "draw", "label": "翻牌"}]
        if phase == "discard":
            return [{"id": f"discard:{t}", "kind": "discard", "card": t,
                     "label": f"打出{label(t)}"} for t in sorted(hand) if code(t)[1] != "0"]
        if phase != "response":
            return []
        pending = state["pending"]
        window_pid, tier = pending["windows"][pending["index"]]
        if window_pid != pid:
            return []
        result = []
        for candidate in claims(hand, pending["card"]):
            kind = candidate["meld"]["kind"]
            if tier == "hu":
                remaining = [t for t in hand if t not in candidate["held"]]
                partition = best_partition(remaining)
                if partition is None:
                    continue
                hu = partition["hu"] + candidate["meld"]["hu"] + sum(
                    meld_info(m["cards"], exposed=True)["hu"] for m in state["melds"][pid])
                action_kind, text = "hu", f"胡（组合{hu}胡）"
            elif tier == "pong" and kind in ("pong", "kong"):
                action_kind, text, hu = kind, "碰" if kind == "pong" else "杠", None
            elif tier == "chi" and kind not in ("pong", "kong"):
                action_kind, text, hu = "chi", "吃", None
            else:
                continue
            suffix = ",".join(candidate["held"]) or "general"
            result.append({"id": f"{action_kind}:{suffix}", "kind": action_kind,
                           "label": text, "held": candidate["held"],
                           "meld": candidate["meld"], "base_hu": hu})
        forced = (tier == "chi" and pending["from_deck"] and
                  pending["source"] == pid and code(pending["card"])[1] == "0")
        if not forced:
            result.append({"id": "pass", "kind": "pass", "label": "过"})
        return result

    def _seek_response(self, state):
        pending = state["pending"]
        while pending["index"] < len(pending["windows"]):
            state["turn_player_id"] = pending["windows"][pending["index"]][0]
            options = self._options(state, state["turn_player_id"])
            if any(o["kind"] != "pass" for o in options):
                return
            pending["index"] += 1
        state["discards"].append(pending["card"])
        next_player = self._next(state, pending["source"])
        state["pending"] = None
        state["turn_player_id"] = next_player
        state["flow"]["phase"] = "draw"
        if not state["wall"]:
            self._draw_game(state)

    def _offer(self, state, card, source, from_deck):
        state["pending"] = {"card": card, "source": source, "from_deck": from_deck,
                            "windows": [list(w) for w in response_windows(
                                state["participant_order"], source, from_deck)],
                            "index": 0}
        state["flow"]["phase"] = "response"
        self._seek_response(state)

    def _draw_game(self, state):
        state["result"] = {"draw": True, "reason": "wall_exhausted",
                           "point_deltas": {p: 0 for p in state["participant_order"]}}
        state["turn_player_id"] = None
        state["flow"]["phase"] = "finished"

    def _finish(self, state, pid):
        flower = state["wall"][0] if state["wall"] else state["first_discard"]
        scoring = score_hand(state["hands"][pid], [m["cards"] for m in state["melds"][pid]], flower)
        if state["wall"]:
            state["flower"] = state["wall"].pop(0)
        value = scoring["per_opponent"]
        deltas = {p: -value for p in state["participant_order"]}
        deltas[pid] = value * (len(deltas) - 1)
        winners = [pid] if scoring["qualified"] else [p for p in deltas if p != pid]
        state["result"] = {"draw": len(winners) != 1,
                           "winner_player_id": winners[0] if len(winners) == 1 else None,
                           "winning_player_ids": winners, "declarer_player_id": pid,
                           "reason": "hu" if scoring["qualified"] else "under_ten",
                           "point_deltas": deltas, "scoring": scoring,
                           "flower_card": flower, "flower_from_first_discard": state["flower"] is None}
        state["flow"]["phase"] = "finished"
        state["turn_player_id"] = None

    def validate_action(self, state, move, actor):
        if (not isinstance(move, dict) or set(move) != {"action", "action_id"}
                or move.get("action") != "act" or not isinstance(move.get("action_id"), str)):
            raise ValueError("请原样提交当前 legal_actions 的动作")
        pid = str(actor["player_id"])
        if not any(option["id"] == move["action_id"] for option in self._options(state, pid)):
            raise ValueError("当前玩家或阶段不允许此动作")

    def apply_action(self, state, move, actor):
        self.validate_action(state, move, actor)
        state = deepcopy(state)
        pid = str(actor["player_id"])
        option = next(o for o in self._options(state, pid) if o["id"] == move["action_id"])
        kind = option["kind"]
        completed = kind == "discard"
        if kind == "draw":
            if not state["wall"]:
                self._draw_game(state)
            else:
                self._offer(state, state["wall"].pop(0), pid, True)
            completed = state["flow"]["phase"] in ("draw", "finished")
        elif kind == "discard":
            card = option["card"]
            state["hands"][pid].remove(card)
            if state["first_discard"] is None:
                state["first_discard"] = card
            self._offer(state, card, pid, False)
        elif kind == "pass":
            was_draw = state["pending"]["from_deck"]
            state["pending"]["index"] += 1
            self._seek_response(state)
            completed = was_draw and state["flow"]["phase"] in ("draw", "finished")
        else:
            for card in option["held"]:
                state["hands"][pid].remove(card)
            group = deepcopy(option["meld"])
            group["source_player_id"] = state["pending"]["source"]
            state["melds"][pid].append(group)
            state["pending"] = None
            if kind == "hu" or all(code(t)[1] == "0" for t in state["hands"][pid]):
                self._finish(state, pid)
                completed = True
            else:
                state["flow"]["phase"] = "discard"
                state["turn_player_id"] = pid
        if completed:
            state["flow"]["turn_number"] += 1
        return MoveResult(state=state, next_player_id=state["turn_player_id"],
                          result=deepcopy(state["result"]), turn_completed=completed,
                          note=option["label"],
                          public_event={"four_color_state": self.public_state(state, [])})

    def public_state(self, state, participants):
        pending = state["pending"]
        return {"board_kind": self.game_type, "rules_version": RULES_VERSION,
                "flow": deepcopy(state["flow"]), "participant_order": list(state["participant_order"]),
                "turn_player_id": state["turn_player_id"],
                "dealer_player_id": state.get("dealer_player_id"),
                "hand_counts": {p: len(h) for p, h in state["hands"].items()},
                "melds": deepcopy(state["melds"]), "wall_count": len(state["wall"]),
                "discards": list(state["discards"]),
                "offer": {k: pending[k] for k in ("card", "source", "from_deck")} if pending else None,
                "result": deepcopy(state["result"])}

    def private_state(self, state, viewer, participants):
        pid = str(viewer["player_id"])
        if pid not in state["hands"]:
            return {}
        options = self._options(state, pid)
        return {"hand": [card_view(t) for t in sorted(state["hands"][pid])],
                "legal_actions": [self._action(o["id"]) for o in options],
                "action_labels": {o["id"]: o["label"] for o in options}}

    def npc_legal_actions(self, state, actor, participants):
        return self.private_state(state, actor, participants).get("legal_actions", [])

    def choose_local_npc_action(self, state, actor, participants):
        pid = str(actor["player_id"])
        options = self._options(state, pid)
        if not options:
            return None
        wins = [o for o in options if o["kind"] == "hu" and o["base_hu"] >= 10]
        if wins:
            chosen = max(wins, key=lambda o: o["base_hu"])
        else:
            claims_available = [o for o in options if o["kind"] in ("chi", "pong", "kong")]
            discards = [o for o in options if o["kind"] == "discard"]
            if claims_available:
                chosen = max(claims_available, key=lambda o: (len(o["held"]), o["meld"]["hu"]))
            elif discards:
                # Prefer isolated cards; only own-hand frequencies are consulted.
                counts = Counter(code(t) for t in state["hands"][pid])
                chosen = min(discards, key=lambda o: (counts[code(o["card"])], o["card"]))
            else:
                chosen = next((o for o in options if o["kind"] == "pass"), options[0])
        return self._action(chosen["id"])

    def project_event(self, event, viewer, participants):
        projected = deepcopy(event)
        # Action IDs can contain selected physical cards. Only server-authored
        # public consequences are sufficient for opponent event consumers.
        for key in ("action_id",):
            projected.pop(key, None)
        if isinstance(projected.get("move"), dict):
            projected["move"] = {k: v for k, v in projected["move"].items()
                                 if k in ("action", "four_color_state")}
        payload = projected.get("payload")
        if isinstance(payload, dict):
            for key in ("action_id",):
                payload.pop(key, None)
            if isinstance(payload.get("move"), dict):
                payload["move"] = {k: v for k, v in payload["move"].items()
                                   if k in ("action", "four_color_state")}
        return projected

    def result_for(self, state, participants):
        return deepcopy(state["result"])

    def mcp_event(self, event, previous):
        """Legacy explicit event reader; normal MCP uses atomic v2 responses."""
        from ..mcp_incremental import compact_event
        from .mcp import delta
        event = compact_event(event)
        move = event.get("move") or {}
        if "four_color_state" in move:
            event["move"] = {"four_color_patch": delta(move["four_color_state"], previous.get("four_color_state", {}))}
        return event

    def apply_resignation(self, state, resigned_player_id, participants):
        # Digital single-hand cancellation policy, not a traditional scoring
        # rule. Never strand a response on an inactive seat or redistribute
        # its hidden cards. A partial hand has no ordinary hu points.
        if state["result"] is None:
            state["result"] = {"draw": True, "reason": "hand_cancelled_by_departure",
                               "departed_player_id": resigned_player_id,
                               "point_deltas": {p: 0 for p in state["participant_order"]}}
            state["turn_player_id"] = None
            state["flow"]["phase"] = "finished"

    def check_winner(self, state):
        result = state["result"]
        return ("draw" if result["draw"] else result["winner_player_id"]) if result else None

    def format_action(self, state, move, actor):
        # Never put the private hu evaluation/physical held-card list in logs.
        return "四色牌行动"

    def validate_move(self, state, move, mark):
        raise ValueError("请使用服务端玩家身份")

    def apply_move(self, state, move, mark):
        raise ValueError("请使用服务端玩家身份")
