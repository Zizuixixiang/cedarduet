"""Original Classic Rummikub implementation; see docs/RUMMIKUB.md for sources.

Actions describe a complete table. Validation is pure; an invalid draft never
changes the authoritative state. Tile IDs denote physical pieces, not faces.
"""
from __future__ import annotations

from copy import deepcopy
from itertools import combinations, product
import random

from .base import GamePlugin, MoveResult

COLORS = ("red", "blue", "black", "orange")
COLOR_LABELS = dict(zip(COLORS, ("红", "蓝", "黑", "橙")))


def build_tiles() -> list[dict]:
    return [
        {"id": f"{color}-{number}-{copy}", "color": color, "number": number}
        for color in COLORS for number in range(1, 14) for copy in (1, 2)
    ] + [{"id": f"joker-{copy}", "color": "joker", "number": 0} for copy in (1, 2)]


TILES = {tile["id"]: tile for tile in build_tiles()}


def meld_info(ids: list[str], kind: str | None = None) -> dict:
    """Ordered runs; unordered groups. Joker roles are inferred, never trusted.

    A three-tile group deliberately keeps BOTH missing colors possible. In a
    run, the position fixes the joker's number, including either end of a run.
    """
    if not isinstance(ids, list) or not 3 <= len(ids) <= 13:
        raise ValueError("每组须有 3–13 张牌")
    if any(not isinstance(i, str) or i not in TILES for i in ids):
        raise ValueError("未知实体牌 ID")
    if len(set(ids)) != len(ids):
        raise ValueError("同一实体牌不能重复")
    if kind not in (None, "group", "run"):
        raise ValueError("组合类型只能是 group 或 run")
    candidates = []
    real = [(i, TILES[t]) for i, t in enumerate(ids) if TILES[t]["color"] != "joker"]
    if not real:
        raise ValueError("组合不能全是万能牌")
    numbers = {t["number"] for _, t in real}
    colors = {t["color"] for _, t in real}
    if len(ids) <= 4 and len(numbers) == 1 and len(colors) == len(real):
        number = real[0][1]["number"]
        candidates.append({"kind": "group", "number": number, "points": number * len(ids),
                "joker_roles": {t: {"number": number, "colors": [c for c in COLORS if c not in colors]}
                                for t in ids if TILES[t]["color"] == "joker"}})
    start = real[0][1]["number"] - real[0][0]
    if (len(colors) == 1 and 1 <= start and start + len(ids) - 1 <= 13
            and all(t["number"] == start + i for i, t in real)):
        color = real[0][1]["color"]
        candidates.append({"kind": "run", "color": color, "start": start,
                "points": sum(range(start, start + len(ids))),
                "joker_roles": {t: {"number": start + i, "colors": [color]}
                                for i, t in enumerate(ids) if TILES[t]["color"] == "joker"}})
    choices = [c for c in candidates if kind is None or c["kind"] == kind]
    if choices:
        return max(choices, key=lambda c: c["points"])
    raise ValueError("组合须为同数异色 3–4 张，或同色连续至少 3 张（按数字顺序，13 不接 1）")


def joker_assignments(melds, kinds):
    """Enumerate the at-most-two jokers' compatible concrete roles.

    Missing colors in a three-tile group stay ambiguous. Within one group,
    however, two jokers must represent DISTINCT colors, including across a
    split into different groups. This joint check prevents independent color
    intersections from accidentally retaining two formerly different roles.
    """
    roles, group_jokers = {}, []
    for ids, kind in zip(melds, kinds):
        info = meld_info(ids, kind)
        if info["kind"] == "group":
            group_jokers.append(list(info["joker_roles"]))
        for joker, role in info["joker_roles"].items():
            roles[joker] = [(info["kind"], role["number"], c) for c in role["colors"]]
    ids = sorted(roles)
    for values in product(*(roles[j] for j in ids)):
        assignment = dict(zip(ids, values))
        if all(len({assignment[j][2] for j in group}) == len(group) for group in group_jokers):
            yield assignment


class Rummikub(GamePlugin):
    game_type = "rummikub"
    display_name = "拉密"
    category = "tabletop"
    max_players = 4
    allowed_player_counts = (2, 3, 4)
    recommended_players = 4
    supports_npcs = True
    uses_local_npc_strategy = True
    # Tile scores are NOT platform wallet deltas. This release is stake=0 only.
    supports_stakes = False
    mcp_immediate_public_events = True
    mcp_event_key = 'rummikub_delta'
    rules_text = (
        "【目标与准备】\n"
        "- 2–4 人，推荐 4 人。四种颜色各有两套 1–13，加两张万能牌，共 106 张；每人 14 张。先出完手牌获胜。\n"
        "- 先手沿用房间选项；每个房间进行一局。\n\n"
        "【组合与开局】\n"
        "- 组：同一个数字、不同颜色的 3–4 张。顺子：同色连续至少 3 张，1 只能作最小牌，不能接在 13 后。\n"
        "- 首次出牌只能使用自己手牌，组成合计至少 30 分的合法组合；万能牌按它代表的数字计分。这一回合不能借用或改动桌面。\n\n"
        "【行动与重组】\n"
        "- 开局后的回合可拆分、合并、重排桌面，并至少加入一张本回合原手牌。原桌面牌必须全部留在桌上，不可重复或拿回手中。\n"
        "- 万能牌可用手牌、桌面牌或拆分重组来释放；释放后必须在本回合重新组成含至少一张原手牌的新组合，不能收回或留待下轮。未开局不能释放桌面万能牌。\n"
        "- 不出牌时摸一张并结束回合，刚摸的牌下回合才能使用。草稿可撤销、重置，整回合一次提交；无效草稿不改桌面、不罚牌。无固定 60 秒倒计时，超时接管按房间设置。\n\n"
        "【耗尽、胜负与离场】\n"
        "- 牌堆空了仍继续出牌。无法继续时点“无牌可出”声明；全部在局玩家连续一圈作此声明才结算，任何出牌或离场都会清除声明。声明由玩家判断，不是自动穷举判定。\n"
        "- 正常出完：其余玩家剩余数字牌按面值、万能牌按 30 计负分，赢家得对应总正分。堵局：剩余牌值最低者胜，其余人扣与最低值的差，赢家得差额总和。最低值并列时并列获胜，正分均分，可有小数。\n"
        "- 离场按弃权，手牌封存且不回牌堆；至少两人仍在局则继续，只剩一人时该人获胜，只剩 NPC 时按房间约定结束。弃权终局不计牌面局分。\n"
        "- 本版仅娱乐局；牌面局分不扣平台钱包。"
    )
    move_format = (
        '所有动作必须带外层 revision。出牌：{"move":{"action":"meld","melds":'
        '[["red-10-1","red-11-1","red-12-1"]]}}（首次 33 分示例，仅在这些 ID 确在己方手牌时成立）。'
        'melds 是最终整桌的二维实体 ID 数组，含全部旧桌面牌；顺子按小到大，万能牌 ID 放在代表数字的位置。'
        '可选 kinds 与 melds 逐组对应，值为 group/run；省略优先较高分解释；旧桌面照 meld_info.kind 保留。'
        '摸牌：{"move":{"action":"draw"}}；空堆声明无法继续：{"move":{"action":"pass"}}。'
        'private_state.hand 为己方牌，board_state.melds 为桌面，opened 为各席首次出牌状态。'
        'private_state.legal_action_spec 定义参数，suggested_move 是有限搜索建议，不是全部合法动作；'
        '可自行重组提交。不需要看图，不得使用他人手牌或猜测牌堆。'
    )

    def __init__(self, rng=None):
        self.rng = rng or random.SystemRandom()

    def initial_state(self):
        return {"board_kind": self.game_type, "flow": {"phase": "playing", "turn_number": 0},
                "participant_order": [], "active_player_ids": [], "turn_player_id": None,
                "hands": {}, "pool": [], "melds": [], "meld_kinds": [], "opened": {}, "blocked_player_ids": [],
                "resigned_player_ids": [], "result": None, "last_action": None}

    def tokens_for(self, participants):
        return [f"P{i + 1}" for i in range(len(participants))]

    def initialize(self, participants):
        order = [str(p["player_id"]) for p in sorted(participants, key=lambda p: p.get("seat_index", 0))]
        if not 1 <= len(order) <= 4 or len(set(order)) != len(order):
            raise ValueError("拉密需要 2–4 位不同玩家")
        state = self.initial_state()
        if len(order) == 1:
            state.update(participant_order=order, active_player_ids=list(order),
                         hands={order[0]: []}, opened={order[0]: False})
            state["flow"]["phase"] = "waiting"
            return state
        pool = list(TILES)
        self.rng.shuffle(pool)
        hands = {p: [] for p in order}
        for _ in range(14):
            for p in order:
                hands[p].append(pool.pop())
        state.update(participant_order=order, active_player_ids=list(order), pool=pool,
                     hands=hands, opened={p: False for p in order}, turn_player_id=order[0])
        return state

    def prepare_opening_state(self, state, first_player_id, participants):
        state["turn_player_id"] = first_player_id
        return state

    def validate_move(self, state, move, mark):
        raise ValueError("拉密使用带玩家身份的 action 接口")

    def apply_move(self, state, move, mark):
        raise ValueError("拉密使用带玩家身份的 action 接口")

    def check_winner(self, state):
        return "draw" if (state.get("result") or {}).get("draw") else None

    def validate_action(self, state, move, actor):
        pid = str(actor["player_id"])
        if (state.get("result") or state.get("flow", {}).get("phase") != "playing"
                or pid not in state["active_player_ids"] or state["turn_player_id"] != pid):
            raise ValueError("当前不是该玩家的行动回合")
        if not isinstance(move, dict):
            raise ValueError("动作必须是对象")
        action = move.get("action")
        if action in ("draw", "pass"):
            if set(move) != {"action"}:
                raise ValueError("摸牌或无法继续声明不接受其他参数")
            if action == "draw" and not state["pool"]:
                raise ValueError("牌堆已空；可继续出牌，无法继续时提交 pass")
            if action == "pass" and state["pool"]:
                raise ValueError("牌堆未空，不出牌应摸一张")
            return
        if action != "meld" or not {"action", "melds"} <= set(move) or set(move) - {"action", "melds", "kinds"}:
            raise ValueError("动作仅支持 meld、draw、pass；meld 必须提交完整 melds")
        melds = move["melds"]
        if not isinstance(melds, list) or not 1 <= len(melds) <= 35:
            raise ValueError("melds 必须为 1–35 个完整组合")
        kinds = move.get("kinds", [None] * len(melds))
        if (not isinstance(kinds, list) or len(kinds) != len(melds)
                or ("kinds" in move and any(k not in ("group", "run") for k in kinds))):
            raise ValueError("kinds 须逐组对应，值为 group 或 run")
        infos = [meld_info(m, k) for m, k in zip(melds, kinds)]
        flat = [t for m in melds for t in m]
        if len(flat) != len(set(flat)):
            raise ValueError("同一实体牌不能在整桌中重复")
        before = {t for m in state["melds"] for t in m}
        after = set(flat)
        hand = set(state["hands"][pid])
        if not before <= after:
            raise ValueError("不能丢失桌面牌或把桌面牌拿回手中")
        if not after <= before | hand:
            raise ValueError("只能使用原桌面和自己的原手牌")
        added = after - before
        if not added:
            raise ValueError("正常出牌至少加入一张本回合原手牌")
        if not state["opened"][pid]:
            def unchanged_key(m, kind):
                info = meld_info(m, kind)
                return info["kind"], tuple(sorted(m)) if info["kind"] == "group" else tuple(m)
            old_sets = {unchanged_key(m, self._kind(state, i)) for i, m in enumerate(state["melds"])}
            untouched = {unchanged_key(m, k) for m, k in zip(melds, kinds) if not set(m) & hand}
            if old_sets != untouched or any(set(m) & before and set(m) & hand for m in melds):
                raise ValueError("首次开局只能用自己的牌，不能拆用或添加桌面组合")
            points = sum(info["points"] for m, info in zip(melds, infos) if set(m) <= hand)
            if points < 30:
                raise ValueError(f"首次出牌须至少 30 分，当前 {points} 分")
        else:
            # Jokers in new combinations with original rack tiles can be
            # retrieved. Every other old joker MUST have a jointly consistent
            # retained role and at least one original real companion.
            required = []
            for old in state["melds"]:
                for joker in (t for t in old if TILES[t]["color"] == "joker"):
                    new = next(m for m in melds if joker in m)
                    if set(new) & hand:
                        continue
                    companions = {t for t in old if TILES[t]["color"] != "joker"}
                    if not companions.intersection(new):
                        raise ValueError("释放的万能牌须在同回合用于含至少一张原手牌的新组合")
                    required.append(joker)
            if required:
                old_kinds = [self._kind(state, i) for i in range(len(state["melds"]))]
                before = list(joker_assignments(state["melds"], old_kinds))
                after = list(joker_assignments(melds, kinds))
                if not any(all(a[j] == b[j] for j in required) for a in before for b in after):
                    raise ValueError("释放的万能牌须在同回合用于含至少一张原手牌的新组合")

    @staticmethod
    def _kind(state, index):
        kinds = state.get("meld_kinds", [])
        return kinds[index] if index < len(kinds) else None

    @staticmethod
    def _next(state, pid):
        order = state["participant_order"]
        start = order.index(pid)
        return next(order[(start + n) % len(order)] for n in range(1, len(order) + 1)
                    if order[(start + n) % len(order)] in state["active_player_ids"])

    def _finish(self, state, reason, winners=None):
        active = state["active_player_ids"]
        remaining = {p: sum(TILES[t]["number"] or 30 for t in state["hands"][p]) for p in active}
        if winners is None:
            lowest = min(remaining.values())
            winners = [p for p in active if remaining[p] == lowest]
        scores = {}
        if not state["resigned_player_ids"] and reason in ("empty_rack", "blocked"):
            base = 0 if reason == "empty_rack" else min(remaining.values())
            scores = {p: -(v - base) for p, v in remaining.items() if p not in winners}
            reward = -sum(scores.values()) / len(winners)
            scores.update({p: int(reward) if reward.is_integer() else reward for p in winners})
        state["result"] = {"reason": reason, "draw": len(winners) != 1,
                           "winner_player_id": winners[0] if len(winners) == 1 else None,
                           "winning_player_ids": winners, "remaining_values": remaining,
                           "tile_scores": scores}
        state["flow"]["phase"] = "finished"
        state["turn_player_id"] = None
        return state["result"]

    def apply_action(self, state, move, actor):
        self.validate_action(state, move, actor)
        # Isolate callers too, not just SQLite rollback.
        state = deepcopy(state)
        pid, action = str(actor["player_id"]), move["action"]
        played = 0
        if action == "meld":
            used = {t for m in move["melds"] for t in m}
            hand = state["hands"][pid]
            played = sum(t in used for t in hand)
            state["hands"][pid] = [t for t in hand if t not in used]
            state["melds"] = deepcopy(move["melds"])
            kinds = move.get("kinds", [None] * len(move["melds"]))
            state["meld_kinds"] = [meld_info(m, k)["kind"] for m, k in zip(move["melds"], kinds)]
            state["opened"][pid] = True
            state["blocked_player_ids"] = []
            if not state["hands"][pid]:
                self._finish(state, "empty_rack", [pid])
            note = f"打出 {played} 张，整桌组合已确认。"
        elif action == "draw":
            state["hands"][pid].append(state["pool"].pop())
            state["blocked_player_ids"] = []
            note = "摸一张，回合结束。"
        else:
            if pid not in state["blocked_player_ids"]:
                state["blocked_player_ids"].append(pid)
            if set(state["active_player_ids"]) <= set(state["blocked_player_ids"]):
                self._finish(state, "blocked")
            note = "牌堆已空，声明无牌可出。"
        state["flow"]["turn_number"] += 1
        state["last_action"] = {"player_id": pid, "action": action, "played_count": played}
        if not state["result"]:
            state["turn_player_id"] = self._next(state, pid)
        return MoveResult(state=state, next_player_id=state["turn_player_id"],
                          result=deepcopy(state["result"]), note=note,
                          public_event={"rummikub_delta": {"melds": deepcopy(state["melds"]),
                                        "meld_kinds": deepcopy(state["meld_kinds"]),
                                        "opened": deepcopy(state["opened"]),
                                        "pool_count": len(state["pool"]),
                                        "hand_counts": {p: len(h) for p, h in state["hands"].items()},
                                        "last_action": deepcopy(state["last_action"]),
                                        "result": deepcopy(state["result"])}})

    def public_state(self, state, participants):
        return {"board_kind": self.game_type, "flow": deepcopy(state["flow"]),
                "melds": deepcopy(state["melds"]),
                "table_tiles": {t: deepcopy(TILES[t]) for m in state["melds"] for t in m},
                "meld_info": [meld_info(m, self._kind(state, i)) for i, m in enumerate(state["melds"])],
                "pool_count": len(state["pool"]), "hand_counts": {p: len(h) for p, h in state["hands"].items()},
                **{k: deepcopy(state[k]) for k in ("opened", "active_player_ids", "turn_player_id",
                    "blocked_player_ids", "resigned_player_ids", "last_action", "result")}}

    def private_state(self, state, viewer, participants):
        pid = str(viewer["player_id"])
        active = (state["flow"]["phase"] == "playing" and not state["result"]
                  and state["turn_player_id"] == pid and pid in state["active_player_ids"])
        return {"hand": [deepcopy(TILES[t]) for t in state["hands"].get(pid, [])],
                "opened": state["opened"].get(pid, False), "opening_threshold": 30,
                "legal_actions": [{"action": "draw" if state["pool"] else "pass"}] if active else [],
                "legal_action_spec": {"action": "meld", "melds": {"type": "array", "minItems": 1,
                    "maxItems": 35, "items": {"type": "array", "minItems": 3, "maxItems": 13,
                                               "items": {"type": "string", "description": "实体牌 ID"}}},
                    "kinds": {"type": "array", "optional": True, "items": {"enum": ["group", "run"]},
                              "description": "与 melds 逐组对应；有两张万能牌时可指定解释；省略则优先较高点数。"},
                    "description": "完整最终桌面；所有旧牌恰好一次，至少一张原手牌；顺子升序。"} if active else None,
                "suggested_move": self._suggest(state, pid) if active else None}

    def mcp_event(self, event, previous):
        from .mcp_incremental import compact_event, changed, table_patch
        event = compact_event(event)
        move = event.get('move') or {}
        if event['event_type'] == 'move' and move.get('action') == 'meld':
            # The paired result contains the authoritative table edit, including
            # resolved joker kinds. Do not echo the complete submitted table.
            event['move'] = {'action': 'meld'}
        value = move.get('rummikub_delta')
        if value is not None:
            old = previous.get('rummikub_delta', {})
            delta = changed(value, old)
            delta.pop('melds', None)
            delta.pop('meld_kinds', None)
            patch = table_patch(value['melds'], value['meld_kinds'],
                                old.get('melds', []), old.get('meld_kinds', []))
            if patch['set'] or len(value['melds']) != len(old.get('melds', [])):
                delta['table_patch'] = patch
                delta['joker_roles'] = {joker: role
                    for m, kind in zip(value['melds'], value['meld_kinds'])
                    for joker, role in meld_info(m, kind)['joker_roles'].items()}
            for key in ('opened', 'hand_counts'):
                updates = changed(value[key], old.get(key, {}))
                delta.pop(key, None)
                if updates:
                    delta[key] = updates
            event['move'] = {'rummikub_delta': delta}
        return event

    def mcp_snapshot_state(self, public_state, viewer, participants):
        public = deepcopy(public_state)
        public.pop('table_tiles', None)
        info = public.pop('meld_info')
        public['meld_kinds'] = [m['kind'] for m in info]
        public['joker_roles'] = {joker: role for m in info for joker, role in m['joker_roles'].items()}
        public['tile_encoding'] = 'red|blue|black|orange-N-copy: N=1..13, copy=1|2; joker-1|joker-2. IDs identify physical tiles.'
        public['delta_format'] = 'table_patch: resize melds/meld_kinds to size, then set each [index,ids,kind]. opened/hand_counts merge by player. Other keys replace. Submit meld with FULL final melds and kinds, never table_patch. Skip event revision <= restored snapshot revision.'
        return public

    mcp_bootstrap_state = mcp_snapshot_state

    def mcp_private_state(self, private_state, viewer, participants):
        private = deepcopy(private_state)
        private['hand'] = [t['id'] for t in private['hand']]
        private['legal_action_spec'] = {'action': 'meld', 'melds': 'FULL final table of tile IDs; old tiles exactly once; add at least one hand tile',
                                        'kinds': 'optional group|run per meld; ascending runs; initial opening >=30 from own rack'} if private_state.get('legal_action_spec') else None
        return private

    def mcp_turn_private_state(self, private, public):
        from .mcp_incremental import table_patch
        private = deepcopy(private)
        suggestion = private.pop('suggested_move', None)
        if suggestion and suggestion['action'] == 'meld':
            private['suggested_table_patch'] = table_patch(suggestion['melds'], suggestion['kinds'],
                                                           public['melds'], public['meld_kinds'])
        elif suggestion:
            private['suggested_move'] = suggestion
        private.pop('opening_threshold', None)
        return private

    def mcp_lifecycle_state(self, public):
        return {k: deepcopy(public[k]) for k in ('active_player_ids', 'resigned_player_ids',
                'blocked_player_ids', 'turn_player_id', 'hand_counts', 'result')}

    def participant_summary(self, state, participant, participants):
        pid = participant["player_id"]
        summary = {"hand_count": state.get("hand_counts", {}).get(pid, 0),
                   "开局状态": "已开局" if state.get("opened", {}).get(pid) else "尚未开局"}
        if state.get("result"):
            score = state["result"].get("tile_scores", {}).get(pid)
            if score is not None:
                summary["开局状态"] = f"局分 {score:+g}"
        return summary

    def result_for(self, state, participants):
        return deepcopy(state.get("result"))

    def apply_resignation(self, state, resigned_player_id, participants):
        if resigned_player_id in state["active_player_ids"]:
            state["active_player_ids"].remove(resigned_player_id)
            state["resigned_player_ids"].append(resigned_player_id)
        state["blocked_player_ids"] = []
        active = state["active_player_ids"]
        if len(active) == 1:
            self._finish(state, "resignation", active)
        elif active and state["turn_player_id"] == resigned_player_id:
            state["turn_player_id"] = self._next(state, resigned_player_id)

    def result_for_resignation(self, state, resigned_player_id, participants):
        if state.get("result"):
            return deepcopy(state["result"])
        return self._finish(state, "only_system_npcs_remaining", [])

    def format_action(self, state, move, actor):
        return {"draw": "摸一张", "pass": "无牌可出", "meld": "提交整桌组合"}.get(move.get("action"), "拉密行动")

    @staticmethod
    def _rack_melds(hand):
        """Polynomial candidate generation: one copy/face suffices per rack meld.

        Greedy repeats can use the second copy. No hidden zones are inspected.
        Jokers may fill gaps or either end; each candidate is authority checked.
        """
        faces = {(TILES[t]["color"], TILES[t]["number"]): t for t in reversed(hand)
                 if TILES[t]["color"] != "joker"}
        jokers = [t for t in hand if TILES[t]["color"] == "joker"]
        candidates = []
        for number in range(1, 14):
            real = [faces[(c, number)] for c in COLORS if (c, number) in faces]
            for size in (3, 4):
                for count in range(max(1, size - len(jokers)), min(len(real), size) + 1):
                    for selected in combinations(real, count):
                        candidates.append(list(selected) + jokers[:size-count])
        for color in COLORS:
            for start in range(1, 12):
                for end in range(start + 2, 14):
                    missing = [n for n in range(start, end + 1) if (color, n) not in faces]
                    if len(missing) <= len(jokers):
                        wild = iter(jokers)
                        candidates.append([faces[(color, n)] if (color, n) in faces else next(wild)
                                           for n in range(start, end + 1)])
        return sorted(candidates, key=lambda m: (-len(m), -meld_info(m)["points"], tuple(m)))

    def _suggest(self, state, pid):
        hand = list(state["hands"][pid])
        table = deepcopy(state["melds"])
        kinds = [meld_info(m, self._kind(state, i))["kind"] for i, m in enumerate(table)]
        candidates = self._rack_melds(hand)
        chosen = []
        # Bounded rack packing. Try each first choice, then greedy disjoint melds;
        # this avoids missing a 30-point opening because a long low run won first.
        best = (0, 0)
        for first in candidates[:128]:
            used, selected = set(first), [first]
            for m in candidates:
                if not used.intersection(m):
                    selected.append(m)
                    used.update(m)
            points = sum(meld_info(m)["points"] for m in selected)
            if not state["opened"][pid] and points < 30:
                continue
            if (len(used), points) > best:
                chosen, best = selected, (len(used), points)
        used = {t for m in chosen for t in m}
        hand = [t for t in hand if t not in used]
        table.extend(chosen)
        kinds.extend(meld_info(m)["kind"] for m in chosen)
        if state["opened"][pid]:
            # Extend/insert into existing combinations. Recheck joker release
            # rules on the whole table, never trust a shape-only suggestion.
            for tile in list(hand):
                accepted = False
                for index, m in enumerate(table):
                    for position in range(len(m) + 1):
                        new = m[:position] + [tile] + m[position:]
                        try:
                            new_kind = meld_info(new)["kind"]
                            proposal = table[:index] + [new] + table[index + 1:]
                            proposed_kinds = kinds[:index] + [new_kind] + kinds[index + 1:]
                            self.validate_action(state, {"action": "meld", "melds": proposal, "kinds": proposed_kinds}, {"player_id": pid})
                        except ValueError:
                            continue
                        table = proposal
                        kinds = proposed_kinds
                        hand.remove(tile)
                        accepted = True
                        break
                    if accepted:
                        break
        move = {"action": "meld", "melds": table, "kinds": kinds}
        try:
            self.validate_action(state, move, {"player_id": pid})
            return move
        except ValueError:
            return {"action": "draw" if state["pool"] else "pass"}

    def npc_legal_actions(self, state, actor, participants):
        pid = str(actor["player_id"])
        if (state["flow"]["phase"] != "playing" or state["result"]
                or state["turn_player_id"] != pid or pid not in state["active_player_ids"]):
            return []
        suggestion = self._suggest(state, pid)
        fallback = {"action": "draw" if state["pool"] else "pass"}
        return [suggestion] if suggestion == fallback else [suggestion, fallback]

    def choose_local_npc_action(self, state, actor, participants):
        actions = self.npc_legal_actions(state, actor, participants)
        return actions[0] if actions else None
