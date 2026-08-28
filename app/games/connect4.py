from typing import Any

from .base import GamePlugin


class Connect4(GamePlugin):
    supports_stakes = True
    game_type = "connect4"
    display_name = "四子连珠"
    rules_text = (
        "四子连珠使用 7 列×6 行棋盘。双方轮流选择一列，棋子受重力落到该列最低空位；"
        "最先在横、竖或斜线方向形成连续四子者获胜。棋盘下满且无人四连则和棋。"
    )
    move_format = (
        '只需选择零起始列号：{"move":{"col":3}}；col 为 0–6，不传 row。'
    )

    def initial_state(self) -> dict[str, Any]:
        return {
            "size": 7,
            "rows": 6,
            "cols": 7,
            "board": [[None for _ in range(7)] for _ in range(6)],
        }

    @staticmethod
    def _column(move: dict[str, Any]) -> int:
        if set(move) != {"col"}:
            raise ValueError("四子连珠只接受 col 列号")
        col = move.get("col")
        if isinstance(col, bool) or not isinstance(col, int):
            raise ValueError("col 必须是整数")
        if not 0 <= col < 7:
            raise ValueError("列号越界：col 必须在 0 到 6 之间")
        return col

    def validate_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> None:
        col = self._column(move)
        if state["board"][0][col] is not None:
            raise ValueError("该列已经下满")

    def apply_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> dict[str, Any]:
        col = self._column(move)
        row = next(row for row in range(5, -1, -1) if state["board"][row][col] is None)
        state["board"][row][col] = mark
        state["last_move"] = {"row": row, "col": col, "mark": mark}
        return state

    def check_winner(self, state: dict[str, Any]) -> str | None:
        board = state["board"]
        for row in range(6):
            for col in range(7):
                mark = board[row][col]
                if mark is None:
                    continue
                for dr, dc in ((0, 1), (1, 0), (1, 1), (1, -1)):
                    end_row, end_col = row + 3 * dr, col + 3 * dc
                    if 0 <= end_row < 6 and 0 <= end_col < 7 and all(
                        board[row + step * dr][col + step * dc] == mark
                        for step in range(4)
                    ):
                        return mark
        if all(cell is not None for row in board for cell in row):
            return "draw"
        return None

    def format_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> str:
        return f"第 {self._column(move) + 1} 列"
