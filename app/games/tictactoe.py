from typing import Any

from .base import GamePlugin, move_coordinates


class TicTacToe(GamePlugin):
    supports_stakes = True
    game_type = "tictactoe"
    display_name = "井字棋"
    category = "board"
    rules_text = (
        "井字棋使用 3×3 棋盘。双方轮流在空格落下自己的记号；"
        "最先在横、竖或斜线上连成三个相同记号者获胜。棋盘填满且无人连成三子则和棋。"
    )
    move_format = (
        '落子参数使用零起始坐标：{"move":{"row":0,"col":0}}；'
        "row 自上而下为 0–2，col 自左而右为 0–2。"
    )

    def initial_state(self) -> dict[str, Any]:
        return {"size": 3, "board": [[None for _ in range(3)] for _ in range(3)]}

    def validate_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> None:
        row, col = move_coordinates(move, 3)
        if state["board"][row][col] is not None:
            raise ValueError("该位置已有棋子")

    def apply_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> dict[str, Any]:
        row, col = move_coordinates(move, 3)
        state["board"][row][col] = mark
        state["last_move"] = {"row": row, "col": col, "mark": mark}
        return state

    def check_winner(self, state: dict[str, Any]) -> str | None:
        board = state["board"]
        lines = list(board)
        lines.extend([[board[r][c] for r in range(3)] for c in range(3)])
        lines.append([board[i][i] for i in range(3)])
        lines.append([board[i][2 - i] for i in range(3)])
        for line in lines:
            if line[0] is not None and line.count(line[0]) == 3:
                return line[0]
        if all(cell is not None for row in board for cell in row):
            return "draw"
        return None
