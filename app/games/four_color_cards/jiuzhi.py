"""Five-pair Jiuzhi: published public-draw variant + explicit Duel conventions."""

from collections import Counter
from copy import deepcopy
from .common import FourColorGame, record_discard
from .cards import code, label
from .jiuzhi_rules import winning_pairs
from .rules import JIUZHI


def score(cards, incoming, self_draw, reserve):
    if winning_pairs(cards) is None:
        raise ValueError("九支须同色同字五对十张")
    counts = Counter(map(code, cards))
    ranks = {c[1] for c in counts}
    heads = {"颜色": 5 - len({c[0] for c in counts})}
    if "6" not in ranks:
        heads["无兵卒"] = 1
    if "0" not in ranks:
        heads["无将帅"] = 1
    if self_draw:
        heads["自摸"] = 1
        heads["抽五张"] = sum(
            (2 if i == 4 else 1) for i, t in enumerate(reserve) if code(t) in counts
        )
    kaijiao = (
        len(ranks) == 1
        and sorted(counts.values()) == [2, 4, 4]
        and incoming is not None
        and counts[code(incoming)] == 4
    )
    if kaijiao:
        heads["开交胡"] = 5
    elif any(n == 4 for n in counts.values()):
        heads["开交对子"] = sum(n == 4 for n in counts.values())
    return {"heads": heads, "total": sum(heads.values()), "pairs": winning_pairs(cards)}


class Jiuzhi(FourColorGame):
    game_type = "four_color_jiuzhi"
    display_name = "四色牌·九支十仔"
    rules_version = "taiwan-five-pairs-sekai2015-duel1"
    rules_text = JIUZHI
    min_players = max_players = recommended_players = 4
    other_count = 9

    def setup(self, s):
        s.update(
            closed_pairs={p: [] for p in s["participant_order"]},
            broken={p: [] for p in s["participant_order"]},
            reserve=[],
        )

    def deal(self, s):
        s["reserve"] = s["wall"][:5]
        del s["wall"][:5]
        super().deal(s)

    def after_deal(self, s):
        for p in s["participant_order"]:
            for c in sorted(set(map(code, s["hands"][p]))):
                same = sorted(t for t in s["hands"][p] if code(t) == c)
                for i in range(0, len(same) - 1, 2):
                    pair = same[i : i + 2]
                    s["closed_pairs"][p].append(pair)
                    for t in pair:
                        s["hands"][p].remove(t)
        if not s["hands"][s["dealer_player_id"]]:
            s["flow"]["phase"] = "opening_hu"

    def owned(self, s, p):
        return (
            s["hands"][p]
            + sum(s["closed_pairs"][p], [])
            + [t for g in s["melds"][p] for t in g["cards"]]
        )

    def _options(self, s, p):
        if s["result"] or s["turn_player_id"] != p:
            return []
        phase = s["flow"]["phase"]
        hand = s["hands"][p]
        if phase == "opening_hu":
            return [self.option("hu", "五对胡牌")]
        if phase == "draw":
            return [self.option("draw", "翻牌")]
        if phase == "discard":
            broken = [t for t in s["broken"][p] if t in hand]
            counts = Counter(t[0] for t in hand)
            choices = (
                broken[:1]
                if broken
                else [t for t in sorted(hand) if counts[t[0]] == min(counts.values())]
            )
            return [
                self.option("discard:" + t, "打出" + label(t), card=t) for t in choices
            ]
        if phase != "response":
            return []
        q = s["pending"]
        tier = q["windows"][q["index"]][1]
        eligible = sorted(
            t for t in hand if code(t) == code(q["card"]) and t not in s["broken"][p]
        )
        win = eligible and winning_pairs(self.owned(s, p) + [q["card"]]) is not None
        if tier == "hu" and win:
            return [self.option("hu", "五对胡牌", held=eligible[0])]
        if tier == "pair" and eligible:
            return [self.option("pair:" + eligible[0], "吃对", held=eligible[0])]
        return []

    def offer(self, s, card, source, from_deck):
        order = self._order(s, source) if from_deck else [self._next(s, source)]
        s["pending"] = dict(
            card=card,
            source=source,
            from_deck=from_deck,
            windows=[(p, tier) for tier in ("hu", "pair") for p in order],
            index=0,
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
        record_discard(s, q)
        s["turn_player_id"] = self._next(s, q["source"])
        s["pending"] = None
        s["flow"]["phase"] = "draw"
        if not s["wall"]:
            self.finish(s)

    def win(self, s, p, q=None):
        selfdraw = bool(q and q["from_deck"] and q["source"] == p)
        scoring = score(
            self.owned(s, p), q["card"] if q else None, selfdraw, s["reserve"]
        )
        payers = [q["source"]] if q and not q["from_deck"] else None
        self.finish(
            s,
            p,
            scoring["total"],
            payers,
            scoring=scoring,
            winning_cards=self.owned(s, p),
            flowers=list(s["reserve"]) if selfdraw else [],
            win_kind=(
                "self_draw" if selfdraw else ("discard" if payers else "public_draw")
            ),
        )

    def step(self, s, p, o):
        kind = o["kind"]
        if kind == "hu" and s["flow"]["phase"] == "opening_hu":
            self.win(s, p)
            return
        if kind == "draw":
            if not s["wall"]:
                self.finish(s)
            else:
                self.offer(s, s["wall"].pop(0), p, True)
        elif kind == "discard":
            s["hands"][p].remove(o["card"])
            if o["card"] in s["broken"][p]:
                s["broken"][p].remove(o["card"])
            self.offer(s, o["card"], p, False)
        else:
            q = deepcopy(s["pending"])
            s["hands"][p].remove(o["held"])
            s["melds"][p].append({"kind": "pair", "cards": [o["held"], q["card"]]})
            s["pending"] = None
            downstream = self._next(s, p)
            for t in sorted(s["hands"][downstream]):
                if code(t) == code(q["card"]) and t not in s["broken"][downstream]:
                    s["broken"][downstream].append(t)
            if kind == "hu":
                self.win(s, p, q)
            else:
                s["flow"]["phase"] = "discard"
                s["turn_player_id"] = p

    def public_state(self, s, participants):
        out = super().public_state(s, participants)
        out.update(
            closed_pair_counts={p: len(v) for p, v in s["closed_pairs"].items()},
            broken=deepcopy(s["broken"]),
            reserve_count=len(s["reserve"]),
        )
        return out

    def private_extra(self, s, p):
        return {"closed_pairs": deepcopy(s["closed_pairs"][p])}

    def choose_local_npc_action(self, s, actor, participants):
        options = self._options(s, str(actor["player_id"]))
        return self._action(options[0]["id"]) if options else None
