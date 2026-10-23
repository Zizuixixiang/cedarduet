"""Transport, physical dealing and lifecycle only; no shared variant rules."""

from copy import deepcopy
import random
from ..base import GamePlugin, MoveResult
from .cards import DECK, card_view

GAME_TYPES = frozenset(("four_color_tenhu", "four_color_jiuzhi", "four_color_qunhu"))


def record_discard(state, pending):
    """Record only a settled public card. None means an unclaimed deck reveal.

    Missing entries belong to old saves and have unknown provenance; never
    reconstruct them from the current actor or from private hands.
    """
    state["discards"].append(pending["card"])
    state.setdefault("discard_sources", {})[pending["card"]] = (
        None if pending["from_deck"] else pending["source"]
    )


def public_discard_sources(state):
    known = state.get("discard_sources", {})
    return {card: known[card] for card in state["discards"]
            if card in known and (known[card] is None or known[card] in state["participant_order"])}


class FourColorGame(GamePlugin):
    category = "card"
    supports_npcs = True
    uses_local_npc_strategy = True
    supports_stakes = False
    mcp_immediate_public_events = True
    mcp_event_key = "four_color_state"
    move_format = '提交当前 legal_actions 中的 {"action":"act","action_id":"…"}，外层必须带 revision。'

    def __init__(self, rng=None):
        self.rng = rng or random.SystemRandom()

    def tokens_for(self, participants):
        return [f"seat-{i}" for i, _ in enumerate(participants)]

    def initial_state(self):
        return dict(
            board_kind=self.game_type,
            rules_version=self.rules_version,
            participant_order=[],
            hands={},
            melds={},
            wall=[],
            discards=[],
            pending=None,
            turn_player_id=None,
            dealer_player_id=None,
            result=None,
            flow=dict(phase="waiting", round_number=1, turn_number=0),
        )

    def initialize(self, participants):
        return self.initialize_for_first_player(
            participants, str(participants[0]["player_id"])
        )

    def initialize_for_first_player(self, participants, first_player_id):
        if (
            len(participants) != 1
            and len(participants) not in self.resolved_allowed_player_counts()
        ):
            raise ValueError("该规则不支持此人数")
        order = [
            str(p["player_id"])
            for p in sorted(participants, key=lambda p: p.get("seat_index", 0))
        ]
        if len(set(order)) != len(order) or first_player_id not in order:
            raise ValueError("无效座位")
        i = order.index(first_player_id)
        order = order[i:] + order[:i]
        s = self.initial_state()
        s.update(
            participant_order=order,
            hands={p: [] for p in order},
            melds={p: [] for p in order},
            turn_player_id=first_player_id,
            dealer_player_id=first_player_id,
        )
        self.setup(s)
        if len(order) == 1:
            return s
        s["wall"] = list(DECK)
        self.rng.shuffle(s["wall"])
        self.deal(s)
        s["flow"]["phase"] = "discard"
        self.after_deal(s)
        return s

    def setup(self, s):
        pass

    def after_deal(self, s):
        pass

    def deal(self, s):
        for _ in range(self.other_count):
            for p in s["participant_order"]:
                s["hands"][p].append(s["wall"].pop(0))
        s["hands"][s["dealer_player_id"]].append(s["wall"].pop(0))

    def _next(self, s, p):
        order = s["participant_order"]
        return order[(order.index(p) + 1) % len(order)]

    def _order(self, s, p):
        order = s["participant_order"]
        i = order.index(p)
        return order[i:] + order[:i]

    @staticmethod
    def _action(id):
        return {"action": "act", "action_id": id}

    @staticmethod
    def option(id, label, **kw):
        return dict(id=id, label=label, kind=id.split(":")[0], **kw)

    def validate_action(self, s, move, actor):
        self.selected(s, move, actor)

    def selected(self, s, move, actor):
        if (
            not isinstance(move, dict)
            or set(move) != {"action", "action_id"}
            or move.get("action") != "act"
        ):
            raise ValueError("请原样提交当前合法动作；规则不能在房间中变更")
        for o in self._options(s, str(actor["player_id"])):
            if o["id"] == move.get("action_id"):
                return o
        raise ValueError("当前玩家或阶段不允许此动作")

    def apply_action(self, state, move, actor):
        o = self.selected(state, move, actor)
        s = deepcopy(state)
        self.step(s, str(actor["player_id"]), o)
        s["flow"]["turn_number"] += 1
        return MoveResult(
            state=s,
            next_player_id=s["turn_player_id"],
            result=deepcopy(s["result"]),
            turn_completed=True,
            note=o["label"],
            public_event={"four_color_state": self.public_state(s, [])},
        )

    def finish(self, s, pid=None, points=0, payers=None, **details):
        deltas = {p: 0 for p in s["participant_order"]}
        if pid:
            for p in (payers if payers is not None else s["participant_order"]):
                if p != pid:
                    deltas[p] -= points
                    deltas[pid] += points
        s["result"] = dict(
            draw=pid is None,
            winner_player_id=pid,
            declarer_player_id=pid,
            reason="hu" if pid else "wall_exhausted",
            point_deltas=deltas,
            **details,
        )
        s["turn_player_id"] = None
        s["flow"]["phase"] = "finished"

    def public_state(self, s, participants):
        return dict(
            board_kind=self.game_type,
            rules_version=self.rules_version,
            flow=deepcopy(s["flow"]),
            participant_order=list(s["participant_order"]),
            turn_player_id=s["turn_player_id"],
            dealer_player_id=s["dealer_player_id"],
            hand_counts={p: len(h) for p, h in s["hands"].items()},
            melds=deepcopy(s["melds"]),
            wall_count=len(s["wall"]),
            discards=list(s["discards"]),
            discard_sources=public_discard_sources(s),
            offer=(
                {k: s["pending"][k] for k in ("card", "source", "from_deck")}
                if s["pending"]
                else None
            ),
            result=deepcopy(s["result"]),
        )

    def private_extra(self, s, p):
        return {}

    def private_state(self, s, viewer, participants):
        p = str(viewer["player_id"])
        options = self._options(s, p)
        return dict(
            hand=[card_view(t) for t in sorted(s["hands"][p])],
            legal_actions=[self._action(o["id"]) for o in options],
            action_labels={o["id"]: o["label"] for o in options},
            **self.private_extra(s, p),
        )

    def npc_legal_actions(self, s, actor, participants):
        return [
            self._action(o["id"]) for o in self._options(s, str(actor["player_id"]))
        ]

    def result_for(self, s, participants):
        return deepcopy(s["result"])

    def apply_resignation(self, s, resigned_player_id, participants):
        if s["result"] is None:
            self.finish(s)
            s["result"].update(
                reason="hand_cancelled_by_departure",
                departed_player_id=resigned_player_id,
            )

    def check_winner(self, s):
        return (
            ("draw" if s["result"]["draw"] else s["result"]["winner_player_id"])
            if s["result"]
            else None
        )

    def format_action(self, s, move, actor):
        return "四色牌行动"

    def project_event(self, event, viewer, participants):
        out = deepcopy(event)
        for obj in (out, out.get("payload")):
            if isinstance(obj, dict):
                obj.pop("action_id", None)
                if isinstance(obj.get("move"), dict):
                    obj["move"] = {
                        k: v
                        for k, v in obj["move"].items()
                        if k in ("action", "four_color_state")
                    }
        return out

    def mcp_event(self, event, previous):
        from ..mcp_incremental import compact_event
        from .mcp import delta

        out = compact_event(event)
        move = out.get("move") or {}
        if "four_color_state" in move:
            out["move"] = {
                "four_color_patch": delta(
                    move["four_color_state"], previous.get("four_color_state", {})
                )
            }
        return out

    def validate_move(self, *args):
        raise ValueError("请使用服务端玩家身份")

    def apply_move(self, *args):
        raise ValueError("请使用服务端玩家身份")
