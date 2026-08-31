from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from .base import GamePlugin, MoveResult
from .go_engine import (
    GoEngineError,
    engine_apply,
    engine_state,
    engine_toggle_dead,
)


class Go(GamePlugin):
    game_type = "go"
    display_name = "围棋"
    category = "board"
    min_players = 2
    max_players = 2
    allowed_player_counts = (2,)
    recommended_players = 2
    supports_npcs = True
    uses_local_npc_strategy = True
    supports_stakes = True
    mcp_immediate_public_events = True

    RULE_VERSION = "go-chinese-19x19-area-psk-v1"
    BOARD_SIZE = 19
    KOMI = 7.5
    KO_RULE = "positional-superko"
    SCORING = "area"
    COLORS = ("black", "white")
    rules_text = (
        "【固定规则版本】\n"
        "go-chinese-19x19-area-psk-v1：双人 19×19 中国规则，黑先，白贴 7.5 目，"
        "采用面积计分（棋盘活子数 + 所围空点数），禁止自杀。\n\n"
        "【行动】\n"
        "行棋阶段每回合从服务端列出的合法行动中选择一个交叉点落子，"
        "或选择 pass。提子、劫与超级劫均由服务端 Tenuki 规则核心判定。\n\n"
        "【超级劫】\n"
        "固定采用 positional superko（位置超级劫）：若一次落子后的黑白棋子分布与本局此前"
        "任一落子后局面完全相同，该落子非法；行棋方身份不参与比较。pass 本身不受位置重复"
        "限制，也不会清除更早局面的超级劫历史。\n\n"
        "【终局与死子确认】\n"
        "连续两次 pass 后进入死子确认阶段，不会立即结算。轮到的一方可以切换 Tenuki 选定的"
        "整组死子，任何修改都会清空双方此前确认；只有双方先后确认完全相同的当前死子集合，"
        "系统才按面积分结算并给白方加 7.5 目。任一方不能单独判死。\n\n"
        "【胜负与 CedarDuet 娱乐筹码】\n"
        "双方确认后总分较高者获胜；也可随时使用房间的认输操作。终局赢家获得一份房间底注、败者"
        "扣除一份房间底注，认输也采用相同的双人标准结算；若面积分相同判和，则双方筹码变化均为 0。"
    )
    move_format = (
        '行棋：{"move":{"action":"play","row":3,"col":3},"revision":当前版本}；'
        '停一手：{"move":{"action":"pass"},"revision":当前版本}；'
        '死子阶段：{"move":{"action":"toggle_dead","row":3,"col":3},"revision":当前版本} '
        '或 {"move":{"action":"confirm_score"},"revision":当前版本}。row、col 为 0–18；'
        "只能原样选择服务端 authoritative legal_actions 中的动作。"
    )

    @staticmethod
    def tokens_for(participants: list[dict[str, Any]]) -> list[str]:
        if len(participants) != 2:
            raise ValueError("围棋固定需要 2 名参与者")
        return ["black", "white"]

    def first_player_id(
        self, participants: list[dict[str, Any]], mode: str
    ) -> str:
        return super().first_player_id(participants, mode)

    def initialize_for_first_player(
        self,
        participants: list[dict[str, Any]],
        first_player_id: str,
    ) -> dict[str, Any]:
        opener = next(
            (
                item for item in participants
                if item.get("player_id") == first_player_id
            ),
            None,
        )
        black = next(
            (item for item in participants if item.get("token") == "black"),
            None,
        )
        if opener is None or black is None:
            raise ValueError("围棋缺少指定先手或黑方")
        if opener is not black:
            opener["token"], black["token"] = black["token"], opener["token"]
        return self.initialize(participants)

    @staticmethod
    def _state_from_engine(
        engine: dict[str, Any],
        *,
        history: list[dict[str, Any]],
        previous: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        state = deepcopy(previous or {})
        state.update({
            "rule_version": Go.RULE_VERSION,
            "size": Go.BOARD_SIZE,
            "rows": Go.BOARD_SIZE,
            "cols": Go.BOARD_SIZE,
            "board_kind": "go",
            "komi": Go.KOMI,
            "scoring_rule": Go.SCORING,
            "ko_rule": Go.KO_RULE,
            "suicide_allowed": False,
            "board": deepcopy(engine["board"]),
            "to_play": engine["to_play"],
            "move_number": engine["move_number"],
            "consecutive_passes": engine["consecutive_passes"],
            "captures": deepcopy(engine["captures"]),
            "ko_point": deepcopy(engine.get("ko_point")),
            "last_move": deepcopy(engine.get("last_move")),
            "engine_history": deepcopy(history),
            "position_history_count": engine["position_history_count"],
            "position_identity": engine["position_identity"],
            "dead_stones": deepcopy(engine.get("dead_stones", [])),
            "score_preview": deepcopy(engine.get("score")),
            "territory": deepcopy(engine.get("territory")),
            "legal_actions": deepcopy(engine.get("legal_actions", [])),
            "phase": "scoring" if engine.get("is_over") else "play",
        })
        if state["phase"] == "play":
            state["dead_stones"] = []
            state["score_preview"] = None
            state["territory"] = None
            state["scoring_confirmations"] = {}
        else:
            state.setdefault("scoring_confirmations", {})
        return state

    def initial_state(self) -> dict[str, Any]:
        try:
            engine = engine_state([], [])
        except GoEngineError as exc:
            raise ValueError(str(exc)) from exc
        return self._state_from_engine(engine, history=[])

    def initialize(self, participants: list[dict[str, Any]]) -> dict[str, Any]:
        if len(participants) != 2:
            raise ValueError("围棋固定需要 2 名参与者")
        return self.initial_state()

    def prepare_opening_state(
        self,
        state: dict[str, Any],
        first_player_id: str,
        participants: list[dict[str, Any]],
    ) -> dict[str, Any]:
        players_by_color = {
            str(item["token"]): str(item["player_id"])
            for item in participants
        }
        if players_by_color.get("black") != first_player_id:
            raise ValueError("围棋必须由黑方先行")
        state["players_by_color"] = players_by_color
        return state

    @staticmethod
    def _dead_signature(state: dict[str, Any]) -> str:
        points = sorted(
            (
                {"row": int(point["row"]), "col": int(point["col"])}
                for point in state.get("dead_stones", [])
            ),
            key=lambda point: (point["row"], point["col"]),
        )
        return json.dumps(points, separators=(",", ":"))

    @classmethod
    def _scoring_actions(cls, state: dict[str, Any]) -> list[dict[str, Any]]:
        actions = [
            {"action": "toggle_dead", "row": row, "col": col}
            for row, line in enumerate(state["board"])
            for col, value in enumerate(line)
            if value in cls.COLORS
        ]
        actions.append({"action": "confirm_score"})
        return actions

    def _refresh_legal_actions(self, state: dict[str, Any]) -> None:
        if state["phase"] == "scoring":
            state["legal_actions"] = self._scoring_actions(state)
        elif state["phase"] == "finished":
            state["legal_actions"] = []

    def public_state(
        self,
        state: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any]:
        projected = deepcopy(state)
        projected.pop("engine_history", None)
        projected.pop("position_identity", None)
        confirmations = projected.pop("scoring_confirmations", {})
        signature = self._dead_signature(state)
        projected["confirmed_player_ids"] = sorted(
            player_id
            for player_id, confirmed_signature in confirmations.items()
            if confirmed_signature == signature
        )
        projected["required_confirmation_count"] = len(participants)
        return projected

    def private_state(
        self,
        state: dict[str, Any],
        viewer: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any]:
        del participants
        if (
            state.get("phase") in {"play", "scoring"}
            and viewer.get("token") == state.get("to_play")
        ):
            return {"legal_actions": deepcopy(state.get("legal_actions", []))}
        return {}

    def mcp_snapshot_state(
        self,
        public_state: dict[str, Any],
        viewer: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any]:
        snapshot = super().mcp_snapshot_state(
            public_state, viewer, participants
        )
        snapshot.pop("position_identity", None)
        # The same submit-ready actions are already in current actor private_state.
        snapshot.pop("legal_actions", None)
        return snapshot

    def mcp_bootstrap_state(
        self,
        public_state: dict[str, Any],
        viewer: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any]:
        snapshot = super().mcp_bootstrap_state(
            public_state, viewer, participants
        )
        # The browser uses this list to make 361 intersections clickable.  MCP
        # receives the same authority in the compact row-range spec below.
        snapshot.pop("legal_actions", None)
        return snapshot

    @staticmethod
    def _column_ranges(columns: list[int]) -> str:
        if not columns:
            return ""
        parts: list[str] = []
        start = previous = columns[0]
        for column in columns[1:]:
            if column == previous + 1:
                previous = column
                continue
            parts.append(str(start) if start == previous else f"{start}-{previous}")
            start = previous = column
        parts.append(str(start) if start == previous else f"{start}-{previous}")
        return ",".join(parts)

    def mcp_private_state(
        self,
        private_state: dict[str, Any],
        viewer: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any]:
        del viewer, participants
        actions = private_state.get("legal_actions")
        if not isinstance(actions, list):
            return deepcopy(private_state)
        coordinates = [
            action for action in actions
            if action.get("action") in {"play", "toggle_dead"}
        ]
        standalone = [
            deepcopy(action) for action in actions if action not in coordinates
        ]
        if not coordinates:
            return {"legal_actions": standalone}
        coordinate_action = str(coordinates[0]["action"])
        columns_by_row = [
            self._column_ranges(sorted(
                int(action["col"])
                for action in coordinates
                if action.get("action") == coordinate_action
                and int(action["row"]) == row
            ))
            for row in range(self.BOARD_SIZE)
        ]
        return {
            "legal_actions": standalone,
            "legal_action_spec": {
                "format": "coordinate_rows_v1",
                "action": coordinate_action,
                "columns_by_row": columns_by_row,
                "submit": {"action": coordinate_action, "row": "row index", "col": "listed column"},
            },
        }

    def participant_summary(
        self,
        state: dict[str, Any],
        participant: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, str | int | bool | None]:
        del participants
        color = str(participant.get("token") or "")
        return {
            "color": color,
            "captures": int(state.get("captures", {}).get(color, 0)),
            "score_confirmed": participant.get("player_id")
            in state.get("confirmed_player_ids", []),
        }

    @staticmethod
    def _checked_move(move: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(move, dict):
            raise ValueError("move 必须是对象")
        action = move.get("action")
        if action in {"pass", "confirm_score"}:
            if move != {"action": action}:
                raise ValueError(f"{action} 动作不能包含额外字段")
            return {"action": action}
        if action not in {"play", "toggle_dead"}:
            raise ValueError("未知围棋动作")
        row, col = move.get("row"), move.get("col")
        if (
            isinstance(row, bool)
            or isinstance(col, bool)
            or not isinstance(row, int)
            or not isinstance(col, int)
        ):
            raise ValueError("row 和 col 必须是整数")
        if not (0 <= row < Go.BOARD_SIZE and 0 <= col < Go.BOARD_SIZE):
            raise ValueError("坐标越界：row 和 col 均为 0–18")
        if set(move) != {"action", "row", "col"}:
            raise ValueError(f"{action} 动作只能包含 action、row、col")
        return {"action": action, "row": row, "col": col}

    def validate_action(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
    ) -> None:
        checked = self._checked_move(move)
        if actor.get("token") != state.get("to_play"):
            raise ValueError("当前行动者颜色与 Tenuki 行棋方不一致")
        phase = state.get("phase")
        if phase == "play" and checked["action"] not in {"play", "pass"}:
            raise ValueError("行棋阶段只能落子或 pass")
        if phase == "scoring" and checked["action"] not in {
            "toggle_dead", "confirm_score",
        }:
            raise ValueError("死子确认阶段只能切换死子或确认计分")
        if phase not in {"play", "scoring"}:
            raise ValueError("对局已经结束")
        if checked not in state.get("legal_actions", []):
            raise ValueError("该动作不在服务端 authoritative legal_actions 中")

    def apply_action(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
    ) -> MoveResult:
        self.validate_action(state, move, actor)
        checked = self._checked_move(move)
        if checked["action"] in {"play", "pass"}:
            try:
                engine, applied, history = engine_apply(
                    state.get("engine_history", []), checked
                )
            except GoEngineError as exc:
                raise ValueError(str(exc)) from exc
            updated = self._state_from_engine(
                engine, history=history, previous=state
            )
            self._refresh_legal_actions(updated)
            updated["last_delta"] = {
                "kind": applied["action"],
                **(
                    {
                        "placed": {
                            "row": applied["row"],
                            "col": applied["col"],
                            "color": applied["color"],
                        },
                        "captured": deepcopy(applied.get("captured", [])),
                    }
                    if applied["action"] == "play"
                    else {"color": applied["color"]}
                ),
                "to_play": updated["to_play"],
                "phase": updated["phase"],
                "captures": deepcopy(updated["captures"]),
            }
            public_delta: dict[str, Any] = {}
            if applied["action"] == "play" and applied.get("captured"):
                public_delta["captured"] = deepcopy(applied["captured"])
            if updated["phase"] != state.get("phase"):
                public_delta["phase"] = updated["phase"]
            note = (
                "双方已连续 pass，进入死子双方确认阶段。"
                if updated["phase"] == "scoring"
                else (
                    f"提取 {len(applied.get('captured', []))} 子。"
                    if applied.get("captured") else ""
                )
            )
            return MoveResult(
                updated,
                note=note,
                public_event=(
                    {"go_delta": public_delta} if public_delta else None
                ),
            )

        if checked["action"] == "toggle_dead":
            try:
                engine, added, removed = engine_toggle_dead(
                    state.get("engine_history", []),
                    state.get("dead_stones", []),
                    checked["row"],
                    checked["col"],
                )
            except GoEngineError as exc:
                raise ValueError(str(exc)) from exc
            updated = self._state_from_engine(
                engine,
                history=state.get("engine_history", []),
                previous=state,
            )
            updated["to_play"] = (
                "white" if actor.get("token") == "black" else "black"
            )
            updated["scoring_confirmations"] = {}
            self._refresh_legal_actions(updated)
            updated["last_delta"] = {
                "kind": "toggle_dead",
                "dead_added": added,
                "dead_removed": removed,
                "score_preview": deepcopy(updated["score_preview"]),
            }
            public_delta = {
                "dead_added": added,
                "dead_removed": removed,
                "score_preview": deepcopy(updated["score_preview"]),
            }
            return MoveResult(
                updated,
                note="死子集合已修改，双方需要重新确认。",
                public_event={"go_delta": public_delta},
            )

        updated = deepcopy(state)
        signature = self._dead_signature(updated)
        confirmations = updated.setdefault("scoring_confirmations", {})
        confirmations[str(actor["player_id"])] = signature
        updated["to_play"] = (
            "white" if actor.get("token") == "black" else "black"
        )
        participant_ids = [str(item) for item in updated["players_by_color"].values()]
        settled = all(confirmations.get(player_id) == signature for player_id in participant_ids)
        last_delta: dict[str, Any] = {
            "kind": "confirm_score",
            "player_id": str(actor["player_id"]),
            "confirmed_player_ids": sorted(
                player_id
                for player_id in participant_ids
                if confirmations.get(player_id) == signature
            ),
            "settled": settled,
        }
        delta: dict[str, Any] = {}
        result = None
        note = "已确认当前死子集合，等待对方确认。"
        if settled:
            try:
                authoritative = engine_state(
                    updated.get("engine_history", []),
                    updated.get("dead_stones", []),
                )
            except GoEngineError as exc:
                raise ValueError(str(exc)) from exc
            score = deepcopy(authoritative.get("score"))
            if not isinstance(score, dict):
                raise ValueError("Tenuki 未返回有效面积分")
            updated["score_preview"] = deepcopy(score)
            updated["territory"] = deepcopy(authoritative.get("territory"))
            black_score = float(score["black"])
            white_score = float(score["white"])
            winner_color = (
                "black" if black_score > white_score
                else "white" if white_score > black_score
                else None
            )
            winner_player_id = (
                updated["players_by_color"][winner_color]
                if winner_color else None
            )
            result = {
                "winner_player_id": winner_player_id,
                "draw": winner_player_id is None,
                "scores": {"black": black_score, "white": white_score},
                "winner_color": winner_color,
                "komi": self.KOMI,
                "scoring": self.SCORING,
                "ko_rule": self.KO_RULE,
                "dead_stones": deepcopy(updated.get("dead_stones", [])),
            }
            updated["phase"] = "finished"
            updated["legal_actions"] = []
            updated["final_score"] = deepcopy(result["scores"])
            updated["winner_color"] = winner_color
            updated["game_result"] = deepcopy(result)
            delta["score"] = deepcopy(result["scores"])
            delta["winner_color"] = winner_color
            last_delta.update(deepcopy(delta))
            note = (
                f"双方确认完成：黑 {black_score:g}，白 {white_score:g}（含贴目），"
                f"{('黑方' if winner_color == 'black' else '白方') if winner_color else '双方'}"
                f"{'获胜' if winner_color else '和棋'}。"
            )
        updated["last_delta"] = last_delta
        return MoveResult(
            updated,
            note=note,
            result=result,
            public_event={"go_delta": delta} if delta else None,
        )

    def validate_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> None:
        self.validate_action(state, move, {"token": mark})

    def apply_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> MoveResult:
        player_id = state.get("players_by_color", {}).get(mark, mark)
        return self.apply_action(
            state, move, {"token": mark, "player_id": player_id}
        )

    def check_winner(self, state: dict[str, Any]) -> str | None:
        result = state.get("game_result")
        if not isinstance(result, dict):
            return None
        if result.get("draw"):
            return "draw"
        winner_color = result.get("winner_color")
        return winner_color if winner_color in self.COLORS else None

    def result_for(
        self,
        state: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        del participants
        result = state.get("game_result")
        return deepcopy(result) if isinstance(result, dict) else None

    def npc_compact_rules(
        self,
        state: dict[str, Any],
        actor: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> str:
        del state, actor, participants
        return (
            "19×19 中国面积规则，白贴 7.5，位置超级劫且禁自杀。必须逐字选择"
            " authoritative legal_actions 中的动作；行棋阶段优先落子而非 pass；"
            "死子阶段不确定时选择 confirm_score，不得构造列表外坐标。"
        )

    def npc_public_actions(
        self,
        state: dict[str, Any],
        actor: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        del actor, participants
        return deepcopy(state.get("engine_history", [])[-40:])

    def npc_legal_actions(
        self,
        state: dict[str, Any],
        actor: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        del participants
        if actor.get("token") != state.get("to_play"):
            return []
        return deepcopy(state.get("legal_actions", []))

    def choose_local_npc_action(
        self,
        state: dict[str, Any],
        actor: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        actions = self.npc_legal_actions(state, actor, participants)
        if state.get("phase") == "scoring":
            return next(
                (item for item in actions if item == {"action": "confirm_score"}),
                None,
            )
        plays = [item for item in actions if item.get("action") == "play"]
        if plays:
            return min(
                plays,
                key=lambda item: (
                    abs(item["row"] - 9) + abs(item["col"] - 9),
                    item["row"],
                    item["col"],
                ),
            )
        return next(
            (item for item in actions if item == {"action": "pass"}), None
        )

    @staticmethod
    def _coordinate(row: int, col: int) -> str:
        letters = "ABCDEFGHJKLMNOPQRST"
        return f"{letters[col]}{Go.BOARD_SIZE - row}"

    def format_action(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
    ) -> str:
        del actor
        checked = self._checked_move(move)
        if checked["action"] == "pass":
            return "Pass"
        if checked["action"] == "confirm_score":
            return "确认当前死子与计分"
        coordinate = self._coordinate(checked["row"], checked["col"])
        return (
            coordinate
            if checked["action"] == "play"
            else f"切换死子 {coordinate}"
        )
