from __future__ import annotations

import random
from copy import deepcopy
from typing import Any

from .base import GamePlugin, MoveResult
from .junqi_engine import (
    JunqiEngineError,
    engine_apply,
    engine_initial,
    engine_moves,
    engine_shuffle,
    engine_swap,
    engine_swaps,
)


class Junqi(GamePlugin):
    """Two-player dark Junqi backed by the pinned online-junqi engine."""

    game_type = "junqi"
    display_name = "军棋"
    category = "board"
    min_players = 2
    max_players = 2
    allowed_player_counts = (2,)
    recommended_players = 2
    supports_npcs = True
    uses_local_npc_strategy = True
    supports_stakes = True
    mcp_immediate_public_events = True

    rules_version = "online-junqi@f5ba2e8cedaa7e1dc3975349d5bbe097f2d5e13a"
    colors = ("b", "r")
    ranks = {
        0: "炸弹",
        1: "司令",
        2: "军长",
        3: "师长",
        4: "旅长",
        5: "团长",
        6: "营长",
        7: "连长",
        8: "排长",
        9: "工兵",
        10: "地雷",
        11: "军旗",
    }
    rules_text = (
        "【目标】\n"
        "夺取对方军旗；若一方轮到行动时没有任何可移动棋子，也判负。\n\n"
        "【布阵】\n"
        "双方各有 25 子：军旗 1、地雷 3、炸弹 2、工兵 3，以及司令至排长的标准数量。"
        "布阵时行营必须留空，军旗只能放在本方两个大本营之一，地雷只能放在最后两排，"
        "炸弹不能放在最前排。双方依次秘密调整自己的阵形，确认后由蓝方先行。\n\n"
        "【行动】\n"
        "公路线每次走相邻一站，行营与周围斜线相连；行营内的棋子不能被攻击。"
        "铁路上可沿同一直线走任意空站但不能越子，只有工兵能沿连通铁路转弯。"
        "军旗和地雷不能移动，大本营中的棋子不能再移动。\n\n"
        "【碰撞】\n"
        "司令、军长、师长、旅长、团长、营长、连长、排长、工兵依次由强到弱；"
        "同级同归于尽。炸弹与任何棋子同归于尽；工兵可排除地雷，其他棋子碰地雷阵亡；"
        "任何可移动棋子都可夺旗。司令阵亡后，其军旗位置公开。\n\n"
        "【暗棋信息】\n"
        "对手棋子身份始终隐藏；只有实际碰撞结果和司令阵亡后的军旗位置会公开。"
        "\n\n【CedarDuet 娱乐筹码】\n"
        "终局赢家获得一份房间底注，败者扣除一份房间底注，采用双人标准零和结算，不设棋子、回合或"
        "终局原因倍率；认输同样按赢家获得一份、认输者扣一份结算。"
    )
    move_format = (
        '布阵换位：{"move":{"action":"swap","from":"a1","to":"a2"}}；'
        '随机合法布阵：{"move":{"action":"shuffle"}}；'
        '确认布阵：{"move":{"action":"ready"}}；'
        '自动布阵并确认：{"move":{"action":"auto_setup"}}；'
        '走棋：{"move":{"action":"move","from":"a6","to":"a7"}}。'
    )

    def __init__(self, rng: random.Random | None = None) -> None:
        self._rng = rng or random.SystemRandom()

    @staticmethod
    def _engine(call, *args):
        try:
            return call(*args)
        except JunqiEngineError as exc:
            raise ValueError(str(exc)) from exc

    @staticmethod
    def _other_player_id(state: dict[str, Any], player_id: str) -> str:
        order = [str(item) for item in state.get("participant_order", [])]
        if len(order) != 2 or player_id not in order:
            raise ValueError("行动者不属于当前军棋对局")
        return order[1] if order[0] == player_id else order[0]

    @staticmethod
    def _square(value: Any) -> str:
        if not isinstance(value, str) or len(value) not in {2, 3}:
            raise ValueError("军棋坐标必须为 a1 到 e12")
        column, row_text = value[0], value[1:]
        if column not in "abcde" or not row_text.isdigit():
            raise ValueError("军棋坐标必须为 a1 到 e12")
        row = int(row_text)
        if not 1 <= row <= 12 or str(row) != row_text:
            raise ValueError("军棋坐标必须为 a1 到 e12")
        return value

    @classmethod
    def _piece_name(cls, piece: dict[str, Any] | None) -> str:
        if not piece:
            return "空位"
        return cls.ranks.get(int(piece["rank"]), "未知棋子")

    @staticmethod
    def _camp_name(color: str | None) -> str:
        return {"b": "蓝方", "r": "红方"}.get(color, "未分配")

    def _base_state(self) -> dict[str, Any]:
        initial = self._engine(engine_initial)
        return {
            "board_kind": "junqi",
            "rows": 12,
            "cols": 5,
            "board": initial["board"],
            "bunkers": initial["bunkers"],
            "headquarters": initial["headquarters"],
            "rail_lines": initial["rail_lines"],
            "participant_order": [],
            "color_by_player": {},
            "player_by_color": {},
            "active_player_id": None,
            "phase": "setup",
            "setup_ready": {},
            "commanders_alive": {"b": True, "r": True},
            "flags_captured": {"b": False, "r": False},
            "last_action": None,
            "last_battle": None,
            "last_setup_action": None,
            "public_actions": [],
            "winner_player_id": None,
            "terminal_reason": None,
            "rules_version": self.rules_version,
        }

    def initial_state(self) -> dict[str, Any]:
        return self._base_state()

    def initialize_for_first_player(
        self,
        participants: list[dict[str, Any]],
        first_player_id: str,
    ) -> dict[str, Any]:
        if len(participants) != 2:
            raise ValueError("军棋固定为双人对局")
        player_ids = [str(item["player_id"]) for item in participants]
        if first_player_id not in player_ids:
            raise ValueError("军棋先手不属于对局参与者")
        other_player_id = next(item for item in player_ids if item != first_player_id)
        state = self._base_state()
        state["participant_order"] = player_ids
        state["color_by_player"] = {
            first_player_id: "b",
            other_player_id: "r",
        }
        state["player_by_color"] = {"b": first_player_id, "r": other_player_id}
        state["active_player_id"] = first_player_id
        state["setup_ready"] = {player_id: False for player_id in player_ids}
        return state

    def _setup_actions(
        self, state: dict[str, Any], player_id: str
    ) -> list[dict[str, Any]]:
        if (
            state.get("phase") != "setup"
            or state.get("active_player_id") != player_id
            or state.get("setup_ready", {}).get(player_id)
        ):
            return []
        color = state["color_by_player"][player_id]
        swaps = self._engine(engine_swaps, state["board"], color)
        actions = [
            {"action": "swap", "from": item["from"], "to": item["to"]}
            for item in swaps
        ]
        actions.extend([
            {"action": "shuffle"},
            {"action": "ready"},
            {"action": "auto_setup"},
        ])
        return actions

    def _play_actions(
        self, state: dict[str, Any], player_id: str
    ) -> list[dict[str, str]]:
        if (
            state.get("phase") != "play"
            or state.get("active_player_id") != player_id
            or state.get("winner_player_id")
        ):
            return []
        color = state["color_by_player"][player_id]
        return [
            {"action": "move", "from": item["from"], "to": item["to"]}
            for item in self._engine(engine_moves, state["board"], color)
        ]

    def legal_actions_for(
        self, state: dict[str, Any], player_id: str
    ) -> list[dict[str, Any]]:
        if state.get("phase") == "setup":
            return self._setup_actions(state, player_id)
        return self._play_actions(state, player_id)

    def validate_action(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
    ) -> None:
        if not isinstance(move, dict):
            raise ValueError("move 必须是对象")
        player_id = str(actor["player_id"])
        if state.get("active_player_id") != player_id:
            raise ValueError("当前军棋行动者与房间行动者不一致")
        action = move.get("action")
        if action in {"shuffle", "ready", "auto_setup"}:
            if set(move) != {"action"}:
                raise ValueError(f"{action} 只接受 action 字段")
        elif action in {"swap", "move"}:
            if set(move) != {"action", "from", "to"}:
                raise ValueError(f"{action} 只接受 action、from、to 字段")
            start, end = self._square(move.get("from")), self._square(move.get("to"))
            if start == end:
                raise ValueError("起点和终点不能相同")
        else:
            raise ValueError("action 必须是 swap、shuffle、ready、auto_setup 或 move")
        if move not in self.legal_actions_for(state, player_id):
            raise ValueError("该行动不在第三方规则核心发布的权威合法行动中")

    def validate_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> None:
        del state, move, mark
        raise ValueError("军棋需要 participant-aware action 接口")

    def apply_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> dict[str, Any]:
        del state, move, mark
        raise ValueError("军棋需要 participant-aware action 接口")

    def _complete_setup(
        self, state: dict[str, Any], player_id: str
    ) -> tuple[str, bool]:
        state["setup_ready"][player_id] = True
        other_player_id = self._other_player_id(state, player_id)
        if not state["setup_ready"][other_player_id]:
            state["active_player_id"] = other_player_id
            return other_player_id, False
        first_player_id = state["player_by_color"]["b"]
        state["phase"] = "play"
        state["active_player_id"] = first_player_id
        state["last_setup_action"] = None
        return first_player_id, True

    def _setup_result(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        player_id: str,
    ) -> MoveResult:
        color = state["color_by_player"][player_id]
        action = move["action"]
        if action == "swap":
            state["board"] = self._engine(
                engine_swap, state["board"], color, move["from"], move["to"]
            )
            state["last_setup_action"] = {
                "action": "swap",
                "player_id": player_id,
                "from": move["from"],
                "to": move["to"],
            }
            return MoveResult(
                state,
                retain_turn=True,
                note="已按规则调整两枚己方棋子。",
                event_visible_to_player_ids=[player_id],
            )
        if action in {"shuffle", "auto_setup"}:
            state["board"] = self._engine(engine_shuffle, state["board"], color)
            state["last_setup_action"] = {
                "action": action,
                "player_id": player_id,
            }
            if action == "shuffle":
                return MoveResult(
                    state,
                    retain_turn=True,
                    note="第三方规则核心已生成一套合法随机布阵。",
                    event_visible_to_player_ids=[player_id],
                )
        next_player_id, started = self._complete_setup(state, player_id)
        note = (
            "双方布阵完成，蓝方先行。"
            if started else "一方布阵已锁定，轮到另一方布阵。"
        )
        return MoveResult(
            state,
            next_player_id=next_player_id,
            note=note,
            event_visible_to_player_ids=[player_id],
        )

    def _battle_view(self, move_result: dict[str, Any]) -> dict[str, Any] | None:
        defender = move_result.get("defender")
        if not isinstance(defender, dict):
            return None
        attacker = move_result.get("attacker")
        if not isinstance(attacker, dict):
            raise ValueError("规则核心碰撞结果缺少进攻方")
        return {
            "attacker_rank": int(attacker["rank"]),
            "attacker_name": self._piece_name(attacker),
            "defender_rank": int(defender["rank"]),
            "defender_name": self._piece_name(defender),
            "result": str(move_result["result_type"]),
        }

    def _play_result(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        player_id: str,
    ) -> MoveResult:
        color = state["color_by_player"][player_id]
        other_player_id = self._other_player_id(state, player_id)
        other_color = state["color_by_player"][other_player_id]
        engine = self._engine(
            engine_apply, state["board"], color, move["from"], move["to"]
        )
        state["board"] = engine["board"]
        state["commanders_alive"] = engine["commanders_alive"]
        state["flags_captured"] = engine["flags_captured"]
        authoritative = engine["move"]
        battle = self._battle_view(authoritative)
        public_action: dict[str, Any] = {
            "action": "move",
            "actor_player_id": player_id,
            "from": move["from"],
            "to": move["to"],
            "outcome": str(authoritative["result_type"]),
        }
        if battle is not None:
            public_action["battle"] = battle
        state["last_action"] = deepcopy(public_action)
        state["last_battle"] = deepcopy(battle)
        state["public_actions"] = [
            *state.get("public_actions", [])[-39:],
            deepcopy(public_action),
        ]

        winner_player_id: str | None = None
        terminal_reason: str | None = None
        if engine["flags_captured"][other_color]:
            winner_player_id = player_id
            terminal_reason = "flag_captured"
        elif int(engine["moves_remaining"][other_color]) == 0:
            winner_player_id = player_id
            terminal_reason = "immobilized"

        note = f"{move['from']}→{move['to']}。"
        if battle is not None:
            result_text = {
                "capture": "进攻方胜",
                "dies": "防守方胜",
                "equal": "双方同归于尽",
            }.get(battle["result"], "碰撞已裁决")
            note = (
                f"{battle['attacker_name']}碰撞{battle['defender_name']}："
                f"{result_text}。"
            )
        if winner_player_id:
            state["winner_player_id"] = winner_player_id
            state["terminal_reason"] = terminal_reason
            state["phase"] = "finished"
            state["active_player_id"] = None
            note += "夺取军旗，对局结束。" if terminal_reason == "flag_captured" else (
                "对方已无合法行动，对局结束。"
            )
        else:
            state["active_player_id"] = other_player_id

        delta = deepcopy(public_action)
        delta["commanders_alive"] = deepcopy(state["commanders_alive"])
        if winner_player_id:
            delta["winner_player_id"] = winner_player_id
            delta["terminal_reason"] = terminal_reason
        return MoveResult(
            state,
            next_player_id=other_player_id if not winner_player_id else None,
            note=note,
            result=(
                {"winner_player_id": winner_player_id, "draw": False}
                if winner_player_id else None
            ),
            public_event={"junqi_delta": delta},
        )

    def apply_action(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
    ) -> MoveResult:
        self.validate_action(state, move, actor)
        updated = deepcopy(state)
        player_id = str(actor["player_id"])
        if updated.get("phase") == "setup":
            return self._setup_result(updated, move, player_id)
        return self._play_result(updated, move, player_id)

    def check_winner(self, state: dict[str, Any]) -> str | None:
        winner = state.get("winner_player_id")
        if not winner:
            return None
        return state.get("marks_by_player", {}).get(winner)

    def result_for(
        self,
        state: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        del participants
        winner = state.get("winner_player_id")
        return {"winner_player_id": winner, "draw": False} if winner else None

    def public_state(
        self,
        state: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any]:
        del participants
        commanders_alive = state.get("commanders_alive", {"b": True, "r": True})
        projected_board: dict[str, Any] = {}
        remaining = {"b": 0, "r": 0}
        for square, piece in state["board"].items():
            if piece is None:
                projected_board[square] = None
                continue
            color = str(piece["color"])
            remaining[color] += 1
            projected = {"color": color}
            if int(piece["rank"]) == 11 and not commanders_alive.get(color, True):
                projected["rank"] = 11
            projected_board[square] = projected
        return {
            "board_kind": "junqi",
            "rows": 12,
            "cols": 5,
            "board": projected_board,
            "bunkers": deepcopy(state["bunkers"]),
            "headquarters": deepcopy(state["headquarters"]),
            "rail_lines": deepcopy(state["rail_lines"]),
            "participant_order": deepcopy(state.get("participant_order", [])),
            "color_by_player": deepcopy(state.get("color_by_player", {})),
            "active_player_id": state.get("active_player_id"),
            "phase": state.get("phase"),
            "setup_ready": deepcopy(state.get("setup_ready", {})),
            "commanders_alive": deepcopy(commanders_alive),
            "remaining_by_color": remaining,
            "last_action": deepcopy(state.get("last_action")),
            "last_battle": deepcopy(state.get("last_battle")),
            "public_actions": deepcopy(state.get("public_actions", [])),
            "winner_player_id": state.get("winner_player_id"),
            "terminal_reason": state.get("terminal_reason"),
            "rules_version": self.rules_version,
            "last_action_note": (
                "双方依次秘密布阵。"
                if state.get("phase") == "setup"
                else state.get("last_action_note", "")
            ),
        }

    def private_state(
        self,
        state: dict[str, Any],
        viewer: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any]:
        del participants
        player_id = str(viewer["player_id"])
        color = state.get("color_by_player", {}).get(player_id)
        if color not in self.colors:
            return {}
        pieces = {
            square: int(piece["rank"])
            for square, piece in state["board"].items()
            if isinstance(piece, dict) and piece.get("color") == color
        }
        return {
            "camp": color,
            "pieces": pieces,
            "legal_actions": self.legal_actions_for(state, player_id),
            "setup_locked": bool(state.get("setup_ready", {}).get(player_id)),
        }

    def mcp_snapshot_state(
        self,
        public_state: dict[str, Any],
        viewer: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any]:
        snapshot = super().mcp_snapshot_state(public_state, viewer, participants)
        snapshot.pop("public_actions", None)
        return snapshot

    def participant_summary(
        self,
        state: dict[str, Any],
        participant: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, str]:
        del participants
        player_id = str(participant["player_id"])
        color = state.get("color_by_player", {}).get(player_id)
        phase = state.get("phase")
        if phase == "setup":
            status = "已就绪" if state.get("setup_ready", {}).get(player_id) else "布阵中"
        elif state.get("winner_player_id") == player_id:
            status = "胜"
        elif phase == "finished":
            status = "负"
        else:
            status = "行动中" if state.get("active_player_id") == player_id else "等待"
        return {"camp": self._camp_name(color), "phase": status}

    def project_event(
        self,
        event: dict[str, Any],
        viewer: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        del participants
        move = event.get("move")
        if not isinstance(move, dict):
            return deepcopy(event)
        action = move.get("action")
        if action in {"swap", "shuffle", "ready", "auto_setup"}:
            sender = event.get("sender")
            sender_player_id = (
                sender.get("player_id") if isinstance(sender, dict)
                else event.get("sender_player_id")
            )
            if sender_player_id != viewer.get("player_id"):
                return None
        return deepcopy(event)

    def npc_compact_rules(
        self,
        state: dict[str, Any],
        actor: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> str:
        del participants
        color = state.get("color_by_player", {}).get(str(actor["player_id"]))
        return (
            f"标准双人暗棋陆战棋，你是{self._camp_name(color)}。"
            "布阵阶段系统 NPC 只能选择 auto_setup，由第三方核心生成合法阵形并确认；"
            "行棋阶段只能原样选择服务端第三方核心发布的 legal_actions。"
            "铁路直走、工兵转弯、行营保护、大本营锁定与碰撞均由规则核心裁决。"
        )

    def npc_public_actions(
        self,
        state: dict[str, Any],
        actor: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        del actor, participants
        return deepcopy(state.get("public_actions", [])[-20:])

    def npc_legal_actions(
        self,
        state: dict[str, Any],
        actor: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        del participants
        player_id = str(actor["player_id"])
        if state.get("phase") == "setup":
            legal = self._setup_actions(state, player_id)
            return [action for action in legal if action == {"action": "auto_setup"}]
        return self._play_actions(state, player_id)

    def choose_local_npc_action(
        self,
        state: dict[str, Any],
        actor: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        legal = self.npc_legal_actions(state, actor, participants)
        return deepcopy(self._rng.choice(legal)) if legal else None

    def format_action(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
    ) -> str:
        del actor
        action = move.get("action")
        if action == "swap":
            return f"秘密换位 {move.get('from', '?')}↔{move.get('to', '?')}"
        if action == "shuffle":
            return "秘密随机布阵"
        if action == "auto_setup":
            return "自动合法布阵并确认"
        if action == "ready":
            return "确认布阵"
        start, end = move.get("from", "?"), move.get("to", "?")
        target = state.get("board", {}).get(str(end))
        return f"{start}{'×' if target is not None else '→'}{end}"
