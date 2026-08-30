from __future__ import annotations

import random
from collections import Counter
from copy import deepcopy
from typing import Any

from MahjongGB import MahjongFanCalculator, MahjongShanten

from .base import GamePlugin, MoveResult


WINDS = ("东", "南", "西", "北")
SUIT_LABELS = {"W": "万", "B": "筒", "T": "条"}
HONOR_LABELS = {
    "F1": "东", "F2": "南", "F3": "西", "F4": "北",
    "J1": "中", "J2": "发", "J3": "白",
}
TILE_CODES = tuple(
    [f"W{rank}" for rank in range(1, 10)]
    + [f"B{rank}" for rank in range(1, 10)]
    + [f"T{rank}" for rank in range(1, 10)]
    + [f"F{rank}" for rank in range(1, 5)]
    + [f"J{rank}" for rank in range(1, 4)]
)
TILE_ORDER = {code: index for index, code in enumerate(TILE_CODES)}


def tile_label(code: str) -> str:
    if code in HONOR_LABELS:
        return HONOR_LABELS[code]
    return f"{code[1]}{SUIT_LABELS[code[0]]}"


def build_wall() -> list[dict[str, str]]:
    return [
        {"id": f"{code}-{copy_index}", "code": code}
        for code in TILE_CODES
        for copy_index in range(1, 5)
    ]


def _tile_sort_key(tile: dict[str, Any]) -> tuple[int, str]:
    return TILE_ORDER[str(tile["code"])], str(tile["id"])


class Mahjong(GamePlugin):
    game_type = "mahjong"
    display_name = "麻将"
    category = "card"
    min_players = 4
    max_players = 4
    allowed_player_counts = (4,)
    recommended_players = 4
    supports_npcs = True
    uses_local_npc_strategy = True
    supports_stakes = True
    supports_multiplayer_stakes = True
    mcp_immediate_public_events = True
    rules_text = (
        "【固定规则版本】\n"
        "第一版为四人、136 张无花牌、东一局单手国标麻将。牌只有万、筒、条与东南西北"
        "中发白；座位 0–3 固定对应东、南、西、北，东家为庄家并先摸打。摸牌从牌墙头部，"
        "杠后补牌从牌墙尾部。一手自摸、点炮、抢杠和或荒牌后房间立即结束，不换庄、不进入"
        "下一局。\n\n"
        "【和牌与番数】\n"
        "所有和牌形状、番种互斥与番数由 PyMahjongGB 1.4.0 原生 MahjongFanCalculator"
        "按中国官方/国标语义裁决；所有向听数由原生 MahjongShanten 计算。花牌数固定为 0，"
        "圈风固定东，门风按逻辑座位传入。把引擎返回的各番 point×count 相加，合计至少 8 番"
        "才可和；低于 8 番即使成和也不能和。自摸、绝张、杠上开花/抢杠和、海底捞月/妙手"
        "回春等上下文按实际牌局传给引擎。该上游 SHA 的编译配置 SUPPORT_BLESSINGS=0，"
        "因此不另计天和、地和、人和。\n\n"
        "【吃碰杠与响应优先级】\n"
        "吃仅限上家所打的牌：也就是只有打牌者的下一逻辑座位（下家）可用该弃牌组成顺子；"
        "任意其他玩家可碰或用三张"
        "同牌明杠。暗杠不破门清；加杠只可把自己的明碰升级。每张弃牌依次处理：和牌最高，"
        "其次明杠/碰，最后吃；同一级按距出牌者 1、2、3 个逻辑座位的顺序。所有具有同级"
        "资格的玩家都会依次获得明确的和/过或鸣牌/过窗口，刷新或服务重启不会丢失。多人"
        "可和时采用单和制：离出牌者最近且首个选择和牌者获胜；一旦更高优先级响应成立，"
        "其余响应作废。选择过和后仍可在稍后的碰杠级响应。\n\n"
        "【杠与公开信息】\n"
        "明杠、暗杠、加杠成立后立即从牌墙尾补一张；加杠先给其余三家按座位距离依次检查"
        "抢杠和，任何首个合法和牌立即终止加杠。弃牌、吃、碰、明杠、加杠及响应结果公开。"
        "暗杠固定只公开‘暗杠’与四张牌背，不公开牌面；暗杠者自己仍能看到真实牌面。\n\n"
        "【CedarDuet 娱乐筹码】\n"
        "这是 CedarDuet 钱包政策，不是官方麻将竞赛计分，也不改变 PyMahjongGB 番数计算。"
        "自摸时其余三家各 -stake、和牌者 +3×stake；点炮或抢杠和时来源玩家 -3×stake、"
        "和牌者 +3×stake，另外两家为 0；荒牌四家均为 0。total_fan 不乘算钱包筹码。"
    )
    move_format = (
        '只能原样提交 private_state.legal_actions 中的一项，例如 '
        '{"action":"act","action_id":"discard:W1-1"}；不得自行构造牌面、番数或响应。'
    )

    def __init__(self, rng: random.Random | None = None) -> None:
        self._rng = rng or random.SystemRandom()

    @staticmethod
    def _empty_state(order: list[str]) -> dict[str, Any]:
        return {
            "board_kind": "mahjong",
            "rules_version": "mcr-east-one-136-v1",
            "participant_order": order,
            "seat_winds": {
                player_id: WINDS[index] for index, player_id in enumerate(order)
            },
            "dealer_player_id": order[0] if order else None,
            "prevalent_wind": "东",
            "round_label": "东一局",
            "phase": "waiting",
            "flow": {"phase": "waiting", "round_number": 1, "turn_number": 0},
            "turn_player_id": order[0] if order else None,
            "wall": [],
            "hands": {player_id: [] for player_id in order},
            "melds": {player_id: [] for player_id in order},
            "discards": {player_id: [] for player_id in order},
            "drawn_tile_id": None,
            "draw_context": None,
            "last_discard": None,
            "response_window": None,
            "pending_kong": None,
            "robbed_kong_tiles": [],
            "action_history": [],
            "last_action": None,
            "game_result": None,
        }

    def initial_state(self) -> dict[str, Any]:
        return self._empty_state([])

    def tokens_for(self, participants: list[dict[str, Any]]) -> list[str]:
        return [f"seat-{index}" for index, _item in enumerate(participants)]

    def first_player_id(
        self, participants: list[dict[str, Any]], mode: str
    ) -> str:
        del mode
        ordered = sorted(participants, key=lambda item: item.get("seat_index", 0))
        if not ordered:
            raise ValueError("麻将房间至少需要一个席位")
        return str(ordered[0]["player_id"])

    def initialize(self, participants: list[dict[str, Any]]) -> dict[str, Any]:
        return self.initialize_for_first_player(
            participants, self.first_player_id(participants, "human_first")
        )

    def initialize_for_first_player(
        self,
        participants: list[dict[str, Any]],
        first_player_id: str,
    ) -> dict[str, Any]:
        del first_player_id
        if not 1 <= len(participants) <= 4:
            raise ValueError("麻将固定四人，等待房允许先创建一至三个席位")
        ordered = sorted(participants, key=lambda item: item.get("seat_index", 0))
        order = [str(item["player_id"]) for item in ordered]
        state = self._empty_state(order)
        if len(order) < 4:
            return state

        wall = build_wall()
        self._rng.shuffle(wall)
        state["wall"] = wall
        for _round in range(13):
            for player_id in order:
                state["hands"][player_id].append(state["wall"].pop(0))
        dealer = order[0]
        if not self._draw(state, dealer, replacement=False):
            raise RuntimeError("麻将开局牌墙不足")
        state["phase"] = "discard"
        state["flow"]["phase"] = "discard"
        state["turn_player_id"] = dealer
        return state

    @staticmethod
    def _next_player(state: dict[str, Any], player_id: str) -> str:
        order = state["participant_order"]
        return str(order[(order.index(player_id) + 1) % 4])

    @staticmethod
    def _distance_order(state: dict[str, Any], source_player_id: str) -> list[str]:
        order = state["participant_order"]
        source_index = order.index(source_player_id)
        return [str(order[(source_index + distance) % 4]) for distance in (1, 2, 3)]

    @staticmethod
    def _public_tile(tile: dict[str, Any]) -> dict[str, Any]:
        code = str(tile["code"])
        return {
            "id": str(tile["id"]),
            "code": code,
            "label": tile_label(code),
            "suit": code[0],
            "rank": int(code[1]),
        }

    @staticmethod
    def _offer_for_player(
        state: dict[str, Any], claimant_id: str, source_id: str
    ) -> int:
        order = state["participant_order"]
        return (order.index(claimant_id) - order.index(source_id)) % 4

    @staticmethod
    def _engine_pack(meld: dict[str, Any]) -> tuple[str, str, int]:
        kind = str(meld["kind"])
        pack_type = {
            "chi": "CHI",
            "peng": "PENG",
            "ming_gang": "GANG",
            "concealed_gang": "GANG",
            "added_gang": "GANG",
        }[kind]
        return pack_type, str(meld["engine_tile"]), int(meld["offer"])

    @classmethod
    def _engine_packs_for(
        cls, state: dict[str, Any], player_id: str
    ) -> tuple[tuple[str, str, int], ...]:
        return tuple(
            cls._engine_pack(meld)
            for meld in state.get("melds", {}).get(player_id, [])
        )

    @staticmethod
    def _visible_code_count(state: dict[str, Any], code: str) -> int:
        count = sum(
            tile.get("code") == code
            for pile in state.get("discards", {}).values()
            for tile in pile
        )
        for melds in state.get("melds", {}).values():
            for meld in melds:
                if meld.get("kind") == "concealed_gang":
                    continue
                count += sum(tile.get("code") == code for tile in meld.get("tiles", []))
        pending = state.get("pending_kong")
        if isinstance(pending, dict) and pending.get("tile", {}).get("code") == code:
            count += 1
        return count

    def _fan_evaluation(
        self,
        state: dict[str, Any],
        player_id: str,
        win_tile: dict[str, Any],
        *,
        source_kind: str,
    ) -> dict[str, Any] | None:
        hand = list(state["hands"][player_id])
        is_self_drawn = source_kind == "self_draw"
        if is_self_drawn:
            win_id = str(win_tile["id"])
            for index, tile in enumerate(hand):
                if str(tile["id"]) == win_id:
                    del hand[index]
                    break
            else:
                return None
        code = str(win_tile["code"])
        visible_count = self._visible_code_count(state, code)
        is_fourth = visible_count >= (3 if is_self_drawn else 4)
        draw_context = state.get("draw_context") or {}
        is_about_kong = source_kind == "rob_kong" or (
            is_self_drawn and bool(draw_context.get("replacement"))
        )
        is_wall_last = (
            bool(draw_context.get("wall_last"))
            if is_self_drawn
            else source_kind == "discard" and not state.get("wall")
        )
        seat_wind = state["participant_order"].index(player_id)
        try:
            raw_fans = MahjongFanCalculator(
                pack=self._engine_packs_for(state, player_id),
                hand=tuple(str(tile["code"]) for tile in hand),
                winTile=code,
                flowerCount=0,
                isSelfDrawn=is_self_drawn,
                is4thTile=is_fourth,
                isAboutKong=is_about_kong,
                isWallLast=is_wall_last,
                seatWind=seat_wind,
                prevalentWind=0,
                verbose=True,
            )
        except TypeError:
            return None
        fans = [
            {
                "points": int(points),
                "count": int(count),
                "name": str(name),
                "name_en": str(name_en),
                "fan": int(points) * int(count),
            }
            for points, count, name, name_en in raw_fans
        ]
        total = sum(item["fan"] for item in fans)
        return {"total_fan": total, "fans": fans, "meets_minimum": total >= 8}

    def _hu_action(
        self,
        state: dict[str, Any],
        player_id: str,
        tile: dict[str, Any],
        source_kind: str,
    ) -> dict[str, Any] | None:
        evaluation = self._fan_evaluation(
            state, player_id, tile, source_kind=source_kind
        )
        if not evaluation or not evaluation["meets_minimum"]:
            return None
        return {
            "action": "act",
            "action_id": f"hu:{source_kind}:{tile['id']}",
            "kind": "hu",
            "source_kind": source_kind,
            "tile": deepcopy(tile),
            "label": f"和（{evaluation['total_fan']} 番）",
            "public_label": "和牌",
            **evaluation,
        }

    @staticmethod
    def _ids_for_code(
        state: dict[str, Any], player_id: str, code: str
    ) -> list[str]:
        return sorted(
            str(tile["id"])
            for tile in state["hands"][player_id]
            if tile["code"] == code
        )

    def _chi_actions(
        self,
        state: dict[str, Any],
        player_id: str,
        tile: dict[str, Any],
    ) -> list[dict[str, Any]]:
        code = str(tile["code"])
        if code[0] not in SUIT_LABELS:
            return []
        rank = int(code[1])
        actions: list[dict[str, Any]] = []
        for start in range(max(1, rank - 2), min(rank, 7) + 1):
            sequence = [f"{code[0]}{value}" for value in range(start, start + 3)]
            needed = list(sequence)
            needed.remove(code)
            consume_ids: list[str] = []
            for needed_code in needed:
                choices = self._ids_for_code(state, player_id, needed_code)
                if not choices:
                    consume_ids = []
                    break
                consume_ids.append(choices[0])
            if len(consume_ids) != 2:
                continue
            actions.append({
                "action": "act",
                "action_id": f"chi:{start}:{'-'.join(consume_ids)}",
                "kind": "chi",
                "consume_ids": consume_ids,
                "sequence": sequence,
                "engine_tile": sequence[1],
                "offer": sequence.index(code) + 1,
                "label": f"吃 {' '.join(tile_label(item) for item in sequence)}",
                "public_label": "吃",
            })
        return actions

    def _discard_response_queue(
        self,
        state: dict[str, Any],
        source_player_id: str,
        tile: dict[str, Any],
    ) -> list[dict[str, Any]]:
        distance_order = self._distance_order(state, source_player_id)
        queue: list[dict[str, Any]] = []
        for player_id in distance_order:
            hu = self._hu_action(state, player_id, tile, "discard")
            if hu:
                queue.append({"player_id": player_id, "priority": "hu", "actions": [hu]})
        # The final discard after the wall is exhausted permits only a win.
        # There is no subsequent live-wall draw with which to continue a meld.
        if not state.get("wall"):
            return queue
        code = str(tile["code"])
        for player_id in distance_order:
            ids = self._ids_for_code(state, player_id, code)
            actions: list[dict[str, Any]] = []
            if len(ids) >= 3 and state.get("wall"):
                actions.append({
                    "action": "act",
                    "action_id": f"ming_gang:{'-'.join(ids[:3])}",
                    "kind": "ming_gang",
                    "consume_ids": ids[:3],
                    "label": f"明杠 {tile_label(code)}",
                    "public_label": "明杠",
                })
            if len(ids) >= 2:
                actions.append({
                    "action": "act",
                    "action_id": f"peng:{'-'.join(ids[:2])}",
                    "kind": "peng",
                    "consume_ids": ids[:2],
                    "label": f"碰 {tile_label(code)}",
                    "public_label": "碰",
                })
            if actions:
                queue.append({"player_id": player_id, "priority": "peng_gang", "actions": actions})
        next_player = distance_order[0]
        chi_actions = self._chi_actions(state, next_player, tile)
        if chi_actions:
            queue.append({"player_id": next_player, "priority": "chi", "actions": chi_actions})
        return queue

    def _rob_kong_queue(
        self,
        state: dict[str, Any],
        source_player_id: str,
        tile: dict[str, Any],
    ) -> list[dict[str, Any]]:
        queue = []
        for player_id in self._distance_order(state, source_player_id):
            hu = self._hu_action(state, player_id, tile, "rob_kong")
            if hu:
                queue.append({"player_id": player_id, "priority": "hu", "actions": [hu]})
        return queue

    @staticmethod
    def _pass_action(window: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": "act",
            "action_id": f"pass:{window['source_kind']}:{window['index']}",
            "kind": "pass",
            "label": "过",
            "public_label": "过",
        }

    def legal_actions_for(
        self, state: dict[str, Any], player_id: str
    ) -> list[dict[str, Any]]:
        if state.get("game_result") is not None or state.get("turn_player_id") != player_id:
            return []
        if state.get("phase") == "response":
            window = state.get("response_window") or {}
            queue = window.get("queue") or []
            index = int(window.get("index", 0))
            if index >= len(queue) or queue[index].get("player_id") != player_id:
                return []
            return [*deepcopy(queue[index]["actions"]), self._pass_action(window)]
        if state.get("phase") != "discard":
            return []
        hand = sorted(state["hands"][player_id], key=_tile_sort_key)
        actions: list[dict[str, Any]] = []
        drawn_id = state.get("drawn_tile_id")
        if drawn_id:
            drawn = next(
                (tile for tile in hand if tile["id"] == drawn_id), None
            )
            if drawn:
                hu = self._hu_action(state, player_id, drawn, "self_draw")
                if hu:
                    actions.append(hu)
        if state.get("wall"):
            by_code: dict[str, list[dict[str, Any]]] = {}
            for tile in hand:
                by_code.setdefault(str(tile["code"]), []).append(tile)
            for code, tiles in by_code.items():
                if len(tiles) == 4:
                    ids = [str(tile["id"]) for tile in tiles]
                    actions.append({
                        "action": "act",
                        "action_id": f"concealed_gang:{'-'.join(ids)}",
                        "kind": "concealed_gang",
                        "consume_ids": ids,
                        "code": code,
                        "label": f"暗杠 {tile_label(code)}",
                        "public_label": "暗杠",
                    })
            for meld_index, meld in enumerate(state["melds"][player_id]):
                if meld.get("kind") != "peng":
                    continue
                code = str(meld["engine_tile"])
                ids = self._ids_for_code(state, player_id, code)
                if ids:
                    actions.append({
                        "action": "act",
                        "action_id": f"added_gang:{meld_index}:{ids[0]}",
                        "kind": "added_gang",
                        "meld_index": meld_index,
                        "tile_id": ids[0],
                        "label": f"加杠 {tile_label(code)}",
                        "public_label": "加杠",
                    })
        actions.extend({
            "action": "act",
            "action_id": f"discard:{tile['id']}",
            "kind": "discard",
            "tile_id": str(tile["id"]),
            "label": f"打 {tile_label(str(tile['code']))}",
            "public_label": f"打 {tile_label(str(tile['code']))}",
        } for tile in hand)
        return actions

    def _resolve_action(
        self, state: dict[str, Any], player_id: str, move: dict[str, Any]
    ) -> dict[str, Any]:
        if not isinstance(move, dict):
            raise ValueError("move 必须是对象")
        if set(move) != {"action", "action_id"} or move.get("action") != "act":
            raise ValueError("只能提交 action=act 与服务端发布的 action_id")
        action_id = move.get("action_id")
        matches = [
            action for action in self.legal_actions_for(state, player_id)
            if action["action_id"] == action_id
        ]
        if len(matches) != 1:
            raise ValueError("action_id 不在当前权威 legal_actions 中")
        return matches[0]

    def validate_action(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
    ) -> None:
        player_id = str(actor["player_id"])
        if state.get("phase") == "finished":
            raise ValueError("本手已经结束")
        if player_id not in state.get("participant_order", []):
            raise ValueError("行动者不在本桌")
        if state.get("turn_player_id") != player_id:
            raise ValueError("当前行动权属于另一名参与者")
        self._resolve_action(state, player_id, move)

    @staticmethod
    def _take_hand_tiles(
        state: dict[str, Any], player_id: str, tile_ids: list[str]
    ) -> list[dict[str, Any]]:
        hand = state["hands"][player_id]
        wanted = set(tile_ids)
        if len(wanted) != len(tile_ids):
            raise ValueError("牌张 ID 不能重复")
        selected = [tile for tile in hand if tile["id"] in wanted]
        if len(selected) != len(tile_ids):
            raise ValueError("所选牌不全在行动者手中")
        state["hands"][player_id] = [tile for tile in hand if tile["id"] not in wanted]
        return selected

    @staticmethod
    def _draw(state: dict[str, Any], player_id: str, *, replacement: bool) -> bool:
        wall = state.get("wall") or []
        if not wall:
            return False
        tile = wall.pop(-1 if replacement else 0)
        state["hands"][player_id].append(tile)
        state["drawn_tile_id"] = tile["id"]
        state["draw_context"] = {
            "player_id": player_id,
            "replacement": replacement,
            "wall_last": len(wall) == 0,
        }
        state["phase"] = "discard"
        state["flow"]["phase"] = "discard"
        state["turn_player_id"] = player_id
        return True

    @staticmethod
    def _record_action(
        state: dict[str, Any], player_id: str, action: dict[str, Any], **extra: Any
    ) -> None:
        public = {
            "number": len(state["action_history"]) + 1,
            "player_id": player_id,
            "kind": action["kind"],
            "label": action.get("public_label", action.get("label", action["kind"])),
            **extra,
        }
        state["action_history"].append(public)
        state["last_action"] = public
        state["flow"]["turn_number"] = int(state["flow"].get("turn_number", 0)) + 1

    @staticmethod
    def _draw_game_result(reason: str = "wall_exhausted") -> dict[str, Any]:
        return {"draw": True, "reason": reason, "single_hand_end": True}

    def _finish_draw(self, state: dict[str, Any]) -> dict[str, Any]:
        result = self._draw_game_result()
        state["phase"] = "finished"
        state["flow"]["phase"] = "finished"
        state["turn_player_id"] = None
        state["response_window"] = None
        state["game_result"] = result
        return result

    def _finish_win(
        self,
        state: dict[str, Any],
        winner_id: str,
        action: dict[str, Any],
        source_player_id: str | None,
    ) -> dict[str, Any]:
        source_kind = str(action["source_kind"])
        tile = deepcopy(action["tile"])
        if source_kind == "rob_kong":
            pending = state.get("pending_kong") or {}
            robbed = self._take_hand_tiles(
                state, str(pending["player_id"]), [str(tile["id"])]
            )[0]
            state["robbed_kong_tiles"].append(robbed)
            state["pending_kong"] = None
        win_type = {
            "self_draw": "self_draw",
            "discard": "discard",
            "rob_kong": "rob_kong",
        }[source_kind]
        result = {
            "winner_player_id": winner_id,
            "winner_player_ids": [winner_id],
            "draw": False,
            "win_type": win_type,
            "source_player_id": source_player_id,
            "winning_tile": self._public_tile(tile),
            "total_fan": int(action["total_fan"]),
            "fans": deepcopy(action["fans"]),
            "seat_wind": state["seat_winds"][winner_id],
            "prevalent_wind": "东",
            "minimum_fan": 8,
            "single_hand_end": True,
        }
        state["phase"] = "finished"
        state["flow"]["phase"] = "finished"
        state["turn_player_id"] = None
        state["response_window"] = None
        state["game_result"] = result
        return result

    @staticmethod
    def _remove_source_discard(
        state: dict[str, Any], source_player_id: str, tile_id: str
    ) -> dict[str, Any]:
        pile = state["discards"][source_player_id]
        if not pile or pile[-1]["id"] != tile_id:
            raise ValueError("响应窗口对应的弃牌已经变化")
        tile = pile.pop()
        state["last_discard"] = None
        return tile

    def _complete_added_gang(self, state: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
        pending = state.get("pending_kong") or {}
        player_id = str(pending["player_id"])
        meld_index = int(pending["meld_index"])
        tile = self._take_hand_tiles(state, player_id, [str(pending["tile"]["id"])])[0]
        meld = state["melds"][player_id][meld_index]
        if meld.get("kind") != "peng" or meld.get("engine_tile") != tile.get("code"):
            raise ValueError("待加杠的明碰已经变化")
        meld["kind"] = "added_gang"
        meld["tiles"].append(tile)
        state["pending_kong"] = None
        state["response_window"] = None
        if not self._draw(state, player_id, replacement=True):
            return None, self._finish_draw(state)
        return player_id, None

    def _after_response_passes(
        self, state: dict[str, Any]
    ) -> tuple[str | None, dict[str, Any] | None]:
        window = state.get("response_window") or {}
        if window.get("source_kind") == "added_kong":
            return self._complete_added_gang(state)
        source_player_id = str(window["source_player_id"])
        state["response_window"] = None
        next_player = self._next_player(state, source_player_id)
        if not self._draw(state, next_player, replacement=False):
            return None, self._finish_draw(state)
        return next_player, None

    def _apply_claim(
        self,
        state: dict[str, Any],
        player_id: str,
        action: dict[str, Any],
    ) -> tuple[str | None, dict[str, Any] | None]:
        window = state.get("response_window") or {}
        source_id = str(window["source_player_id"])
        source_tile = self._remove_source_discard(
            state, source_id, str(window["tile"]["id"])
        )
        consumed = self._take_hand_tiles(state, player_id, list(action["consume_ids"]))
        kind = str(action["kind"])
        if kind == "chi":
            engine_tile = str(action["engine_tile"])
            offer = int(action["offer"])
        else:
            engine_tile = str(source_tile["code"])
            offer = self._offer_for_player(state, player_id, source_id)
        meld = {
            "kind": kind,
            "tiles": sorted([*consumed, source_tile], key=_tile_sort_key),
            "engine_tile": engine_tile,
            "offer": offer,
            "source_player_id": source_id,
        }
        state["melds"][player_id].append(meld)
        state["response_window"] = None
        state["drawn_tile_id"] = None
        state["draw_context"] = None
        state["phase"] = "discard"
        state["flow"]["phase"] = "discard"
        state["turn_player_id"] = player_id
        if kind == "ming_gang":
            if not self._draw(state, player_id, replacement=True):
                return None, self._finish_draw(state)
        return player_id, None

    def apply_action(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
    ) -> MoveResult:
        player_id = str(actor["player_id"])
        action = self._resolve_action(state, player_id, move)
        next_state = deepcopy(state)
        kind = str(action["kind"])
        result: dict[str, Any] | None = None
        next_player_id: str | None = None

        if kind == "discard":
            tile = self._take_hand_tiles(next_state, player_id, [str(action["tile_id"])])[0]
            next_state["discards"][player_id].append(tile)
            next_state["last_discard"] = {
                "player_id": player_id,
                "tile": deepcopy(tile),
                "discard_index": len(next_state["discards"][player_id]) - 1,
            }
            next_state["drawn_tile_id"] = None
            next_state["draw_context"] = None
            self._record_action(
                next_state, player_id, action, tile=self._public_tile(tile)
            )
            queue = self._discard_response_queue(next_state, player_id, tile)
            if queue:
                next_state["response_window"] = {
                    "source_kind": "discard",
                    "source_player_id": player_id,
                    "tile": deepcopy(tile),
                    "queue": queue,
                    "index": 0,
                    "decisions": [],
                }
                next_state["phase"] = "response"
                next_state["flow"]["phase"] = "response"
                next_player_id = str(queue[0]["player_id"])
                next_state["turn_player_id"] = next_player_id
            else:
                next_player_id = self._next_player(next_state, player_id)
                if not self._draw(next_state, next_player_id, replacement=False):
                    next_player_id = None
                    result = self._finish_draw(next_state)

        elif kind == "pass":
            window = next_state.get("response_window") or {}
            queue = window.get("queue") or []
            index = int(window.get("index", 0))
            window.setdefault("decisions", []).append({
                "player_id": player_id,
                "priority": queue[index]["priority"],
                "decision": "pass",
            })
            self._record_action(next_state, player_id, action)
            window["index"] = index + 1
            if window["index"] < len(queue):
                next_player_id = str(queue[window["index"]]["player_id"])
                next_state["turn_player_id"] = next_player_id
            else:
                next_player_id, result = self._after_response_passes(next_state)

        elif kind == "hu":
            source_kind = str(action["source_kind"])
            source_id = None
            if source_kind != "self_draw":
                source_id = str((next_state.get("response_window") or {})["source_player_id"])
            self._record_action(
                next_state,
                player_id,
                action,
                tile=self._public_tile(action["tile"]),
                total_fan=int(action["total_fan"]),
            )
            result = self._finish_win(next_state, player_id, action, source_id)

        elif kind in {"chi", "peng", "ming_gang"}:
            self._record_action(
                next_state,
                player_id,
                action,
                tile=self._public_tile((next_state.get("response_window") or {})["tile"]),
            )
            next_player_id, result = self._apply_claim(next_state, player_id, action)

        elif kind == "concealed_gang":
            tiles = self._take_hand_tiles(next_state, player_id, list(action["consume_ids"]))
            next_state["melds"][player_id].append({
                "kind": "concealed_gang",
                "tiles": sorted(tiles, key=_tile_sort_key),
                "engine_tile": str(action["code"]),
                "offer": 0,
                "source_player_id": None,
            })
            self._record_action(next_state, player_id, action)
            if not self._draw(next_state, player_id, replacement=True):
                result = self._finish_draw(next_state)
            else:
                next_player_id = player_id

        elif kind == "added_gang":
            tile = next(
                tile for tile in next_state["hands"][player_id]
                if tile["id"] == action["tile_id"]
            )
            next_state["pending_kong"] = {
                "player_id": player_id,
                "meld_index": int(action["meld_index"]),
                "tile": deepcopy(tile),
            }
            self._record_action(
                next_state, player_id, action, tile=self._public_tile(tile)
            )
            queue = self._rob_kong_queue(next_state, player_id, tile)
            if queue:
                next_state["response_window"] = {
                    "source_kind": "added_kong",
                    "source_player_id": player_id,
                    "tile": deepcopy(tile),
                    "queue": queue,
                    "index": 0,
                    "decisions": [],
                }
                next_state["phase"] = "response"
                next_state["flow"]["phase"] = "response"
                next_player_id = str(queue[0]["player_id"])
                next_state["turn_player_id"] = next_player_id
            else:
                next_player_id, result = self._complete_added_gang(next_state)
        else:
            raise ValueError("未知麻将动作")

        note = str(action.get("public_label", action.get("label", kind)))
        return MoveResult(
            state=next_state,
            next_player_id=next_player_id,
            note=note,
            result=result,
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
        if isinstance(applied, MoveResult):
            applied.public_event = {"mahjong_delta": self._public_delta(applied.state)}
        return applied

    def validate_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> None:
        del state, move, mark
        raise ValueError("麻将需要 participant-aware action 接口")

    def apply_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> dict[str, Any]:
        del state, move, mark
        raise ValueError("麻将需要 participant-aware action 接口")

    def check_winner(self, state: dict[str, Any]) -> str | None:
        del state
        return None

    def result_for(
        self,
        state: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        del participants
        return deepcopy(state.get("game_result"))

    def settlement_deltas(
        self,
        state: dict[str, Any],
        result: dict[str, Any],
        participants: list[dict[str, Any]],
        stake: int,
    ) -> dict[str, int]:
        del state
        player_ids = [str(item["player_id"]) for item in participants]
        if len(player_ids) != 4:
            raise ValueError("麻将筹码结算固定需要四名参与者")
        if result.get("draw"):
            return {player_id: 0 for player_id in player_ids}
        winner = result.get("winner_player_id")
        if winner not in player_ids:
            raise ValueError("麻将终局缺少有效和牌者")
        win_type = result.get("win_type")
        if win_type == "self_draw":
            return {
                player_id: 3 * stake if player_id == winner else -stake
                for player_id in player_ids
            }
        if win_type in {"discard", "rob_kong"}:
            source = result.get("source_player_id")
            if source not in player_ids or source == winner:
                raise ValueError("麻将点炮或抢杠和终局缺少有效来源玩家")
            return {
                player_id: (
                    3 * stake if player_id == winner
                    else -3 * stake if player_id == source
                    else 0
                )
                for player_id in player_ids
            }
        raise ValueError("麻将终局缺少有效和牌类型")

    @classmethod
    def _public_meld(cls, meld: dict[str, Any], *, reveal_concealed: bool = False) -> dict[str, Any]:
        concealed = meld.get("kind") == "concealed_gang" and not reveal_concealed
        return {
            "kind": str(meld["kind"]),
            "tile_count": len(meld.get("tiles", [])),
            "tiles": (
                [{"back": True} for _tile in meld.get("tiles", [])]
                if concealed
                else [cls._public_tile(tile) for tile in meld.get("tiles", [])]
            ),
            "source_player_id": meld.get("source_player_id"),
        }

    @classmethod
    def _public_response(cls, state: dict[str, Any]) -> dict[str, Any] | None:
        window = state.get("response_window")
        if not isinstance(window, dict):
            return None
        queue = window.get("queue") or []
        index = int(window.get("index", 0))
        current = queue[index] if index < len(queue) else None
        return {
            "source_kind": window.get("source_kind"),
            "source_player_id": window.get("source_player_id"),
            "tile": cls._public_tile(window["tile"]),
            "current_responder_id": current.get("player_id") if current else None,
            "current_priority": current.get("priority") if current else None,
            "remaining_responses": max(0, len(queue) - index),
            "completed_responses": deepcopy(window.get("decisions", [])),
        }

    def public_state(
        self,
        state: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any]:
        del participants
        return {
            "board_kind": "mahjong",
            "rules_version": state.get("rules_version"),
            "participant_order": list(state.get("participant_order", [])),
            "seat_winds": deepcopy(state.get("seat_winds", {})),
            "dealer_player_id": state.get("dealer_player_id"),
            "prevalent_wind": "东",
            "round_label": "东一局",
            "phase": state.get("phase"),
            "turn_player_id": state.get("turn_player_id"),
            "wall_remaining": len(state.get("wall", [])),
            "hand_counts": {
                player_id: len(hand)
                for player_id, hand in state.get("hands", {}).items()
            },
            "melds": {
                player_id: [self._public_meld(meld) for meld in melds]
                for player_id, melds in state.get("melds", {}).items()
            },
            "discards": {
                player_id: [self._public_tile(tile) for tile in pile]
                for player_id, pile in state.get("discards", {}).items()
            },
            "last_discard": (
                {
                    **{key: value for key, value in state["last_discard"].items() if key != "tile"},
                    "tile": self._public_tile(state["last_discard"]["tile"]),
                }
                if state.get("last_discard") else None
            ),
            "response_window": self._public_response(state),
            "last_action": deepcopy(state.get("last_action")),
            "action_history": deepcopy(state.get("action_history", [])),
            "game_result": deepcopy(state.get("game_result")),
            "last_action_note": state.get("last_action_note", ""),
        }

    def _shanten(self, state: dict[str, Any], player_id: str) -> tuple[int | None, str]:
        packs = self._engine_packs_for(state, player_id)
        hand = sorted(state.get("hands", {}).get(player_id, []), key=_tile_sort_key)
        target_count = 13 - 3 * len(packs)
        try:
            if len(hand) == target_count:
                return int(MahjongShanten(packs, tuple(tile["code"] for tile in hand))), "current"
            if len(hand) == target_count + 1:
                values = []
                for tile in hand:
                    reduced = list(hand)
                    reduced.remove(tile)
                    values.append(int(MahjongShanten(
                        packs, tuple(item["code"] for item in reduced)
                    )))
                return min(values), "after_best_discard"
        except TypeError:
            pass
        return None, "unavailable"

    def private_state(
        self,
        state: dict[str, Any],
        viewer: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any]:
        del participants
        player_id = str(viewer["player_id"])
        hand = sorted(state.get("hands", {}).get(player_id, []), key=_tile_sort_key)
        shanten, basis = self._shanten(state, player_id)
        return {
            "hand": [self._public_tile(tile) for tile in hand],
            "drawn_tile_id": (
                state.get("drawn_tile_id")
                if state.get("turn_player_id") == player_id else None
            ),
            "own_melds": [
                self._public_meld(meld, reveal_concealed=True)
                for meld in state.get("melds", {}).get(player_id, [])
            ],
            "shanten": shanten,
            "shanten_basis": basis,
            "legal_actions": [
                {key: deepcopy(value) for key, value in action.items() if key not in {
                    "consume_ids", "tile_id", "meld_index", "code", "engine_tile", "offer",
                }}
                for action in self.legal_actions_for(state, player_id)
            ],
        }

    def mcp_snapshot_state(
        self,
        public_state: dict[str, Any],
        viewer: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any]:
        del viewer, participants
        snapshot = deepcopy(public_state)
        snapshot.pop("action_history", None)
        return snapshot

    def participant_summary(
        self,
        state: dict[str, Any],
        participant: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, str | int | bool]:
        del participants
        player_id = str(participant["player_id"])
        return {
            "wind": str(state.get("seat_winds", {}).get(player_id, "?")),
            "dealer": player_id == state.get("dealer_player_id"),
            "hand_count": int(state.get("hand_counts", {}).get(player_id, 0)),
        }

    def project_event(
        self,
        event: dict[str, Any],
        viewer: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        del viewer, participants
        projected = deepcopy(event)
        move = projected.get("move")
        if isinstance(move, dict) and move.get("action") == "act":
            projected["move"] = {"action": "act"}
        return projected

    def _public_delta(self, state: dict[str, Any]) -> dict[str, Any]:
        public = self.public_state(state, [])
        return {
            key: deepcopy(public[key])
            for key in (
                "phase", "turn_player_id", "wall_remaining", "hand_counts",
                "melds", "discards", "last_discard", "response_window",
                "last_action", "game_result",
            )
        }

    def npc_compact_rules(
        self,
        state: dict[str, Any],
        actor: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> str:
        del state, actor, participants
        return (
            "四人 136 张东一局国标麻将，PyMahjongGB 原生算番且 8 番起和。"
            "只能从 authoritative legal_actions 原样选择 action_id。"
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
        return [
            {"action": "act", "action_id": str(action["action_id"])}
            for action in self.legal_actions_for(state, str(actor["player_id"]))
        ]

    def choose_local_npc_action(
        self,
        state: dict[str, Any],
        actor: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        legal = self.npc_legal_actions(state, actor, participants)
        if not legal:
            return None
        full = {
            action["action_id"]: action
            for action in self.legal_actions_for(state, str(actor["player_id"]))
        }
        priority = {
            "hu": 0, "ming_gang": 1, "added_gang": 1,
            "concealed_gang": 1, "peng": 2, "chi": 3,
            "discard": 4, "pass": 5,
        }
        return min(legal, key=lambda item: priority.get(
            str(full[item["action_id"]].get("kind")), 9
        ))

    def format_action(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
    ) -> str:
        action = next((
            item for item in self.legal_actions_for(state, str(actor["player_id"]))
            if item["action_id"] == move.get("action_id")
        ), None)
        return str(action.get("public_label", "无效动作")) if action else "无效动作"

    def format_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> str:
        del state, mark
        return str(move.get("action_id", "act"))
