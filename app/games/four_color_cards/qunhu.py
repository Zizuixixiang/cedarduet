"""Zhongxiao Qunhu with explicit fixed-listen, priority and flower conventions."""

from .common import FourColorGame
from .cards import code, label
from .qunhu_rules import (
    winning_partition,
    waiting_codes,
    exhibit,
    solver,
    quality_evaluator,
)
from .rules import QUNHU


class Qunhu(FourColorGame):
    game_type = "four_color_qunhu"
    display_name = "四色牌·群胡"
    rules_version = "kinmen-zhongxiao2021-duel1"
    rules_text = QUNHU
    min_players = 3
    max_players = 5
    recommended_players = 4
    other_count = 15

    def setup(self, s):
        s.update(
            listening={p: None for p in s["participant_order"]},
            flowers=[],
            exchanges_since_draw=0,
        )

    def _options(self, s, p):
        if s["result"] or s["turn_player_id"] != p:
            return []
        phase = s["flow"]["phase"]
        hand = s["hands"][p]
        if phase == "draw":
            return [self.option("draw", "翻牌")]
        if phase == "discard":
            result = []
            solve = solver()
            for card in sorted(hand):
                result.append(
                    self.option("discard:" + card, "打出" + label(card), card=card)
                )
                remaining = [t for t in hand if t != card]
                waits = waiting_codes(remaining, solve)
                if waits:
                    result.append(
                        self.option(
                            "listen:" + card,
                            "打出" + label(card) + "并听牌",
                            card=card,
                            waits=waits,
                            exhibit=exhibit(remaining),
                        )
                    )
            return result
        if phase != "response":
            return []
        q = s["pending"]
        tier = q["windows"][q["index"]][1]
        if tier == "hu":
            if (
                s["listening"][p]
                and code(q["card"]) in s["listening"][p]["waits"]
                and winning_partition(hand + [q["card"]])
            ):
                return [self.option("hu", "胡牌"), self.option("pass", "过")]
            return []
        # A listener may only win or leave the incoming card; no changing hand.
        if s["listening"][p]:
            return []
        return [self.option("take", "取上家弃牌"), self.option("pass", "不取，翻牌")]

    def offer(self, s, card, source, from_deck):
        order = self._order(s, source) if from_deck else [self._next(s, source)]
        windows = [(p, "hu") for p in order]
        if not from_deck:
            windows.append((self._next(s, source), "take"))
        s["pending"] = dict(
            card=card, source=source, from_deck=from_deck, windows=windows, index=0
        )
        s["flow"]["phase"] = "response"
        self.seek(s)

    def seek(self, s):
        q = s["pending"]
        while q["index"] < len(q["windows"]):
            s["turn_player_id"] = q["windows"][q["index"]][0]
            if self._options(s, s["turn_player_id"]):
                return
            q["index"] += 1
        s["pending"] = None
        if q["from_deck"]:
            p = q["source"]
            if s["listening"][p]:
                # A locked listener must discard an unclaimed draw unchanged.
                self.offer(s, q["card"], p, False)
            else:
                s["hands"][p].append(q["card"])
                s["turn_player_id"] = p
                s["flow"]["phase"] = "discard"
        else:
            s["discards"].append(q["card"])
            s["turn_player_id"] = self._next(s, q["source"])
            s["flow"]["phase"] = "draw"
            if not s["wall"]:
                self.finish(s)

    def step(self, s, p, o):
        kind = o["kind"]
        if kind == "draw":
            if not s["wall"]:
                self.finish(s)
            else:
                s["exchanges_since_draw"] = 0
                self.offer(s, s["wall"].pop(0), p, True)
        elif kind in ("discard", "listen"):
            s["hands"][p].remove(o["card"])
            s["exchanges_since_draw"] += 1
            if kind == "listen":
                s["listening"][p] = {"exhibit": o["exhibit"], "waits": o["waits"]}
            self.offer(s, o["card"], p, False)
            if not s["result"] and s["exchanges_since_draw"] >= 112:
                self.finish(s)
                s["result"]["reason"] = "exchange_limit"
        elif kind == "pass":
            s["pending"]["index"] += 1
            self.seek(s)
        elif kind == "take":
            s["hands"][p].append(s["pending"]["card"])
            s["pending"] = None
            s["turn_player_id"] = p
            s["flow"]["phase"] = "discard"
        elif kind == "hu":
            q = s["pending"]
            selfdraw = q["from_deck"] and q["source"] == p
            s["hands"][p].append(q["card"])
            s["pending"] = None
            partition = winning_partition(s["hands"][p])
            if not partition:
                raise ValueError("群胡必须五组三张加一单张且有目")
            if not selfdraw:
                s["flowers"] = s["wall"][:2]
                del s["wall"][:2]
            hit = any(code(t) in {code(c) for c in s["hands"][p]} for t in s["flowers"])
            points = 3 if selfdraw else 2 if hit else 1
            self.finish(
                s,
                p,
                points,
                scoring={
                    "per_opponent": points,
                    "flower_hit": hit,
                    "partition": partition,
                },
                flowers=list(s["flowers"]),
                winning_cards=list(s["hands"][p]),
                win_kind="self_draw" if selfdraw else "claimed",
            )

    def public_state(self, s, participants):
        out = super().public_state(s, participants)
        out["listening"] = {
            p: list(v["exhibit"]) if v else None for p, v in s["listening"].items()
        }
        return out

    def private_extra(self, s, p):
        return {
            "waiting_codes": (
                list(s["listening"][p]["waits"]) if s["listening"][p] else []
            )
        }

    def choose_local_npc_action(self, s, actor, participants):
        p = str(actor["player_id"])
        options = self._options(s, p)
        if not options:
            return None
        for kind in ("hu", "listen"):
            preferred = [o for o in options if o["kind"] == kind]
            if preferred:
                return self._action(
                    max(preferred, key=lambda o: len(o.get("waits", [])))["id"]
                )
        quality = quality_evaluator()
        hand = s["hands"][p]
        discards = [o for o in options if o["kind"] == "discard"]
        if discards:
            return self._action(
                max(
                    discards, key=lambda o: quality([t for t in hand if t != o["card"]])
                )["id"]
            )
        if any(o["kind"] == "take" for o in options):
            cards = hand + [s["pending"]["card"]]
            # Improve strictly; do not create an endless take/discard loop.
            best = max(quality([t for t in cards if t != c]) for c in cards)
            return self._action("take" if best > quality(hand) else "pass")
        return self._action(options[0]["id"])
