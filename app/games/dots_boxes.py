from copy import deepcopy
from typing import Any

from .base import GamePlugin, MoveResult


class DotsBoxes(GamePlugin):
    supports_stakes = True
    supports_multiplayer_stakes = True
    supports_npcs = True
    game_type = "dots_boxes"
    display_name = "点格棋"
    category = "board"
    min_players = 2
    max_players = 4
    allowed_player_counts = (2, 3, 4)
    recommended_players = 2
    rules_text = (
        "【目标】\n"
        "在全部格子画完时取得最高分。\n\n"
        "【行动】\n"
        "点格棋使用 5×5 点阵，共有 4×4 个格子，支持 2–4 人。参与者按座位顺序轮流连接相邻两点，画一条尚未占用的横边或竖边。\n\n"
        "【特殊规则】\n"
        "画出一个格子的第四条边，就获得该格并得 1 分，而且继续行动；一条边同时完成两个格子时，两格都计分。\n\n"
        "【胜负】\n"
        "全部 16 格归属后，唯一最高分者获胜；最高分并列则和局，本局筹码原样退还。多人筹码局中，唯一赢家获得其他每名参与者各自承担的一份本局筹码。"
    )
    move_format = (
        '画边参数：{"move":{"orientation":"h","row":0,"col":0},"revision":当前版本}；'
        "orientation 为 h（横边，row 0–4、col 0–3）或 v（竖边，row 0–3、col 0–4）。"
    )

    @staticmethod
    def _empty_board(scores: dict[str, int]) -> dict[str, Any]:
        return {
            "size": 5,
            "rows": 5,
            "cols": 5,
            "board_kind": "dots_boxes",
            "horizontal_edges": [[None for _ in range(4)] for _ in range(5)],
            "vertical_edges": [[None for _ in range(5)] for _ in range(4)],
            "boxes": [[None for _ in range(4)] for _ in range(4)],
            "scores": dict(scores),
            "action_history": [],
        }

    def initial_state(self) -> dict[str, Any]:
        """Keep the historical X/O shape for direct callers and old rooms."""
        return self._empty_board({"X": 0, "O": 0})

    def initialize(self, participants: list[dict[str, Any]]) -> dict[str, Any]:
        player_ids = [str(item["player_id"]) for item in participants]
        token_by_player = {
            str(item["player_id"]): str(item["token"]) for item in participants
        }
        state = self._empty_board({token: 0 for token in token_by_player.values()})
        state.update({
            "ownership_kind": "player_id",
            "participant_order": player_ids,
            "tokens_by_player": token_by_player,
            "scores_by_player": {player_id: 0 for player_id in player_ids},
        })
        return state

    @staticmethod
    def _edge(move: dict[str, Any]) -> tuple[str, int, int]:
        orientation = move.get("orientation")
        row, col = move.get("row"), move.get("col")
        if orientation not in {"h", "v"}:
            raise ValueError("orientation 必须是 h 或 v")
        if (
            isinstance(row, bool) or isinstance(col, bool)
            or not isinstance(row, int) or not isinstance(col, int)
        ):
            raise ValueError("row 和 col 必须是整数")
        max_row, max_col = (4, 3) if orientation == "h" else (3, 4)
        if not (0 <= row <= max_row and 0 <= col <= max_col):
            raise ValueError(
                f"{orientation} 边坐标越界：row 需为 0–{max_row}，col 需为 0–{max_col}"
            )
        return orientation, row, col

    @staticmethod
    def _box_complete(state: dict[str, Any], row: int, col: int) -> bool:
        return all((
            state["horizontal_edges"][row][col],
            state["horizontal_edges"][row + 1][col],
            state["vertical_edges"][row][col],
            state["vertical_edges"][row][col + 1],
        ))

    def validate_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> None:
        del mark
        orientation, row, col = self._edge(move)
        edges = (
            state["horizontal_edges"] if orientation == "h"
            else state["vertical_edges"]
        )
        if edges[row][col] is not None:
            raise ValueError("这条边已经画过")

    def _apply_owner(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        owner: str,
        *,
        token: str | None = None,
    ) -> MoveResult:
        orientation, row, col = self._edge(move)
        edges = (
            state["horizontal_edges"] if orientation == "h"
            else state["vertical_edges"]
        )
        edges[row][col] = owner
        candidates = (
            ((row - 1, col), (row, col))
            if orientation == "h"
            else ((row, col - 1), (row, col))
        )
        completed: list[tuple[int, int]] = []
        for box_row, box_col in candidates:
            if (
                0 <= box_row < 4
                and 0 <= box_col < 4
                and state["boxes"][box_row][box_col] is None
                and self._box_complete(state, box_row, box_col)
            ):
                state["boxes"][box_row][box_col] = owner
                completed.append((box_row, box_col))
        if "scores_by_player" in state:
            state["scores_by_player"][owner] += len(completed)
            score_token = token or state.get("tokens_by_player", {}).get(owner)
            if score_token is not None:
                state.setdefault("scores", {}).setdefault(score_token, 0)
                state["scores"][score_token] += len(completed)
        else:
            state.setdefault("scores", {}).setdefault(owner, 0)
            state["scores"][owner] += len(completed)
        last_move = {
            "orientation": orientation,
            "row": row,
            "col": col,
            "owner": owner,
            "mark": token or owner,
            "completed_boxes": len(completed),
        }
        state["last_move"] = last_move
        state.setdefault("action_history", []).append(deepcopy(last_move))
        note = ""
        if completed:
            note = f"本手完成 {len(completed)} 个格子并得分，行动权保留。"
        return MoveResult(state, retain_turn=bool(completed), note=note)

    def apply_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> MoveResult:
        return self._apply_owner(state, move, mark, token=mark)

    def validate_action(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
    ) -> None:
        self.validate_move(state, move, str(actor["token"]))

    def apply_action(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
    ) -> MoveResult:
        # Persisted legacy rooms have only token-keyed scores. New rooms use IDs.
        owner = (
            str(actor["player_id"])
            if "scores_by_player" in state
            else str(actor["token"])
        )
        return self._apply_owner(state, move, owner, token=str(actor["token"]))

    @staticmethod
    def _board_full(state: dict[str, Any]) -> bool:
        return not any(box is None for row in state["boxes"] for box in row)

    def check_winner(self, state: dict[str, Any]) -> str | None:
        if not self._board_full(state):
            return None
        scores = state.get("scores", {})
        if not scores:
            return "draw"
        best = max(scores.values())
        leaders = [token for token, score in scores.items() if score == best]
        return leaders[0] if len(leaders) == 1 else "draw"

    def result_for(
        self,
        state: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        if "scores_by_player" not in state:
            return super().result_for(state, participants)
        if not self._board_full(state):
            return None
        scores = state["scores_by_player"]
        best = max(scores.values())
        leaders = [player_id for player_id, score in scores.items() if score == best]
        result: dict[str, Any] = {
            "draw": len(leaders) != 1,
            "scores": dict(scores),
        }
        if len(leaders) == 1:
            result["winner_player_id"] = leaders[0]
        else:
            result["tied_player_ids"] = leaders
        return result

    def settlement_deltas(
        self,
        state: dict[str, Any],
        result: dict[str, Any],
        participants: list[dict[str, Any]],
        stake: int,
    ) -> dict[str, int]:
        del state
        player_ids = [str(item["player_id"]) for item in participants]
        if result.get("draw"):
            return {player_id: 0 for player_id in player_ids}
        winner = result.get("winner_player_id")
        if winner not in player_ids:
            raise ValueError("点格棋终局缺少有效唯一赢家")
        return {
            player_id: stake * (len(player_ids) - 1)
            if player_id == winner else -stake
            for player_id in player_ids
        }

    def public_state(
        self,
        state: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any]:
        projected = deepcopy(state)
        if state.get("ownership_kind") != "player_id" or len(participants) != 2:
            return projected
        # Existing two-player Web/MCP clients still see X/O ownership values;
        # persistence and all multiplayer rooms remain player_id-authoritative.
        token_by_player = {
            item["player_id"]: item["token"] for item in participants
        }

        def legacy_owner(owner: str | None) -> str | None:
            return token_by_player.get(owner, owner)

        for key in ("horizontal_edges", "vertical_edges", "boxes"):
            projected[key] = [
                [legacy_owner(owner) for owner in row] for row in state[key]
            ]
        if projected.get("last_move"):
            projected["last_move"]["owner"] = legacy_owner(
                projected["last_move"].get("owner")
            )
        for action in projected.get("action_history", []):
            action["owner"] = legacy_owner(action.get("owner"))
        return projected

    def participant_summary(
        self,
        state: dict[str, Any],
        participant: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, int]:
        del participants
        player_id = str(participant["player_id"])
        if "scores_by_player" in state:
            score = state["scores_by_player"].get(player_id, 0)
        else:
            score = state.get("scores", {}).get(participant.get("token"), 0)
        return {"score": int(score)}

    def npc_compact_rules(
        self,
        state: dict[str, Any],
        actor: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> str:
        del state, actor, participants
        return (
            "从权威列表选择一条未画边。完成格子得 1 分并继续行动；"
            "16 格填满后唯一最高分获胜，并列和局。"
        )

    def npc_public_actions(
        self,
        state: dict[str, Any],
        actor: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        del actor, participants
        return deepcopy(state.get("action_history", [])[-20:])

    def npc_legal_actions(
        self,
        state: dict[str, Any],
        actor: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        del actor, participants
        actions: list[dict[str, Any]] = []
        for row, edges in enumerate(state["horizontal_edges"]):
            actions.extend(
                {"orientation": "h", "row": row, "col": col}
                for col, owner in enumerate(edges) if owner is None
            )
        for row, edges in enumerate(state["vertical_edges"]):
            actions.extend(
                {"orientation": "v", "row": row, "col": col}
                for col, owner in enumerate(edges) if owner is None
            )
        return actions

    def format_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> str:
        del state, mark
        orientation, row, col = self._edge(move)
        start = f"{chr(ord('A') + col)}{row + 1}"
        end_col = col + (1 if orientation == "h" else 0)
        end_row = row + (1 if orientation == "v" else 0)
        end = f"{chr(ord('A') + end_col)}{end_row + 1}"
        return f"{start}–{end}"
