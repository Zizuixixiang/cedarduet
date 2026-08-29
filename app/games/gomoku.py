from typing import Any

from .base import GamePlugin, move_coordinates


class Gomoku(GamePlugin):
    supports_stakes = True
    game_type = "gomoku"
    display_name = "五子棋"
    category = "board"
    rules_text = (
        "五子棋使用 15×15 棋盘，双方轮流在交叉点落子。"
        "本规则不设禁手；任一方在横、竖或任一斜线方向形成连续五子或更多即获胜。"
        "棋盘填满且无人达成五连则和棋。"
    )
    move_format = (
        '落子参数使用零起始坐标：{"move":{"row":7,"col":7}}；'
        "row 自上而下为 0–14，col 自左而右为 0–14。"
    )

    def initial_state(self) -> dict[str, Any]:
        return {
            "size": 15,
            "board": [[None for _ in range(15)] for _ in range(15)],
        }

    def validate_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> None:
        row, col = move_coordinates(move, 15)
        if state["board"][row][col] is not None:
            raise ValueError("该位置已有棋子")

    def apply_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> dict[str, Any]:
        row, col = move_coordinates(move, 15)
        state["board"][row][col] = mark
        state["last_move"] = {"row": row, "col": col, "mark": mark}
        return state

    def check_winner(self, state: dict[str, Any]) -> str | None:
        board = state["board"]
        size = 15
        for row in range(size):
            for col in range(size):
                mark = board[row][col]
                if mark is None:
                    continue
                for dr, dc in ((0, 1), (1, 0), (1, 1), (1, -1)):
                    end_row = row + 4 * dr
                    end_col = col + 4 * dc
                    if not (0 <= end_row < size and 0 <= end_col < size):
                        continue
                    if all(board[row + i * dr][col + i * dc] == mark for i in range(5)):
                        return mark
        if all(cell is not None for row in board for cell in row):
            return "draw"
        return None
