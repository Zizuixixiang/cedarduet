import json
from copy import deepcopy
from typing import Any

from .base import GamePlugin, MoveResult


class Checkers(GamePlugin):
    """Authoritative 8x8 English draughts (American checkers) rules."""

    min_players = 2
    max_players = 2
    allowed_player_counts = (2,)
    recommended_players = 2
    supports_npcs = True
    supports_stakes = True
    game_type = "checkers"
    display_name = "西洋跳棋"
    category = "board"
    rules_text = (
        "【目标】\n"
        "吃光对方棋子，或让对方无棋可走。\n\n"
        "【行动】\n"
        "西洋跳棋采用 English draughts（American checkers）规则：8×8 棋盘上只有 32 个深色格可走，双方各有 12 枚普通棋。\n"
        "普通棋每次向前斜走一格；王棋可向前或向后斜走一格，但不是远距离飞王。吃子时越过相邻敌棋，落到紧邻的空格；普通棋只可向前吃，王棋可前后吃。\n\n"
        "【特殊规则】\n"
        "- 场上只要有棋可吃，就禁止普通移动；但不要求选择吃子数最多的路线。\n"
        "- 一次跳吃后同一枚棋若仍可吃，必须继续跳吃。\n"
        "- 普通棋到达对方底线立即升王，并按 English draughts 规则结束该手；新王要到下一回合才能继续行动。\n\n"
        "【胜负】\n"
        "使对方无棋或无合法行动者获胜。以下情况自动判和：\n"
        "- 棋子位置、普通棋/王棋身份与轮到的一方都相同的局面第三次出现。\n"
        "- 双方各自此前连续 40 手都没有把普通棋向升王线推进，也都没有吃子；王棋的普通移动不会中断这项计数。"
    )
    move_format = (
        '每次移动或多跳中的每一跳都提交真实零起始坐标：{"move":{"from_row":5,'
        '"from_col":0,"to_row":4,"to_col":1},"revision":当前版本}；row、col 均为 '
        "0–7。多跳时服务端保留同一玩家行动权，并只发布被锁定棋子的下一跳 legal_moves。"
    )

    _KING = "k"
    _MAN = "m"

    def initial_state(self) -> dict[str, Any]:
        board = [[None for _ in range(8)] for _ in range(8)]
        for row in range(3):
            for col in range(8):
                if self._is_dark(row, col):
                    board[row][col] = "O:m"
        for row in range(5, 8):
            for col in range(8):
                if self._is_dark(row, col):
                    board[row][col] = "X:m"
        state: dict[str, Any] = {
            "size": 8,
            "rows": 8,
            "cols": 8,
            "board_kind": "checkers",
            "board": board,
            "turn_mark": "X",
            "forced_piece": None,
            # WCDF removes captured men after a complete sequence. The Web
            # board removes each one immediately for clarity, while these
            # squares remain unavailable as landing squares until the chain ends.
            "captured_during_turn": [],
            "action_history": [],
        }
        self._sync_turn(state, "X")
        self._update_counts(state)
        self._reset_draw_tracking(state)
        return state

    @staticmethod
    def _is_dark(row: int, col: int) -> bool:
        return (row + col) % 2 == 1

    @staticmethod
    def _opponent(mark: str) -> str:
        if mark == "X":
            return "O"
        if mark == "O":
            return "X"
        raise ValueError("棋子阵营必须是 X 或 O")

    @staticmethod
    def _piece(value: str) -> tuple[str, str]:
        try:
            owner, kind = value.split(":", 1)
        except (AttributeError, ValueError) as exc:
            raise ValueError("棋盘包含无效棋子") from exc
        if owner not in {"X", "O"} or kind not in {"m", "k"}:
            raise ValueError("棋盘包含无效棋子")
        return owner, kind

    @staticmethod
    def _coords(move: dict[str, Any]) -> tuple[int, int, int, int]:
        if not isinstance(move, dict) or set(move) != {
            "from_row", "from_col", "to_row", "to_col"
        }:
            raise ValueError(
                "走法只接受 from_row、from_col、to_row、to_col 四个字段"
            )
        values = tuple(
            move.get(key)
            for key in ("from_row", "from_col", "to_row", "to_col")
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in values
        ):
            raise ValueError("from_row、from_col、to_row、to_col 必须是整数")
        from_row, from_col, to_row, to_col = values
        if not all(0 <= value < 8 for value in values):
            raise ValueError("坐标越界：row 和 col 必须在 0–7 之间")
        if (from_row, from_col) == (to_row, to_col):
            raise ValueError("起点和落点不能相同")
        return from_row, from_col, to_row, to_col

    @staticmethod
    def _directions(mark: str, kind: str) -> tuple[tuple[int, int], ...]:
        if kind == "k":
            return ((-1, -1), (-1, 1), (1, -1), (1, 1))
        forward = -1 if mark == "X" else 1
        return ((forward, -1), (forward, 1))

    @staticmethod
    def _move(
        from_row: int, from_col: int, to_row: int, to_col: int
    ) -> dict[str, int]:
        return {
            "from_row": from_row,
            "from_col": from_col,
            "to_row": to_row,
            "to_col": to_col,
        }

    @staticmethod
    def _is_capture(move: dict[str, Any]) -> bool:
        return abs(move["to_row"] - move["from_row"]) == 2

    def _capture_actions_from(
        self,
        state: dict[str, Any],
        row: int,
        col: int,
        mark: str,
    ) -> list[dict[str, int]]:
        board = state["board"]
        value = board[row][col]
        if value is None:
            return []
        owner, kind = self._piece(value)
        if owner != mark:
            return []
        blocked_landings = {
            (item["row"], item["col"])
            for item in state.get("captured_during_turn", [])
            if isinstance(item, dict)
            and isinstance(item.get("row"), int)
            and isinstance(item.get("col"), int)
        }
        actions: list[dict[str, int]] = []
        for dr, dc in self._directions(mark, kind):
            jumped_row, jumped_col = row + dr, col + dc
            to_row, to_col = row + 2 * dr, col + 2 * dc
            if not (
                0 <= jumped_row < 8
                and 0 <= jumped_col < 8
                and 0 <= to_row < 8
                and 0 <= to_col < 8
            ):
                continue
            jumped = board[jumped_row][jumped_col]
            if jumped is None or self._piece(jumped)[0] == mark:
                continue
            if board[to_row][to_col] is not None:
                continue
            if (to_row, to_col) in blocked_landings:
                continue
            actions.append(self._move(row, col, to_row, to_col))
        return actions

    def _simple_actions_from(
        self,
        state: dict[str, Any],
        row: int,
        col: int,
        mark: str,
    ) -> list[dict[str, int]]:
        board = state["board"]
        value = board[row][col]
        if value is None:
            return []
        owner, kind = self._piece(value)
        if owner != mark:
            return []
        actions = []
        for dr, dc in self._directions(mark, kind):
            to_row, to_col = row + dr, col + dc
            if (
                0 <= to_row < 8
                and 0 <= to_col < 8
                and board[to_row][to_col] is None
            ):
                actions.append(self._move(row, col, to_row, to_col))
        return actions

    def legal_actions(
        self, state: dict[str, Any], mark: str
    ) -> list[dict[str, int]]:
        """Derive the complete authoritative action list for ``mark``."""
        if mark not in {"X", "O"}:
            raise ValueError("棋子阵营必须是 X 或 O")
        forced = state.get("forced_piece")
        if forced is not None:
            if not isinstance(forced, dict):
                raise ValueError("连续吃子锁定状态无效")
            row, col = forced.get("row"), forced.get("col")
            if (
                isinstance(row, bool)
                or isinstance(col, bool)
                or not isinstance(row, int)
                or not isinstance(col, int)
                or not (0 <= row < 8 and 0 <= col < 8)
            ):
                raise ValueError("连续吃子锁定坐标无效")
            return self._capture_actions_from(state, row, col, mark)

        captures = [
            action
            for row in range(8)
            for col in range(8)
            for action in self._capture_actions_from(state, row, col, mark)
        ]
        if captures:
            return captures
        return [
            action
            for row in range(8)
            for col in range(8)
            for action in self._simple_actions_from(state, row, col, mark)
        ]

    def _sync_turn(self, state: dict[str, Any], mark: str) -> None:
        actions = self.legal_actions(state, mark)
        state["turn_mark"] = mark
        state["legal_moves"] = actions
        state["must_capture"] = bool(actions and self._is_capture(actions[0]))

    def _update_counts(self, state: dict[str, Any]) -> None:
        counts = {"X": 0, "O": 0}
        kings = {"X": 0, "O": 0}
        for row in state["board"]:
            for value in row:
                if value is None:
                    continue
                owner, kind = self._piece(value)
                counts[owner] += 1
                if kind == self._KING:
                    kings[owner] += 1
        state["piece_counts"] = counts
        state["king_counts"] = kings

    def _position_key(self, state: dict[str, Any]) -> str:
        """Return every rule-relevant component of a checkers position.

        Draw counters and history are deliberately excluded: WCDF repetition
        compares the position itself.  Forced-capture state is included even
        though repetitions are recorded only after a complete turn, keeping
        this canonical key correct for a state restored during a jump sequence.
        """
        symbols = {
            None: ".",
            "X:m": "x",
            "X:k": "X",
            "O:m": "o",
            "O:k": "O",
        }
        board = state.get("board")
        if (
            not isinstance(board, list)
            or len(board) != 8
            or any(not isinstance(row, list) or len(row) != 8 for row in board)
        ):
            raise ValueError("西洋跳棋棋盘必须是 8×8 数组")
        try:
            board_key = "".join(symbols[value] for row in board for value in row)
        except (KeyError, TypeError) as exc:
            raise ValueError("棋盘包含无效棋子") from exc

        turn_mark = state.get("turn_mark")
        if turn_mark not in {"X", "O"}:
            raise ValueError("服务端行棋方必须是 X 或 O")
        forced = state.get("forced_piece")
        if forced is None:
            forced_key = None
        elif isinstance(forced, dict):
            row, col = forced.get("row"), forced.get("col")
            if (
                isinstance(row, bool)
                or isinstance(col, bool)
                or not isinstance(row, int)
                or not isinstance(col, int)
                or not (0 <= row < 8 and 0 <= col < 8)
            ):
                raise ValueError("连续吃子锁定坐标无效")
            forced_key = [row, col]
        else:
            raise ValueError("连续吃子锁定状态无效")

        captured_key = []
        for item in state.get("captured_during_turn", []):
            if not isinstance(item, dict):
                raise ValueError("连续吃子记录无效")
            row, col, piece = item.get("row"), item.get("col"), item.get("piece")
            if (
                isinstance(row, bool)
                or isinstance(col, bool)
                or not isinstance(row, int)
                or not isinstance(col, int)
                or not (0 <= row < 8 and 0 <= col < 8)
            ):
                raise ValueError("连续吃子记录坐标无效")
            self._piece(piece)
            captured_key.append([row, col, piece])
        captured_key.sort(key=lambda item: (item[0], item[1], item[2]))
        return json.dumps(
            [board_key, turn_mark, forced_key, captured_key],
            ensure_ascii=True,
            separators=(",", ":"),
        )

    def _reset_draw_tracking(self, state: dict[str, Any]) -> None:
        state["draw_tracking"] = {
            "position_counts": {self._position_key(state): 1},
            "no_progress_moves": {"X": 0, "O": 0},
        }
        state.pop("draw_reason", None)

    def _ensure_draw_tracking(
        self,
        state: dict[str, Any],
        *,
        register_current: bool = True,
    ) -> dict[str, Any]:
        """Normalize persisted counters and safely bootstrap legacy rooms."""
        raw = state.get("draw_tracking")
        if not isinstance(raw, dict):
            raw = {}
        raw_counts = raw.get("position_counts")
        raw_no_progress = raw.get("no_progress_moves")
        valid_counts = isinstance(raw_counts, dict)
        if valid_counts:
            valid_counts = all(
                isinstance(key, str)
                and not isinstance(count, bool)
                and isinstance(count, int)
                and count >= 1
                for key, count in raw_counts.items()
            )
        valid_no_progress = (
            isinstance(raw_no_progress, dict)
            and set(raw_no_progress) == {"X", "O"}
            and all(
                not isinstance(count, bool)
                and isinstance(count, int)
                and count >= 0
                for count in raw_no_progress.values()
            )
        )
        tracker = {
            "position_counts": dict(raw_counts) if valid_counts else {},
            "no_progress_moves": (
                dict(raw_no_progress)
                if valid_no_progress
                else {"X": 0, "O": 0}
            ),
        }
        # Old rooms do not have draw metadata.  Register their current complete
        # position as the first known occurrence; a partial jump is not a turn
        # boundary and therefore must never enter the repetition history.
        if register_current and state.get("forced_piece") is None:
            tracker["position_counts"].setdefault(self._position_key(state), 1)
        state["draw_tracking"] = tracker
        return tracker

    def _complete_turn_draw_reason(
        self,
        state: dict[str, Any],
        mark: str,
        *,
        moved_man: bool,
        captured: bool,
    ) -> str | None:
        """Settle WCDF draw counters once, after a complete player turn."""
        tracker = self._ensure_draw_tracking(state, register_current=False)
        no_progress = tracker["no_progress_moves"]
        no_progress[mark] = (
            0 if moved_man or captured else no_progress[mark] + 1
        )
        key = self._position_key(state)
        occurrences = tracker["position_counts"].get(key, 0) + 1
        tracker["position_counts"][key] = occurrences
        if occurrences >= 3:
            return "threefold_repetition"
        if all(no_progress[side] >= 40 for side in ("X", "O")):
            return "forty_move_rule"
        return None

    def _legal_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> tuple[int, int, int, int]:
        from_row, from_col, to_row, to_col = self._coords(move)
        if not self._is_dark(from_row, from_col) or not self._is_dark(to_row, to_col):
            raise ValueError("西洋跳棋只能在深色格移动")
        if state.get("turn_mark", mark) != mark:
            raise ValueError("当前行动者与服务端行棋方不一致")
        value = state["board"][from_row][from_col]
        if value is None:
            raise ValueError("起点没有棋子")
        if self._piece(value)[0] != mark:
            raise ValueError("只能移动自己的棋子")
        forced = state.get("forced_piece")
        if forced is not None and (from_row, from_col) != (
            forced.get("row"), forced.get("col")
        ):
            raise ValueError("连续吃子必须继续移动同一枚棋")
        legal = self.legal_actions(state, mark)
        if move not in legal:
            if legal and self._is_capture(legal[0]) and abs(to_row - from_row) == 1:
                raise ValueError("当前有可吃子，禁止普通走")
            raise ValueError("该走法不合法；请从服务端 legal_moves 中选择")
        return from_row, from_col, to_row, to_col

    def validate_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> None:
        self._legal_move(state, move, mark)

    def apply_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> MoveResult:
        self._ensure_draw_tracking(state)
        from_row, from_col, to_row, to_col = self._legal_move(state, move, mark)
        board = state["board"]
        original_piece = board[from_row][from_col]
        if original_piece is None:
            raise ValueError("起点没有棋子")
        _owner, original_kind = self._piece(original_piece)
        capture = abs(to_row - from_row) == 2
        captured: dict[str, Any] | None = None
        previous_chain = list(state.get("captured_during_turn", []))
        board[from_row][from_col] = None
        if capture:
            captured_row = (from_row + to_row) // 2
            captured_col = (from_col + to_col) // 2
            captured_piece = board[captured_row][captured_col]
            if captured_piece is None:
                raise ValueError("跳跃路径中没有可吃棋子")
            captured = {
                "row": captured_row,
                "col": captured_col,
                "piece": captured_piece,
            }
            board[captured_row][captured_col] = None
        board[to_row][to_col] = original_piece

        promotion_row = 0 if mark == "X" else 7
        promoted = original_kind == self._MAN and to_row == promotion_row
        if promoted:
            board[to_row][to_col] = f"{mark}:{self._KING}"

        jump_number = len(previous_chain) + 1 if capture else 0
        chain_continues = False
        continuation: list[dict[str, int]] = []
        if capture and not promoted:
            state["captured_during_turn"] = [*previous_chain, deepcopy(captured)]
            state["forced_piece"] = {"row": to_row, "col": to_col}
            continuation = self._capture_actions_from(
                state, to_row, to_col, mark
            )
            chain_continues = bool(continuation)

        if chain_continues:
            state["turn_mark"] = mark
            state["legal_moves"] = continuation
            state["must_capture"] = True
            retain_turn = True
            note = "本次跳吃后仍有可吃子，必须继续移动同一枚棋。"
        else:
            state["forced_piece"] = None
            state["captured_during_turn"] = []
            opponent = self._opponent(mark)
            self._sync_turn(state, opponent)
            retain_turn = False
            note = ""
            opponent_has_piece = any(
                value is not None and self._piece(value)[0] == opponent
                for row in board
                for value in row
            )
            if not opponent_has_piece:
                state["winner_mark"] = mark
                state["terminal_reason"] = "no_pieces"
                note = "对方已无棋子，本方获胜。"
            elif not state["legal_moves"]:
                state["winner_mark"] = mark
                state["terminal_reason"] = "no_legal_moves"
                note = "对方已无合法行动，本方获胜。"
            elif promoted:
                note = "棋子到达王线并升王；按 English draughts 规则本手结束。"
            elif capture:
                note = "跳吃完成。"

            draw_reason = self._complete_turn_draw_reason(
                state,
                mark,
                moved_man=original_kind == self._MAN,
                captured=capture,
            )
            if "winner_mark" not in state and draw_reason is not None:
                state["draw_reason"] = draw_reason
                state["terminal_reason"] = draw_reason
                note = (
                    "同一局面第三次出现，和棋。"
                    if draw_reason == "threefold_repetition"
                    else "双方各自连续 40 手未推进普通棋且未吃子，和棋。"
                )

        record: dict[str, Any] = {
            "from_row": from_row,
            "from_col": from_col,
            "to_row": to_row,
            "to_col": to_col,
            "mark": mark,
            "piece": original_piece,
            "captured": captured,
            "promoted": promoted,
            "jump_number": jump_number,
            "chain_continues": chain_continues,
        }
        state["last_move"] = deepcopy(record)
        state.setdefault("action_history", []).append(deepcopy(record))
        self._update_counts(state)
        return MoveResult(state, retain_turn=retain_turn, note=note)

    def check_winner(self, state: dict[str, Any]) -> str | None:
        winner = state.get("winner_mark")
        if winner in {"X", "O"}:
            return winner
        if state.get("draw_reason") in {
            "threefold_repetition", "forty_move_rule"
        }:
            return "draw"
        turn_mark = state.get("turn_mark")
        if turn_mark not in {"X", "O"}:
            return None
        has_piece = any(
            value is not None and self._piece(value)[0] == turn_mark
            for row in state["board"]
            for value in row
        )
        if not has_piece or not self.legal_actions(state, turn_mark):
            return self._opponent(turn_mark)
        return None

    def npc_compact_rules(
        self,
        state: dict[str, Any],
        actor: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> str:
        del state, actor, participants
        return (
            "8×8 English draughts，仅深色格。普通棋只向前斜走/跳，王可前后但不是飞王；"
            "有吃必吃，不要求最大吃子数。跳后仍可吃时必须用同一枚继续；跳到王线则升王并"
            "立即结束该手。使对方无棋或无合法行动即胜；同一局面第三次出现，或双方各自"
            "连续 40 手都未推进普通棋且未吃子时和棋。只能选择权威 legal_actions。"
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
        del participants
        mark = str(actor.get("token", ""))
        if mark != state.get("turn_mark"):
            return []
        return deepcopy(self.legal_actions(state, mark))

    def format_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> str:
        del mark
        from_row, from_col, to_row, to_col = self._coords(move)
        value = state["board"][from_row][from_col]
        prefix = "王 " if value and self._piece(value)[1] == self._KING else ""
        separator = "×" if abs(to_row - from_row) == 2 else "–"
        start = f"{chr(ord('A') + from_col)}{from_row + 1}"
        end = f"{chr(ord('A') + to_col)}{to_row + 1}"
        return f"{prefix}{start}{separator}{end}"
