from copy import deepcopy
from typing import Any

from .base import GamePlugin, MoveResult
from .chess_engine import (
    STANDARD_FEN,
    ChessEngineError,
    engine_apply,
    engine_state,
)


class Chess(GamePlugin):
    min_players = 2
    max_players = 2
    allowed_player_counts = (2,)
    recommended_players = 2
    supports_npcs = True
    supports_stakes = True
    game_type = "chess"
    display_name = "国际象棋"
    category = "board"
    rules_text = (
        "标准 8×8 国际象棋，白方先行。王、后、车、象、马、兵按标准规则移动；"
        "不得走出令己方王受攻击的着法。完整支持王车易位、吃过路兵，以及兵到达"
        "底线后升变为后、车、象或马。将死判攻击方获胜；逼和、子力不足、三次重复"
        "或连续五十回合没有兵移动与吃子时，由内置 chess.js 规则引擎自动判和。"
    )
    move_format = (
        '移动使用零起始坐标：{"move":{"from_row":6,"from_col":4,'
        '"to_row":4,"to_col":4},"revision":当前版本}；row 0 是黑方底线，'
        'col 0 是 a 线。兵升变必须另加 "promotion":"q|r|b|n"。'
    )
    piece_names = {
        "p": "兵",
        "n": "马",
        "b": "象",
        "r": "车",
        "q": "后",
        "k": "王",
    }
    draw_notes = {
        "stalemate": "逼和，对局结束。",
        "insufficient_material": "子力不足，和棋。",
        "threefold_repetition": "同一局面三次重复，和棋。",
        "fifty_move_rule": "五十回合未走兵且未吃子，和棋。",
    }

    @staticmethod
    def _mark_for_color(color: str) -> str:
        if color == "w":
            return "X"
        if color == "b":
            return "O"
        raise ValueError("规则引擎返回了未知棋子颜色")

    @staticmethod
    def _coords(move: dict[str, Any]) -> tuple[int, int, int, int, str | None]:
        keys = ("from_row", "from_col", "to_row", "to_col")
        values = tuple(move.get(key) for key in keys)
        if any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in values
        ):
            raise ValueError("from_row、from_col、to_row、to_col 必须是整数")
        from_row, from_col, to_row, to_col = values
        if not all(0 <= value < 8 for value in values):
            raise ValueError("坐标越界：row 和 col 均为 0–7")
        if (from_row, from_col) == (to_row, to_col):
            raise ValueError("起点和落点不能相同")
        promotion = move.get("promotion")
        if promotion is not None and promotion not in {"q", "r", "b", "n"}:
            raise ValueError("promotion 必须是 q、r、b 或 n")
        return from_row, from_col, to_row, to_col, promotion

    @classmethod
    def _uci(cls, move: dict[str, Any]) -> str:
        from_row, from_col, to_row, to_col, promotion = cls._coords(move)
        return (
            f"{chr(ord('a') + from_col)}{8 - from_row}"
            f"{chr(ord('a') + to_col)}{8 - to_row}"
            f"{promotion or ''}"
        )

    @staticmethod
    def _payload(legal: dict[str, Any]) -> dict[str, Any]:
        payload = {
            key: legal[key]
            for key in ("from_row", "from_col", "to_row", "to_col")
        }
        if legal.get("promotion"):
            payload["promotion"] = legal["promotion"]
        return payload

    @staticmethod
    def _with_metadata(
        engine: dict[str, Any],
        *,
        starting_fen: str,
        move_history: list[str],
        action_history: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return {
            "size": 8,
            "rows": 8,
            "cols": 8,
            "board_kind": "chess",
            "starting_fen": starting_fen,
            "move_history": list(move_history),
            "action_history": deepcopy(action_history or []),
            **engine,
        }

    def initial_state(self) -> dict[str, Any]:
        try:
            engine = engine_state(STANDARD_FEN, [])
        except ChessEngineError as exc:
            raise ValueError(str(exc)) from exc
        return self._with_metadata(
            engine,
            starting_fen=STANDARD_FEN,
            move_history=[],
        )

    def initialize(self, participants: list[dict[str, Any]]) -> dict[str, Any]:
        if len(participants) != 2:
            raise ValueError("国际象棋固定需要 2 名参与者")
        return self.initial_state()

    def state_from_fen(
        self,
        fen: str,
        history: list[str] | None = None,
    ) -> dict[str, Any]:
        """Build a test/admin position through the authoritative engine."""
        move_history = list(history or [])
        try:
            engine = engine_state(fen, move_history)
        except ChessEngineError as exc:
            raise ValueError(str(exc)) from exc
        return self._with_metadata(
            engine,
            starting_fen=fen,
            move_history=move_history,
        )

    def validate_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> None:
        from_row, from_col, _to_row, _to_col, _promotion = self._coords(move)
        expected_mark = self._mark_for_color(state["turn_color"])
        if mark != expected_mark:
            raise ValueError("当前行动者与规则引擎行棋方不一致")
        piece = state["board"][from_row][from_col]
        if piece is None:
            raise ValueError("起点没有棋子")
        color, _piece_type = piece.split(":", 1)
        if self._mark_for_color(color) != mark:
            raise ValueError("只能移动自己的棋子")
        wanted = self._uci(move)
        if not any(item.get("uci") == wanted for item in state.get("legal_moves", [])):
            raise ValueError("该走法不合法，或会令己方王受攻击")

    def apply_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> MoveResult:
        self.validate_move(state, move, mark)
        from_row, from_col, to_row, to_col, promotion = self._coords(move)
        origin = state["board"][from_row][from_col]
        target = state["board"][to_row][to_col]
        uci = self._uci(move)
        old_history = list(state.get("move_history", []))
        try:
            engine, applied = engine_apply(
                state.get("starting_fen", STANDARD_FEN),
                old_history,
                uci,
            )
        except ChessEngineError as exc:
            raise ValueError(str(exc)) from exc
        captured = target
        if captured is None and applied.get("captured"):
            captured_color = "b" if applied.get("color") == "w" else "w"
            captured = f"{captured_color}:{applied['captured']}"
        action = {
            "from_row": from_row,
            "from_col": from_col,
            "to_row": to_row,
            "to_col": to_col,
            **({"promotion": promotion} if promotion else {}),
            "mark": mark,
            "piece": origin,
            "captured": captured,
            "uci": applied.get("uci", uci),
            "san": applied.get("san", uci),
            "flags": applied.get("flags", ""),
        }
        updated = self._with_metadata(
            engine,
            starting_fen=state.get("starting_fen", STANDARD_FEN),
            move_history=[*old_history, uci],
            action_history=[*state.get("action_history", []), action],
        )
        updated["last_move"] = deepcopy(action)
        note = ""
        if updated["in_checkmate"]:
            updated["winner_mark"] = mark
            updated["terminal_reason"] = "checkmate"
            note = "将死，对局结束。"
        elif updated["in_draw"]:
            updated["winner_mark"] = "draw"
            reason = updated.get("draw_reason") or "draw"
            updated["terminal_reason"] = reason
            note = self.draw_notes.get(reason, "规则引擎判定和棋。")
        elif updated["in_check"]:
            note = "将军。"
        return MoveResult(updated, note=note)

    def check_winner(self, state: dict[str, Any]) -> str | None:
        winner = state.get("winner_mark")
        return winner if winner in {"X", "O", "draw"} else None

    def npc_compact_rules(
        self,
        state: dict[str, Any],
        actor: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> str:
        del state, actor, participants
        return (
            "标准国际象棋。只能从权威 legal_actions 选择走法；优先将死、避免被将死，"
            "升变动作必须保留 promotion 字段。"
        )

    def npc_public_actions(
        self,
        state: dict[str, Any],
        actor: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        del actor, participants
        return deepcopy(state.get("action_history", [])[-40:])

    def npc_legal_actions(
        self,
        state: dict[str, Any],
        actor: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        del participants
        if actor.get("token") != self._mark_for_color(state["turn_color"]):
            return []
        return [self._payload(item) for item in state.get("legal_moves", [])]

    def format_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> str:
        del mark
        from_row, from_col, to_row, to_col, promotion = self._coords(move)
        value = state["board"][from_row][from_col]
        piece_type = value.split(":", 1)[1] if value else ""
        start = f"{chr(ord('a') + from_col)}{8 - from_row}"
        end = f"{chr(ord('a') + to_col)}{8 - to_row}"
        if piece_type == "k" and abs(to_col - from_col) == 2:
            return "王车易位 O-O" if to_col > from_col else "王车易位 O-O-O"
        capture = any(
            item.get("uci") == self._uci(move) and item.get("captured")
            for item in state.get("legal_moves", [])
        )
        suffix = (
            f"={self.piece_names[promotion]}" if promotion else ""
        )
        return (
            f"{self.piece_names.get(piece_type, '棋子')} "
            f"{start}{'×' if capture else '→'}{end}{suffix}"
        )
