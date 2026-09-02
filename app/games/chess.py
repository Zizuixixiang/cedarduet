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
        "【目标】\n"
        "将死对方的王。\n\n"
        "【行动】\n"
        "采用标准 8×8 国际象棋规则，白方先行。王、后、车、象、马、兵按各自规则移动；不能走出让己方王仍受攻击的着法。\n\n"
        "【特殊规则】\n"
        "完整支持王车易位、吃过路兵，以及兵到达底线后升变为后、车、象或马。\n\n"
        "【胜负】\n"
        "将死判攻击方获胜；逼和、子力不足等死局自动判和。轮到自己行棋时，若当前同一局面已第三次出现，"
        "或双方已各走满 50 步且期间没有兵移动与吃子，可以选择申和；也可在落子前声明一手合法"
        "着法，并在该着法将形成第三次重复或完成第 50 回合时申和（申和成立则该着法不实际落盘）。"
        "不申和仍可继续走棋。"
        "同一局面第五次出现，或双方各走满 75 步且期间没有兵移动与吃子时自动和棋；"
        "若第 75 步的最后一手同时将死，将死优先。"
    )
    move_format = (
        '移动使用零起始坐标：{"move":{"from_row":6,"from_col":4,'
        '"to_row":4,"to_col":4}}；row 0 是黑方底线，'
        'col 0 是 a 线。兵升变必须另加 "promotion":"q|r|b|n"。'
        '当前局面满足条件时提交 {"move":{"action":"claim_draw"}}；若由下一手达成，'
        '提交 legal_actions 中带 from_row/from_col/to_row/to_col（及可能 promotion）的 '
        'claim_draw 动作。'
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
        "fivefold_repetition": "同一局面第五次出现，自动和棋。",
        "seventy_five_move_rule": "双方各 75 步未走兵且未吃子，自动和棋。",
    }
    claim_notes = {
        "threefold_repetition": "同一局面已第三次出现",
        "fifty_move_rule": "双方各 50 步未走兵且未吃子",
    }
    claim_draw_action = {"action": "claim_draw"}

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
        state = {
            "size": 8,
            "rows": 8,
            "cols": 8,
            "board_kind": "chess",
            "starting_fen": starting_fen,
            "move_history": list(move_history),
            "action_history": deepcopy(action_history or []),
            **engine,
        }
        state["legal_actions"] = [
            Chess._payload(item) for item in state.get("legal_moves", [])
        ]
        if state.get("can_claim_draw"):
            state["legal_actions"].append(deepcopy(Chess.claim_draw_action))
        state["legal_actions"].extend({
            "action": "claim_draw",
            **Chess._payload(item),
        } for item in state.get("intended_draw_claims", []))
        if state.get("game_over"):
            state["legal_actions"] = []
        return state

    def public_state(
        self,
        state: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any]:
        del participants
        projected = deepcopy(state)
        projected.pop("position_history", None)
        return projected

    def mcp_snapshot_state(
        self,
        public_state: dict[str, Any],
        viewer: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any]:
        snapshot = super().mcp_snapshot_state(
            public_state, viewer, participants
        )
        snapshot.pop("starting_fen", None)
        # legal_actions is the compact submit-ready form of legal_moves.
        snapshot.pop("legal_moves", None)
        return snapshot

    def mcp_bootstrap_state(
        self,
        public_state: dict[str, Any],
        viewer: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any]:
        bootstrap = super().mcp_bootstrap_state(
            public_state, viewer, participants
        )
        bootstrap.pop("legal_moves", None)
        return bootstrap

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
        expected_mark = self._mark_for_color(state["turn_color"])
        if mark != expected_mark:
            raise ValueError("当前行动者与规则引擎行棋方不一致")
        if move.get("action") == "claim_draw":
            if move not in state.get("legal_actions", []):
                raise ValueError("当前局面不满足可申和条件")
            return
        if "action" in move:
            raise ValueError("未知国际象棋动作")
        from_row, from_col, _to_row, _to_col, _promotion = self._coords(move)
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
        if move.get("action") == "claim_draw":
            updated = deepcopy(state)
            intended = None
            if move != self.claim_draw_action:
                wanted = self._uci(move)
                intended = next(
                    (
                        item for item in updated.get("intended_draw_claims", [])
                        if item.get("uci") == wanted
                    ),
                    None,
                )
            reasons = list(
                intended.get("reasons", [])
                if isinstance(intended, dict)
                else updated.get("claimable_draw_reasons", [])
            )
            if not reasons:
                raise ValueError("当前局面不满足可申和条件")
            labels = [self.claim_notes[reason] for reason in reasons]
            claim = {
                "action": "claim_draw",
                "mark": mark,
                "reasons": reasons,
                **({"intended_move": self._payload(intended)} if intended else {}),
            }
            updated["action_history"] = [
                *updated.get("action_history", []), claim,
            ]
            updated["last_action"] = deepcopy(claim)
            updated["winner_mark"] = "draw"
            updated["terminal_reason"] = "claimed_draw"
            updated["draw_reason"] = "claimed_draw"
            updated["draw_claim_reasons"] = reasons
            updated["claimed_draw"] = True
            updated["in_draw"] = True
            updated["game_over"] = True
            updated["can_claim_draw"] = False
            updated["claimable_draw_reasons"] = []
            updated["intended_draw_claims"] = []
            updated["legal_moves"] = []
            updated["legal_actions"] = []
            return MoveResult(
                updated,
                note=f"申和成立：{'；'.join(labels)}。",
            )
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
            "升变动作必须保留 promotion 字段；claim_draw 可能是当前局面申和，也可能"
            "携带声明的下一手坐标，必须原样选择。"
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
        actions = state.get("legal_actions")
        if isinstance(actions, list):
            return deepcopy(actions)
        return [self._payload(item) for item in state.get("legal_moves", [])]

    def format_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> str:
        del mark
        if move.get("action") == "claim_draw":
            intended = None
            if move != self.claim_draw_action:
                wanted = self._uci(move)
                intended = next((
                    item for item in state.get("intended_draw_claims", [])
                    if item.get("uci") == wanted
                ), None)
            reasons = (
                intended.get("reasons", []) if isinstance(intended, dict)
                else state.get("claimable_draw_reasons", [])
            )
            labels = [self.claim_notes.get(reason, reason) for reason in reasons]
            return f"申和（{'；'.join(labels) or '和棋条件'}）"
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
