from typing import Any

from .base import GamePlugin, MoveResult
from .xiangqi_engine import XiangqiEngineError, engine_apply, engine_state


class Xiangqi(GamePlugin):
    min_players = 2
    max_players = 2
    allowed_player_counts = (2,)
    recommended_players = 2
    supports_npcs = False
    supports_stakes = True
    game_type = "xiangqi"
    display_name = "象棋"
    rules_text = (
        "象棋使用 9 路 10 行棋盘，红方先行。车走直线，马走日且受马腿限制，"
        "象走田且受象眼限制并不可过河，士与将帅限于九宫；炮不吃子时同车，"
        "吃子时必须隔恰好一个炮架；兵卒过河前只可前进，过河后可横走但不可后退。"
        "不得走出令己方将帅受攻或将帅照面的着法。将死或无合法着法（困毙）均判负。"
        "第一版不裁决竞赛级长将长捉责任。"
    )
    move_format = (
        '移动使用零起始起终点：{"move":{"from_row":9,"from_col":0,'
        '"to_row":8,"to_col":0}}；row 自黑方底线向红方底线为 0–9，col 为 0–8。'
    )
    piece_names = {
        "r": {
            "r": "车", "n": "马", "b": "相", "a": "仕",
            "k": "帅", "c": "炮", "p": "兵",
        },
        "b": {
            "r": "车", "n": "马", "b": "象", "a": "士",
            "k": "将", "c": "炮", "p": "卒",
        },
    }

    @staticmethod
    def _mark_for_color(color: str) -> str:
        if color == "r":
            return "X"
        if color == "b":
            return "O"
        raise ValueError("规则引擎返回了未知棋子颜色")

    @staticmethod
    def _coords(move: dict[str, Any]) -> tuple[int, int, int, int]:
        keys = ("from_row", "from_col", "to_row", "to_col")
        values = tuple(move.get(key) for key in keys)
        if any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in values
        ):
            raise ValueError("from_row、from_col、to_row、to_col 必须是整数")
        from_row, from_col, to_row, to_col = values
        if not (
            0 <= from_row < 10 and 0 <= to_row < 10
            and 0 <= from_col < 9 and 0 <= to_col < 9
        ):
            raise ValueError("坐标越界：row 为 0–9，col 为 0–8")
        if (from_row, from_col) == (to_row, to_col):
            raise ValueError("起点和落点不能相同")
        return from_row, from_col, to_row, to_col

    @classmethod
    def _iccs(cls, move: dict[str, Any]) -> str:
        from_row, from_col, to_row, to_col = cls._coords(move)
        return (
            f"{chr(ord('a') + from_col)}{9 - from_row}"
            f"{chr(ord('a') + to_col)}{9 - to_row}"
        )

    @staticmethod
    def _with_metadata(engine: dict[str, Any]) -> dict[str, Any]:
        return {
            "size": 10,
            "rows": 10,
            "cols": 9,
            "board_kind": "xiangqi",
            **engine,
        }

    def initial_state(self) -> dict[str, Any]:
        try:
            return self._with_metadata(engine_state())
        except XiangqiEngineError as exc:
            raise ValueError(str(exc)) from exc

    def initialize(self, participants: list[dict[str, Any]]) -> dict[str, Any]:
        kinds = [item.get("participant_kind") for item in participants]
        if len(kinds) != 2 or set(kinds) != {"human", "bound_machine"}:
            raise ValueError("象棋固定需要 1 个人类和 1 只真实绑定小机")
        return self.initial_state()

    def state_from_fen(self, fen: str) -> dict[str, Any]:
        """Build a test/admin position through the authoritative engine."""
        try:
            return self._with_metadata(engine_state(fen))
        except XiangqiEngineError as exc:
            raise ValueError(str(exc)) from exc

    def validate_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> None:
        from_row, from_col, to_row, to_col = self._coords(move)
        expected_mark = self._mark_for_color(state["turn_color"])
        if mark != expected_mark:
            raise ValueError("当前行动者与规则引擎行棋方不一致")
        piece = state["board"][from_row][from_col]
        if piece is None:
            raise ValueError("起点没有棋子")
        color, _piece_type = piece.split(":", 1)
        if self._mark_for_color(color) != mark:
            raise ValueError("只能移动自己的棋子")
        wanted = (from_row, from_col, to_row, to_col)
        legal = any(
            (
                item.get("from_row"), item.get("from_col"),
                item.get("to_row"), item.get("to_col"),
            ) == wanted
            for item in state.get("legal_moves", [])
        )
        if not legal:
            raise ValueError("该走法不合法，或会令己方将帅受攻")

    def apply_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> MoveResult:
        self.validate_move(state, move, mark)
        from_row, from_col, to_row, to_col = self._coords(move)
        origin = state["board"][from_row][from_col]
        captured = state["board"][to_row][to_col]
        iccs = self._iccs(move)
        try:
            engine, applied = engine_apply(state["fen"], iccs)
        except XiangqiEngineError as exc:
            raise ValueError(str(exc)) from exc
        updated = self._with_metadata(engine)
        updated["move_history"] = [*state.get("move_history", []), iccs]
        updated["last_move"] = {
            "from_row": from_row,
            "from_col": from_col,
            "to_row": to_row,
            "to_col": to_col,
            "mark": mark,
            "piece": origin,
            "captured": captured,
            "iccs": applied.get("iccs", iccs),
        }
        note = ""
        if updated["in_checkmate"]:
            updated["winner_mark"] = mark
            updated["terminal_reason"] = "checkmate"
            note = "将死，对局结束。"
        elif updated["in_stalemate"]:
            updated["winner_mark"] = mark
            updated["terminal_reason"] = "stalemate"
            note = "对方无合法着法，困毙判负。"
        elif updated["in_draw"]:
            updated["winner_mark"] = "draw"
            updated["terminal_reason"] = "draw"
            note = "规则引擎判定和棋。"
        elif updated["in_check"]:
            note = "将军。"
        return MoveResult(updated, note=note)

    def check_winner(self, state: dict[str, Any]) -> str | None:
        winner = state.get("winner_mark")
        return winner if winner in {"X", "O", "draw"} else None

    def format_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> str:
        from_row, from_col, to_row, to_col = self._coords(move)
        value = state["board"][from_row][from_col]
        name = "棋子"
        if value:
            color, piece_type = value.split(":", 1)
            name = self.piece_names.get(color, {}).get(piece_type, name)
        start = f"{chr(ord('A') + from_col)}{from_row + 1}"
        end = f"{chr(ord('A') + to_col)}{to_row + 1}"
        return f"{name} {start}→{end}"
