from __future__ import annotations

import random
from copy import deepcopy
from typing import Any

from third_party.rlcard_guandan.engine import (
    GuandanEngine,
    card_label,
    card_sort_key,
    is_wild,
)

from .base import GamePlugin, MoveResult


TEAM_LABELS = {"A": "甲队", "B": "乙队"}
PHASE_LABELS = {
    "waiting": "等待满座",
    "playing": "出牌",
    "tribute": "进贡",
    "return_tribute": "还贡",
    "finished": "比赛结束",
}


class Guandan(GamePlugin):
    game_type = "guandan"
    display_name = "掼蛋"
    category = "card"
    min_players = 4
    max_players = 4
    allowed_player_counts = (4,)
    recommended_players = 4
    supports_npcs = True
    supports_stakes = True
    supports_multiplayer_stakes = True
    mcp_immediate_public_events = True
    rules_text = (
        "【规则版本】\n"
        "本桌规则固定对齐 Choysang/rlcard-guandan 当前标记为 0.1.0 的 "
        "docs/rules.md（2026-08-30 审阅）："
        "四人、两副牌共 108 张，座位 1/3 为甲队、2/4 为乙队；一间双弈房间是一场从 "
        "2 打到 A 的完整升级赛，不把单副牌截成一局。上游已知简化也原样固定：双下后"
        "未出完两席按座次补第 3/4 名；双贡同点时头游取得末游贡牌、头游下家领出。\n\n"
        "【级牌与牌型】\n"
        "级牌在非顺序牌型中高于 A；两张红桃级牌是逢人配，可代替除王以外的牌。"
        "支持单张、对子、三张、三带二、三连对（固定 6 张）、钢板（固定 6 张）、"
        "顺子（固定 5 张）、同花顺、4–10 张同点炸弹和四王炸。顺子、三连对、钢板"
        "按自然点数比较，允许 A2345、AA2233、AAA222 等 A 作低位，也允许到 TJQKA，"
        "不允许 K-A-2 回绕。\n\n"
        "【压制与接风】\n"
        "普通牌必须同牌型同张数且更大。炸弹顺序为：四王炸 > 6–10 张同点炸弹 > "
        "同花顺 > 5 张炸弹 > 4 张炸弹；同类同长度再比较点数。跟牌可过，引牌不可过；"
        "最后出牌者以外的仍在局玩家全过后清墩。若最后出牌者已经出完，则由仍在局的"
        "对家接风领出。\n\n"
        "【出完与升级】\n"
        "同队包揽头游、二游时立即结束本副；否则第三人出完即结束。头游的对家为"
        "二/三/四游时，该队分别升 3/2/1 级且不越过 A。队伍已在 A 时，只有取得"
        "头游+二游或头游+三游才赢得整场；头游+四游仍留在 A 继续下一副。\n\n"
        "【进贡、还贡、抗贡】\n"
        "下一副发牌后按上一副名次进贡；双下由两名败方各贡一张，否则末游向头游"
        "进贡。贡牌是手中最大牌，红桃级牌豁免；有王时王自然成为最大贡牌。受贡者"
        "须还一张点数不高于 10 且不是任何花色级牌的牌。双贡较大者给头游；抗贡条件"
        "为双贡方合计持有两张大王，单贡时为贡方独自持有两张大王。抗贡后由上一副"
        "头游领出。\n\n"
        "【双弈边界】\n"
        "所有牌型、比较、权威合法动作、接风、贡还、抗贡、升级和终局均由实际 vendor"
        " 的 Choysang/rlcard-guandan v0.1.0 规则核心裁定；双弈适配层只保存上游对象、"
        "映射 action_id 并生成安全投影。未公开手牌只进入本人私密视图；客户端和 NPC 只能提交"
        "核心当前发布的 action_id。\n\n"
        "【CedarDuet 娱乐筹码】\n"
        "钱包结算与上述上游升级、名次和级差计分明确分开，只在完整 2 到 A 比赛结束时进行："
        "获胜队两名玩家各 +stake，落败队两名玩家各 -stake，四人合计为 0；不按领先等级、"
        "副数或名次差追加倍数。这只是 CedarDuet 钱包政策，不改写上游掼蛋计分。"
    )
    move_format = (
        '只提交规则核心发布的短 ID：{"move":{"action":"act",'
        '"action_id":"g_..."},"revision":当前版本}。action_id 必须来自本人当前'
        " private_state.legal_actions；不得自行枚举或改写 card_ids。"
    )

    def __init__(self, rng: random.Random | None = None) -> None:
        self._rng = rng or random.SystemRandom()

    @staticmethod
    def tokens_for(participants: list[dict[str, Any]]) -> list[str]:
        return [f"P{index + 1}" for index, _item in enumerate(participants)]

    def initial_state(self) -> dict[str, Any]:
        return GuandanEngine.waiting_state([])

    def initialize(self, participants: list[dict[str, Any]]) -> dict[str, Any]:
        ordered = sorted(participants, key=lambda item: item.get("seat_index", 0))
        if not ordered:
            return self.initial_state()
        opener = next(
            (
                str(item["player_id"]) for item in ordered
                if item.get("_opening_player")
            ),
            str(ordered[0]["player_id"]),
        )
        return self.initialize_for_first_player(participants, opener)

    def initialize_for_first_player(
        self,
        participants: list[dict[str, Any]],
        first_player_id: str,
    ) -> dict[str, Any]:
        ordered = sorted(participants, key=lambda item: item.get("seat_index", 0))
        order = [str(item["player_id"]) for item in ordered]
        if not 1 <= len(order) <= 4:
            raise ValueError("掼蛋固定四人，等待房只允许 1–3 个已占席位")
        if first_player_id not in order:
            raise ValueError("掼蛋首位必须是本桌参与者")
        if len(order) < 4:
            return GuandanEngine.waiting_state(order)
        return GuandanEngine.new_match(order, first_player_id, self._rng)

    @staticmethod
    def _compact_legal(action: dict[str, Any]) -> dict[str, Any]:
        compact = {
            "action_id": str(action["action_id"]),
            "kind": str(action["kind"]),
            "card_ids": list(action.get("card_ids", [])),
            "label": str(action.get("pattern_label") or action["kind"]),
        }
        pattern_type = action.get("pattern_type")
        if pattern_type:
            compact["pattern_type"] = str(pattern_type)
        return compact

    @staticmethod
    def _public_card(card: dict[str, Any], level_rank: str) -> dict[str, Any]:
        projected = deepcopy(card)
        projected["wild"] = is_wild(card, level_rank)
        projected["label"] = card_label(card)
        return projected

    @classmethod
    def _public_play(cls, play: dict[str, Any] | None, level_rank: str) -> dict[str, Any] | None:
        if not isinstance(play, dict):
            return None
        return {
            "player_id": play["player_id"],
            "cards": [cls._public_card(card, level_rank) for card in play.get("cards", [])],
            "pattern": deepcopy(play.get("pattern")),
        }

    @classmethod
    def _public_tribute(cls, state: dict[str, Any]) -> dict[str, Any]:
        level_rank = str(state.get("level_rank", "2"))
        tribute = GuandanEngine.public_tribute(state)
        for key in ("tributes", "returns"):
            for item in tribute.get(key, []):
                if isinstance(item.get("card"), dict):
                    item["card"] = cls._public_card(item["card"], level_rank)
        return tribute

    @classmethod
    def _public_delta(cls, state: dict[str, Any]) -> dict[str, Any]:
        delta = deepcopy(state.get("last_public_delta") or {})
        # A deal-ending transition may already have advanced the match level.
        # Cards in that transition must retain the level under which they were
        # actually played, not be re-labelled with the next deal's level.
        deal_end = delta.get("deal_end")
        played_level = (
            deal_end.get("played_level")
            if isinstance(deal_end, dict)
            else None
        )
        level_rank = str(played_level or state.get("level_rank", "2"))
        cards = delta.get("cards")
        if isinstance(cards, list):
            delta["cards"] = [cls._public_card(card, level_rank) for card in cards]
        card = delta.get("card")
        if isinstance(card, dict):
            delta["card"] = cls._public_card(card, level_rank)
        if "tribute" in delta:
            delta["tribute"] = cls._public_tribute(state)
        return delta

    def validate_action(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
    ) -> None:
        if not isinstance(move, dict) or set(move) != {"action", "action_id"}:
            raise ValueError("move 只接受 action 与 action_id")
        if move.get("action") != "act":
            raise ValueError("掼蛋 action 固定为 act")
        action_id = move.get("action_id")
        if not isinstance(action_id, str) or not action_id.startswith("g_"):
            raise ValueError("action_id 必须是规则核心发布的 g_ 短 ID")
        player_id = str(actor["player_id"])
        if not any(
            action["action_id"] == action_id
            for action in GuandanEngine.legal_actions(state, player_id)
        ):
            raise ValueError("action_id 不在规则核心当前发布的 legal_actions 中")

    def apply_action(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
    ) -> MoveResult:
        self.validate_action(state, move, actor)
        player_id = str(actor["player_id"])
        transition = GuandanEngine.apply_action(state, player_id, str(move["action_id"]))
        result = self.result_for(state, [])
        return MoveResult(
            state=state,
            next_player_id=state.get("turn_player_id"),
            note=str(transition["note"]),
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
            applied.public_event = {
                "guandan_delta": self._public_delta(applied.state)
            }
        return applied

    def validate_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> None:
        del state, move, mark
        raise ValueError("掼蛋需要 participant-aware action 接口")

    def apply_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> dict[str, Any]:
        del state, move, mark
        raise ValueError("掼蛋需要 participant-aware action 接口")

    def check_winner(self, state: dict[str, Any]) -> str | None:
        del state
        return None

    def result_for(
        self,
        state: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        del participants
        match = state.get("match_result")
        if not isinstance(match, dict):
            return None
        team = str(match["winner_team"])
        return {
            "draw": False,
            "winner_team": team,
            "winning_player_ids": list(match["winning_player_ids"]),
            "placements": list(match["placements"]),
            "team_levels": deepcopy(match["team_levels"]),
            "deal_count": int(match["deal_count"]),
            "result_text": f"{TEAM_LABELS.get(team, team)}赢得完整掼蛋升级赛",
        }

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
            raise ValueError("掼蛋筹码结算固定需要四名参与者")
        if result.get("draw"):
            return {player_id: 0 for player_id in player_ids}
        winners = result.get("winning_player_ids")
        if (
            not isinstance(winners, list)
            or len(winners) != 2
            or len(set(winners)) != 2
            or not set(winners).issubset(player_ids)
        ):
            raise ValueError("掼蛋终局必须提供两名有效获胜队员")
        winner_ids = set(winners)
        return {
            player_id: stake if player_id in winner_ids else -stake
            for player_id in player_ids
        }

    def public_state(
        self,
        state: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any]:
        del participants
        order = list(state.get("participant_order", []))
        level_rank = str(state.get("level_rank", "2"))
        trick = state.get("trick") or {}
        last_deal = state.get("deal_history", [])[-1:] or []
        phase = str(state.get("phase", "waiting"))
        return {
            "board_kind": "guandan",
            "engine": state.get("engine", "Choysang/rlcard-guandan"),
            "engine_version": state.get("engine_version", GuandanEngine.version),
            "phase": phase,
            "phase_label": PHASE_LABELS.get(phase, phase),
            "flow": {
                "phase": phase,
                "round_number": int(state.get("deal_number", 0)),
                "turn_number": int(state.get("turn_serial", 0)),
            },
            "deal_number": int(state.get("deal_number", 0)),
            "level_rank": level_rank,
            "team_levels": deepcopy(state.get("team_levels", {"A": "2", "B": "2"})),
            "teams": deepcopy(state.get("teams", {})),
            "hand_counts": {
                player_id: len(state.get("hands", {}).get(player_id, []))
                for player_id in order
            },
            "current_trick": {
                "number": trick.get("number"),
                "leader_player_id": trick.get("leader_player_id"),
                "last_play": self._public_play(trick.get("last_play"), level_rank),
                "pass_player_ids": list(trick.get("pass_player_ids", [])),
                "wind_follow": bool(trick.get("wind_follow", False)),
            },
            "finish_order": list(state.get("finish_order", [])),
            "tribute": self._public_tribute(state),
            "last_deal_results": deepcopy(last_deal),
            "match_result": deepcopy(state.get("match_result")),
            "last_public_delta": self._public_delta(state),
            "last_action_note": state.get("last_action_note", ""),
        }

    def private_state(
        self,
        state: dict[str, Any],
        viewer: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any]:
        del participants
        player_id = str(viewer["player_id"])
        level_rank = str(state.get("level_rank", "2"))
        hand = sorted(
            state.get("hands", {}).get(player_id, []),
            key=lambda card: card_sort_key(card, level_rank),
        )
        legal = GuandanEngine.legal_actions(state, player_id)
        return {
            "hand": [self._public_card(card, level_rank) for card in hand],
            "legal_actions": [self._compact_legal(action) for action in legal],
            "legal_action_count": len(legal),
        }

    def mcp_snapshot_state(
        self,
        public_state: dict[str, Any],
        viewer: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any]:
        del viewer, participants
        snapshot = deepcopy(public_state)
        snapshot.pop("last_public_delta", None)
        return snapshot

    def participant_summary(
        self,
        state: dict[str, Any],
        participant: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, str | int]:
        del participants
        player_id = str(participant["player_id"])
        team = str(state.get("teams", {}).get(player_id, "?"))
        finish_order = state.get("finish_order", [])
        trick = state.get("current_trick") or {}
        passed = player_id in trick.get("pass_player_ids", [])
        if player_id in finish_order:
            status = f"第 {finish_order.index(player_id) + 1} 游"
        elif passed:
            status = "已过"
        else:
            status = "在局"
        return {
            "team": TEAM_LABELS.get(team, team),
            "hand_count": int(state.get("hand_counts", {}).get(player_id, 0)),
            "level": str(state.get("team_levels", {}).get(team, "2")),
            "deal_status": status,
        }

    def npc_compact_rules(
        self,
        state: dict[str, Any],
        actor: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> str:
        del state, actor, participants
        return (
            "四人两副牌、对家同队的完整 2-to-A 掼蛋赛。牌型、压制、接风、贡还、"
            "抗贡与升级都由规则核心处理。查看 private_state 中 action_id 对应的简短"
            "牌组说明；决策时不得自行组合牌，只能选择 provider legal_actions 发布的"
            "一个 action_id。"
        )

    def npc_public_actions(
        self,
        state: dict[str, Any],
        actor: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        del actor, participants
        public = []
        current_level = str(state.get("level_rank", "2"))
        for item in state.get("action_history", [])[-24:]:
            projected = deepcopy(item)
            level_rank = str(projected.get("level_rank", current_level))
            if isinstance(projected.get("cards"), list):
                projected["cards"] = [
                    self._public_card(card, level_rank) for card in projected["cards"]
                ]
            if isinstance(projected.get("card"), dict):
                projected["card"] = self._public_card(projected["card"], level_rank)
            public.append(projected)
        return public

    def npc_legal_actions(
        self,
        state: dict[str, Any],
        actor: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        del participants
        return [
            {"action": "act", "action_id": str(action["action_id"])}
            for action in GuandanEngine.legal_actions(state, str(actor["player_id"]))
        ]

    def format_action(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
    ) -> str:
        action = next(
            (
                item for item in GuandanEngine.legal_actions(
                    state, str(actor["player_id"])
                )
                if item["action_id"] == move.get("action_id")
            ),
            None,
        )
        if action is None:
            return "无效 action_id"
        if action["kind"] == "pass":
            return "过"
        labels = "、".join(
            card_label(card)
            for card in state["hands"][str(actor["player_id"])]
            if card["id"] in action.get("card_ids", [])
        )
        return f"{action.get('pattern_label', action['kind'])}：{labels}"

    def format_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> str:
        del state, mark
        return str(move.get("action_id", "act"))
