from typing import Any

from .base import GamePlugin, MoveResult


class Jungle(GamePlugin):
    supports_stakes = True
    game_type = "jungle"
    display_name = "斗兽棋"
    category = "board"
    rules_text = (
        "【目标】\n"
        "让己方任意一只兽先进入对方兽穴。\n\n"
        "【行动】\n"
        "斗兽棋使用 7×9 棋盘。棋子通常每次上下左右走一格；兽力由低到高为鼠、猫、狗、狼、豹、虎、狮、象，高阶可吃同阶或低阶。不得进入己方兽穴。\n\n"
        "【特殊规则】\n"
        "- 鼠可吃象，象不可吃鼠。鼠可以入河，水中鼠可互吃，但水中鼠不能直接吃岸上象。\n"
        "- 狮、虎可以横向或纵向跳过整条河；路径上有任意一只水中鼠时不能跳。\n"
        "- 进入对方陷阱的兽可被任意敌兽吃。\n\n"
        "【胜负】\n"
        "先进入对方兽穴者获胜；一方无棋子或无任何合法着法时判负。"
    )
    move_format = (
        '移动使用零起始起终点：{"move":{"from_row":6,"from_col":0,'
        '"to_row":5,"to_col":0}}；row 为 0–8，col 为 0–6。'
    )
    ranks = {"R": 1, "C": 2, "D": 3, "W": 4, "P": 5, "T": 6, "L": 7, "E": 8}
    names = {
        "R": "鼠", "C": "猫", "D": "狗", "W": "狼",
        "P": "豹", "T": "虎", "L": "狮", "E": "象",
    }
    water = {
        (row, col)
        for row in (3, 4, 5)
        for col in (1, 2, 4, 5)
    }
    dens = {"O": (0, 3), "X": (8, 3)}
    traps = {
        "O": {(0, 2), (0, 4), (1, 3)},
        "X": {(8, 2), (8, 4), (7, 3)},
    }

    def initial_state(self) -> dict[str, Any]:
        board = [[None for _ in range(7)] for _ in range(9)]
        top = {
            (0, 0): "L", (0, 6): "T", (1, 1): "D", (1, 5): "C",
            (2, 0): "R", (2, 2): "P", (2, 4): "W", (2, 6): "E",
        }
        bottom = {
            (6, 0): "E", (6, 2): "W", (6, 4): "P", (6, 6): "R",
            (7, 1): "C", (7, 5): "D", (8, 0): "T", (8, 6): "L",
        }
        for (row, col), beast in top.items():
            board[row][col] = f"O:{beast}"
        for (row, col), beast in bottom.items():
            board[row][col] = f"X:{beast}"
        return {
            "size": 9,
            "rows": 9,
            "cols": 7,
            "board_kind": "jungle",
            "board": board,
        }

    @staticmethod
    def _coords(move: dict[str, Any]) -> tuple[int, int, int, int]:
        values = tuple(
            move.get(key)
            for key in ("from_row", "from_col", "to_row", "to_col")
        )
        if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
            raise ValueError("from_row、from_col、to_row、to_col 必须是整数")
        from_row, from_col, to_row, to_col = values
        if not (
            0 <= from_row < 9 and 0 <= to_row < 9
            and 0 <= from_col < 7 and 0 <= to_col < 7
        ):
            raise ValueError("坐标越界：row 为 0–8，col 为 0–6")
        return from_row, from_col, to_row, to_col

    @staticmethod
    def _piece(value: str) -> tuple[str, str]:
        owner, beast = value.split(":", 1)
        return owner, beast

    def _jump_target(
        self, state: dict[str, Any], row: int, col: int, dr: int, dc: int
    ) -> tuple[int, int] | None:
        r, c = row + dr, col + dc
        if (r, c) not in self.water:
            return None
        while (r, c) in self.water:
            value = state["board"][r][c]
            if value is not None and self._piece(value)[1] == "R":
                return None
            r += dr
            c += dc
        return (r, c) if 0 <= r < 9 and 0 <= c < 7 else None

    def _can_capture(
        self,
        attacker: str,
        defender: str,
        source: tuple[int, int],
        target: tuple[int, int],
    ) -> bool:
        attacker_owner, attacker_beast = self._piece(attacker)
        defender_owner, defender_beast = self._piece(defender)
        if attacker_owner == defender_owner:
            return False
        if target in self.traps[attacker_owner]:
            return True
        source_water, target_water = source in self.water, target in self.water
        if source_water != target_water:
            return False
        if attacker_beast == "E" and defender_beast == "R":
            return False
        if attacker_beast == "R" and defender_beast == "E":
            return not source_water and not target_water
        return self.ranks[attacker_beast] >= self.ranks[defender_beast]

    def _legal(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> tuple[int, int, int, int]:
        from_row, from_col, to_row, to_col = self._coords(move)
        board = state["board"]
        piece = board[from_row][from_col]
        if piece is None:
            raise ValueError("起点没有棋子")
        owner, beast = self._piece(piece)
        if owner != mark:
            raise ValueError("只能移动自己的棋子")
        if (to_row, to_col) == self.dens[mark]:
            raise ValueError("己方棋子不能进入己方兽穴")
        dr, dc = to_row - from_row, to_col - from_col
        if abs(dr) + abs(dc) != 1:
            if beast not in {"L", "T"}:
                raise ValueError("棋子每次只能横向或纵向移动一格")
            unit_dr = 0 if dr == 0 else (1 if dr > 0 else -1)
            unit_dc = 0 if dc == 0 else (1 if dc > 0 else -1)
            if dr != 0 and dc != 0:
                raise ValueError("狮虎只能横向或纵向跳河")
            if self._jump_target(state, from_row, from_col, unit_dr, unit_dc) != (
                to_row, to_col
            ):
                raise ValueError("不是合法跳河路径，或河中有鼠阻挡")
        target_is_water = (to_row, to_col) in self.water
        if target_is_water and beast != "R":
            raise ValueError("只有鼠可以进入河道")
        target = board[to_row][to_col]
        if target is not None and not self._can_capture(
            piece, target, (from_row, from_col), (to_row, to_col)
        ):
            raise ValueError("不能吃掉目标棋子")
        return from_row, from_col, to_row, to_col

    def _has_legal_move(self, state: dict[str, Any], mark: str) -> bool:
        for row in range(9):
            for col in range(7):
                value = state["board"][row][col]
                if value is None or self._piece(value)[0] != mark:
                    continue
                for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                    destinations = [(row + dr, col + dc)]
                    if self._piece(value)[1] in {"L", "T"}:
                        jump = self._jump_target(state, row, col, dr, dc)
                        if jump is not None:
                            destinations.append(jump)
                    for to_row, to_col in destinations:
                        if not (0 <= to_row < 9 and 0 <= to_col < 7):
                            continue
                        try:
                            self._legal(
                                state,
                                {
                                    "from_row": row, "from_col": col,
                                    "to_row": to_row, "to_col": to_col,
                                },
                                mark,
                            )
                            return True
                        except ValueError:
                            pass
        return False

    def validate_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> None:
        self._legal(state, move, mark)

    def apply_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> MoveResult:
        from_row, from_col, to_row, to_col = self._legal(state, move, mark)
        piece = state["board"][from_row][from_col]
        captured = state["board"][to_row][to_col]
        state["board"][to_row][to_col] = piece
        state["board"][from_row][from_col] = None
        state["last_move"] = {
            "from_row": from_row, "from_col": from_col,
            "to_row": to_row, "to_col": to_col,
            "mark": mark, "captured": captured,
        }
        opponent = "O" if mark == "X" else "X"
        note = ""
        if (to_row, to_col) == self.dens[opponent]:
            state["forced_winner"] = mark
            note = "已进入对方兽穴，立即获胜。"
        else:
            opponent_has_piece = any(
                value is not None and self._piece(value)[0] == opponent
                for row in state["board"] for value in row
            )
            if not opponent_has_piece or not self._has_legal_move(state, opponent):
                state["forced_winner"] = mark
                note = "对方已无棋子或无合法着法，本方获胜。"
        return MoveResult(state, note=note)

    def check_winner(self, state: dict[str, Any]) -> str | None:
        return state.get("forced_winner")

    def format_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> str:
        from_row, from_col, to_row, to_col = self._coords(move)
        value = state["board"][from_row][from_col]
        beast = self.names[self._piece(value)[1]] if value else "兽"
        start = f"{chr(ord('A') + from_col)}{from_row + 1}"
        end = f"{chr(ord('A') + to_col)}{to_row + 1}"
        return f"{beast} {start}→{end}"
