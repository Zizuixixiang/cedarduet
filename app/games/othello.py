from typing import Any

from .base import GamePlugin, MoveResult, move_coordinates


class Othello(GamePlugin):
    supports_stakes = True
    game_type = "othello"
    display_name = "黑白棋"
    category = "board"
    rules_text = (
        "【目标】\n"
        "终局时让己方棋子数量多于对方。\n\n"
        "【行动】\n"
        "黑白棋使用 8×8 棋盘，黑方先行。落子必须在至少一个方向夹住对方棋子，被夹住的棋子全部翻为己方。\n\n"
        "【特殊规则】\n"
        "若一方没有合法落点，系统会自动跳过，由另一方继续。\n\n"
        "【胜负】\n"
        "双方均无合法落点或棋盘填满时终局；棋子较多者获胜，数量相同则和棋。"
    )
    move_format = (
        '落子使用零起始坐标：{"move":{"row":2,"col":3}}；'
        "row、col 均为 0–7，且该位置必须能翻转至少一枚对方棋子。"
    )
    directions = (
        (-1, -1), (-1, 0), (-1, 1), (0, -1),
        (0, 1), (1, -1), (1, 0), (1, 1),
    )

    def initial_state(self) -> dict[str, Any]:
        board = [[None for _ in range(8)] for _ in range(8)]
        board[3][3], board[4][4] = "O", "O"
        board[3][4], board[4][3] = "X", "X"
        return {"size": 8, "rows": 8, "cols": 8, "board": board}

    @staticmethod
    def _opponent(mark: str) -> str:
        return "O" if mark == "X" else "X"

    def _flips(
        self, state: dict[str, Any], row: int, col: int, mark: str
    ) -> list[tuple[int, int]]:
        board = state["board"]
        if board[row][col] is not None:
            return []
        opponent = self._opponent(mark)
        result: list[tuple[int, int]] = []
        for dr, dc in self.directions:
            line: list[tuple[int, int]] = []
            r, c = row + dr, col + dc
            while 0 <= r < 8 and 0 <= c < 8 and board[r][c] == opponent:
                line.append((r, c))
                r += dr
                c += dc
            if line and 0 <= r < 8 and 0 <= c < 8 and board[r][c] == mark:
                result.extend(line)
        return result

    def _has_move(self, state: dict[str, Any], mark: str) -> bool:
        return any(
            self._flips(state, row, col, mark)
            for row in range(8)
            for col in range(8)
        )

    def validate_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> None:
        row, col = move_coordinates(move, 8)
        if state["board"][row][col] is not None:
            raise ValueError("该位置已有棋子")
        if not self._flips(state, row, col, mark):
            raise ValueError("该位置不能夹住对方棋子")

    def apply_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> MoveResult:
        row, col = move_coordinates(move, 8)
        flips = self._flips(state, row, col, mark)
        state["board"][row][col] = mark
        for flip_row, flip_col in flips:
            state["board"][flip_row][flip_col] = mark
        state["last_move"] = {
            "row": row, "col": col, "mark": mark, "flipped": len(flips)
        }
        state["scores"] = {
            side: sum(cell == side for line in state["board"] for cell in line)
            for side in ("X", "O")
        }
        opponent = self._opponent(mark)
        opponent_has_move = self._has_move(state, opponent)
        current_has_move = self._has_move(state, mark)
        retain_turn = not opponent_has_move and current_has_move
        note = ""
        if retain_turn:
            note = "对方当前无合法步，已自动跳过；本方继续行动。"
        elif not opponent_has_move and not current_has_move:
            note = "双方均无合法步，已数子结算。"
        return MoveResult(state, retain_turn=retain_turn, note=note)

    def check_winner(self, state: dict[str, Any]) -> str | None:
        board_full = all(cell is not None for row in state["board"] for cell in row)
        no_moves = not self._has_move(state, "X") and not self._has_move(state, "O")
        if not board_full and not no_moves:
            return None
        scores = {
            side: sum(cell == side for row in state["board"] for cell in row)
            for side in ("X", "O")
        }
        state["scores"] = scores
        if scores["X"] == scores["O"]:
            return "draw"
        return "X" if scores["X"] > scores["O"] else "O"
