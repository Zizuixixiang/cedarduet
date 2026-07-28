from typing import Any

from .base import GamePlugin, MoveResult


class DotsBoxes(GamePlugin):
    game_type = "dots_boxes"
    display_name = "点格棋"
    rules_text = (
        "点格棋使用 5×5 点阵，共 4×4 个格子。双方轮流连接相邻两点画一条未画过的横边或竖边；"
        "完成一个格子的第四条边便获得该格并得 1 分，且继续行动。全部 16 格归属后终局，"
        "得分较高者获胜，同分和棋。"
    )
    move_format = (
        '画边参数：{"move":{"orientation":"h","row":0,"col":0}}；'
        "orientation 为 h（横边，row 0–4、col 0–3）或 v（竖边，row 0–3、col 0–4）。"
    )

    def initial_state(self) -> dict[str, Any]:
        return {
            "size": 5,
            "rows": 5,
            "cols": 5,
            "board_kind": "dots_boxes",
            "horizontal_edges": [[None for _ in range(4)] for _ in range(5)],
            "vertical_edges": [[None for _ in range(5)] for _ in range(4)],
            "boxes": [[None for _ in range(4)] for _ in range(4)],
            "scores": {"X": 0, "O": 0},
        }

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
        orientation, row, col = self._edge(move)
        edges = (
            state["horizontal_edges"] if orientation == "h"
            else state["vertical_edges"]
        )
        if edges[row][col] is not None:
            raise ValueError("这条边已经画过")

    def apply_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> MoveResult:
        orientation, row, col = self._edge(move)
        edges = (
            state["horizontal_edges"] if orientation == "h"
            else state["vertical_edges"]
        )
        edges[row][col] = mark
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
                state["boxes"][box_row][box_col] = mark
                completed.append((box_row, box_col))
        state["scores"][mark] += len(completed)
        state["last_move"] = {
            "orientation": orientation,
            "row": row,
            "col": col,
            "mark": mark,
            "completed_boxes": len(completed),
        }
        note = ""
        if completed:
            note = f"本手完成 {len(completed)} 个格子并得分，行动权保留。"
        return MoveResult(state, retain_turn=bool(completed), note=note)

    def check_winner(self, state: dict[str, Any]) -> str | None:
        if any(box is None for row in state["boxes"] for box in row):
            return None
        x_score, o_score = state["scores"]["X"], state["scores"]["O"]
        if x_score == o_score:
            return "draw"
        return "X" if x_score > o_score else "O"

    def format_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> str:
        orientation, row, col = self._edge(move)
        start = f"{chr(ord('A') + col)}{row + 1}"
        end_col = col + (1 if orientation == "h" else 0)
        end_row = row + (1 if orientation == "v" else 0)
        end = f"{chr(ord('A') + end_col)}{end_row + 1}"
        return f"{start}–{end}"
