from __future__ import annotations

import random
from copy import deepcopy
from typing import Any

from .base import GamePlugin, MoveResult


class Banqi(GamePlugin):
    """Authoritative 4x8 Chinese dark-chess rules with a public-safe projection."""

    game_type = "banqi"
    display_name = "翻翻棋"
    category = "board"
    min_players = 2
    max_players = 2
    allowed_player_counts = (2,)
    recommended_players = 2
    supports_npcs = True
    supports_stakes = True
    mcp_immediate_public_events = True

    rows = 8
    cols = 4
    draw_quiet_turns = 40
    colors = ("r", "b")
    piece_counts = {"k": 1, "a": 2, "b": 2, "r": 2, "n": 2, "c": 2, "p": 5}
    ranks = {"k": 7, "a": 6, "b": 5, "r": 4, "n": 3, "c": 2, "p": 1}
    piece_names = {
        "r": {"k": "帅", "a": "仕", "b": "相", "r": "车", "n": "马", "c": "炮", "p": "兵"},
        "b": {"k": "将", "a": "士", "b": "象", "r": "车", "n": "马", "c": "炮", "p": "卒"},
    }
    rules_text = (
        "【目标】\n"
        "吃光对方棋子，或让对方轮到行动时无棋可走。\n\n"
        "【行动】\n"
        "32 枚中国象棋棋子随机扣放在 4×8 棋盘上。首位玩家第一次翻出的棋子颜色决定其阵营，另一方归属另一颜色。\n"
        "每回合可翻开一枚暗子，或移动一枚己方已经翻开的棋子；除炮外，棋子每次走到上下左右相邻的一格。\n\n"
        "【特殊规则】\n"
        "- 吃子等级为帅/将＞士＞象＞车＞马＞炮＞兵/卒，同级可互吃。兵/卒可吃帅/将，但帅/将不能吃兵/卒。\n"
        "- 炮只能走到相邻空位；吃子时必须与目标在同一行或同一列，中间恰有一个明子或暗子作炮架，目标必须是已翻开的敌子。\n\n"
        "【胜负】\n"
        "一方所有棋子被吃光，或轮到其行动却没有任何合法行动时判负。连续 40 手既未翻子也未吃子时判和。"
    )
    move_format = (
        '翻子：{"move":{"action":"flip","row":0,"col":0}}；'
        '走棋/吃子：{"move":{"action":"move","from_row":0,"from_col":0,'
        '"to_row":1,"to_col":0}}。row 为 0–7，col 为 0–3。'
    )

    def __init__(self, rng: random.Random | None = None) -> None:
        self._rng = rng or random.SystemRandom()

    @classmethod
    def _full_piece_set(cls) -> list[str]:
        return [
            f"{color}:{kind}"
            for color in cls.colors
            for kind, count in cls.piece_counts.items()
            for _ in range(count)
        ]

    def initial_state(self) -> dict[str, Any]:
        pieces = self._full_piece_set()
        expected = sorted(pieces)
        self._rng.shuffle(pieces)
        if len(pieces) != self.rows * self.cols or sorted(pieces) != expected:
            raise ValueError("暗棋随机源必须保留完整的 32 枚棋子")
        board = [
            [
                {"piece": pieces[row * self.cols + col], "revealed": False}
                for col in range(self.cols)
            ]
            for row in range(self.rows)
        ]
        return {
            "board_kind": "banqi",
            "rows": self.rows,
            "cols": self.cols,
            "board": board,
            "participant_order": [],
            "current_player_id": None,
            "color_by_player": {},
            "first_reveal": None,
            "last_action": None,
            "action_history": [],
            "quiet_turns": 0,
            "winner_player_id": None,
            "winner_token": None,
            "draw": False,
            "terminal_reason": None,
        }

    def initialize(self, participants: list[dict[str, Any]]) -> dict[str, Any]:
        if len(participants) != 2:
            raise ValueError("翻翻棋固定为双人对局")
        state = self.initial_state()
        state["participant_order"] = [str(item["player_id"]) for item in participants]
        opener = next(
            (item for item in participants if str(item.get("token")) == "X"),
            participants[0],
        )
        state["current_player_id"] = str(opener["player_id"])
        return state

    @classmethod
    def _coords(cls, move: dict[str, Any], prefix: str = "") -> tuple[int, int]:
        row = move.get(f"{prefix}row")
        col = move.get(f"{prefix}col")
        if (
            isinstance(row, bool)
            or isinstance(col, bool)
            or not isinstance(row, int)
            or not isinstance(col, int)
        ):
            names = f"{prefix}row 和 {prefix}col" if prefix else "row 和 col"
            raise ValueError(f"{names} 必须是整数")
        if not (0 <= row < cls.rows and 0 <= col < cls.cols):
            raise ValueError("坐标越界：row 为 0–7，col 为 0–3")
        return row, col

    @classmethod
    def _cell(cls, state: dict[str, Any], row: int, col: int) -> dict[str, Any] | None:
        return state["board"][row][col]

    @staticmethod
    def _opposite_color(color: str) -> str:
        return "b" if color == "r" else "r"

    @staticmethod
    def _piece_parts(cell: dict[str, Any]) -> tuple[str, str]:
        color, kind = str(cell["piece"]).split(":", 1)
        return color, kind

    @classmethod
    def _piece_label(cls, piece: str) -> str:
        color, kind = piece.split(":", 1)
        return f"{'红' if color == 'r' else '黑'}{cls.piece_names[color][kind]}"

    @staticmethod
    def _square_label(row: int, col: int) -> str:
        return f"{chr(ord('A') + col)}{row + 1}"

    @staticmethod
    def _other_player_id(state: dict[str, Any], player_id: str) -> str:
        order = [str(item) for item in state["participant_order"]]
        if player_id not in order or len(order) != 2:
            raise ValueError("行动者不属于当前翻翻棋对局")
        return order[1] if order[0] == player_id else order[0]

    @classmethod
    def _can_rank_capture(cls, attacker_kind: str, defender_kind: str) -> bool:
        if attacker_kind == "p" and defender_kind == "k":
            return True
        if attacker_kind == "k" and defender_kind == "p":
            return False
        return cls.ranks[attacker_kind] >= cls.ranks[defender_kind]

    @classmethod
    def _cannon_can_capture(
        cls,
        state: dict[str, Any],
        from_row: int,
        from_col: int,
        to_row: int,
        to_col: int,
        actor_color: str,
    ) -> bool:
        if from_row != to_row and from_col != to_col:
            return False
        target = cls._cell(state, to_row, to_col)
        if target is None or not target.get("revealed"):
            return False
        if cls._piece_parts(target)[0] == actor_color:
            return False
        row_step = 0 if from_row == to_row else (1 if to_row > from_row else -1)
        col_step = 0 if from_col == to_col else (1 if to_col > from_col else -1)
        row, col = from_row + row_step, from_col + col_step
        screens = 0
        while (row, col) != (to_row, to_col):
            if cls._cell(state, row, col) is not None:
                screens += 1
            row += row_step
            col += col_step
        return screens == 1

    @classmethod
    def _piece_moves(
        cls,
        state: dict[str, Any],
        row: int,
        col: int,
        actor_color: str,
    ) -> list[dict[str, Any]]:
        origin = cls._cell(state, row, col)
        if origin is None or not origin.get("revealed"):
            return []
        piece_color, piece_kind = cls._piece_parts(origin)
        if piece_color != actor_color:
            return []
        moves: list[dict[str, Any]] = []
        if piece_kind == "c":
            for row_step, col_step in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                target_row, target_col = row + row_step, col + col_step
                if (
                    0 <= target_row < cls.rows
                    and 0 <= target_col < cls.cols
                    and cls._cell(state, target_row, target_col) is None
                ):
                    moves.append({
                        "action": "move",
                        "from_row": row,
                        "from_col": col,
                        "to_row": target_row,
                        "to_col": target_col,
                    })
            for target_row in range(cls.rows):
                if target_row != row and cls._cannon_can_capture(
                    state, row, col, target_row, col, actor_color
                ):
                    moves.append({
                        "action": "move",
                        "from_row": row,
                        "from_col": col,
                        "to_row": target_row,
                        "to_col": col,
                    })
            for target_col in range(cls.cols):
                if target_col != col and cls._cannon_can_capture(
                    state, row, col, row, target_col, actor_color
                ):
                    moves.append({
                        "action": "move",
                        "from_row": row,
                        "from_col": col,
                        "to_row": row,
                        "to_col": target_col,
                    })
            return moves

        for row_step, col_step in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            target_row, target_col = row + row_step, col + col_step
            if not (0 <= target_row < cls.rows and 0 <= target_col < cls.cols):
                continue
            target = cls._cell(state, target_row, target_col)
            if target is None:
                legal = True
            elif not target.get("revealed"):
                legal = False
            else:
                target_color, target_kind = cls._piece_parts(target)
                legal = target_color != actor_color and cls._can_rank_capture(
                    piece_kind, target_kind
                )
            if legal:
                moves.append({
                    "action": "move",
                    "from_row": row,
                    "from_col": col,
                    "to_row": target_row,
                    "to_col": target_col,
                })
        return moves

    @classmethod
    def _legal_actions(
        cls, state: dict[str, Any], player_id: str
    ) -> list[dict[str, Any]]:
        if state.get("winner_player_id") or state.get("draw"):
            return []
        if player_id not in state.get("participant_order", []):
            raise ValueError("行动者不属于当前翻翻棋对局")
        actions = [
            {"action": "flip", "row": row, "col": col}
            for row in range(cls.rows)
            for col in range(cls.cols)
            if (
                (cell := cls._cell(state, row, col)) is not None
                and not cell.get("revealed")
            )
        ]
        actor_color = state.get("color_by_player", {}).get(player_id)
        if actor_color not in cls.colors:
            return actions
        for row in range(cls.rows):
            for col in range(cls.cols):
                actions.extend(cls._piece_moves(state, row, col, actor_color))
        return actions

    @classmethod
    def _remaining_by_color(cls, state: dict[str, Any]) -> dict[str, int]:
        remaining = {color: 0 for color in cls.colors}
        for row in state["board"]:
            for cell in row:
                if cell is not None:
                    remaining[cls._piece_parts(cell)[0]] += 1
        return remaining

    @staticmethod
    def _same_action(left: dict[str, Any], right: dict[str, Any]) -> bool:
        return left == right

    def validate_action(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
    ) -> None:
        if not isinstance(move, dict):
            raise ValueError("move 必须是对象")
        action = move.get("action")
        if action == "flip":
            if set(move) != {"action", "row", "col"}:
                raise ValueError("flip 只接受 action、row、col 字段")
            self._coords(move)
        elif action == "move":
            if set(move) != {
                "action", "from_row", "from_col", "to_row", "to_col"
            }:
                raise ValueError(
                    "move 只接受 action、from_row、from_col、to_row、to_col 字段"
                )
            from_row, from_col = self._coords(move, "from_")
            to_row, to_col = self._coords(move, "to_")
            if (from_row, from_col) == (to_row, to_col):
                raise ValueError("起点和落点不能相同")
        else:
            raise ValueError("action 必须是 flip 或 move")
        player_id = str(actor["player_id"])
        if not any(
            self._same_action(candidate, move)
            for candidate in self._legal_actions(state, player_id)
        ):
            raise ValueError("该行动不在服务端给出的合法行动中")

    def validate_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> None:
        del state, move, mark
        raise ValueError("翻翻棋需要 participant-aware action 接口")

    def apply_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> dict[str, Any]:
        del state, move, mark
        raise ValueError("翻翻棋需要 participant-aware action 接口")

    def _finish_if_needed(
        self,
        state: dict[str, Any],
        actor: dict[str, Any],
        next_player_id: str,
    ) -> tuple[dict[str, Any] | None, str]:
        player_id = str(actor["player_id"])
        color_by_player = state.get("color_by_player", {})
        if len(color_by_player) != 2:
            return None, ""
        remaining = self._remaining_by_color(state)
        actor_color = color_by_player[player_id]
        next_color = color_by_player[next_player_id]
        winner_id: str | None = None
        reason: str | None = None
        if remaining[actor_color] == 0:
            winner_id, reason = next_player_id, "all_captured"
        elif remaining[next_color] == 0:
            winner_id, reason = player_id, "all_captured"
        elif not self._legal_actions(state, next_player_id):
            winner_id, reason = player_id, "immobilized"
        elif state["quiet_turns"] >= self.draw_quiet_turns:
            state["draw"] = True
            state["terminal_reason"] = "quiet_turn_limit"
            state["winner_token"] = "draw"
            return {"draw": True}, "连续 40 手未翻子且未吃子，判和。"
        if winner_id is None:
            return None, ""
        winner_token = next(
            (
                str(item.get("token"))
                for item in (actor,)
                if str(item.get("player_id")) == winner_id
            ),
            None,
        )
        if winner_token is None:
            winner_token = state.get("marks_by_player", {}).get(winner_id)
        state["winner_player_id"] = winner_id
        state["winner_token"] = winner_token
        state["terminal_reason"] = reason
        note = (
            "一方所有棋子已被吃光，对局结束。"
            if reason == "all_captured"
            else "对方没有任何合法行动，困毙判负。"
        )
        return {"winner_player_id": winner_id, "draw": False}, note

    def apply_action(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
    ) -> MoveResult:
        self.validate_action(state, move, actor)
        player_id = str(actor["player_id"])
        other_player_id = self._other_player_id(state, player_id)
        action = move["action"]
        if action == "flip":
            row, col = self._coords(move)
            cell = self._cell(state, row, col)
            if cell is None:
                raise ValueError("该位置没有可翻开的暗子")
            cell["revealed"] = True
            piece = str(cell["piece"])
            first_assignment = not state["color_by_player"]
            if first_assignment:
                color, _kind = piece.split(":", 1)
                state["color_by_player"] = {
                    player_id: color,
                    other_player_id: self._opposite_color(color),
                }
                state["first_reveal"] = {
                    "player_id": player_id,
                    "row": row,
                    "col": col,
                    "piece": piece,
                }
            public_action = {
                "action": "flip",
                "actor_player_id": player_id,
                "row": row,
                "col": col,
                "piece": piece,
            }
            note = f"翻开{self._piece_label(piece)}。"
            if first_assignment:
                note += f"首翻定色，行动者执{'红' if piece.startswith('r:') else '黑'}。"
            state["quiet_turns"] = 0
        else:
            from_row, from_col = self._coords(move, "from_")
            to_row, to_col = self._coords(move, "to_")
            origin = self._cell(state, from_row, from_col)
            target = self._cell(state, to_row, to_col)
            if origin is None:
                raise ValueError("起点没有棋子")
            piece = str(origin["piece"])
            captured = (
                str(target["piece"])
                if target is not None and target.get("revealed")
                else "hidden" if target is not None else None
            )
            state["board"][to_row][to_col] = origin
            state["board"][from_row][from_col] = None
            public_action = {
                "action": "move",
                "actor_player_id": player_id,
                "from_row": from_row,
                "from_col": from_col,
                "to_row": to_row,
                "to_col": to_col,
                "piece": piece,
                "captured": captured,
            }
            state["quiet_turns"] = 0 if target is not None else state["quiet_turns"] + 1
            note = f"{self._piece_label(piece)}移动。"
            if captured == "hidden":
                note = f"{self._piece_label(piece)}吃掉一枚暗子，身份保持隐藏。"
            elif captured:
                note = f"{self._piece_label(piece)}吃掉{self._piece_label(captured)}。"

        state["last_action"] = deepcopy(public_action)
        state["action_history"].append(deepcopy(public_action))
        state["current_player_id"] = other_player_id
        result, terminal_note = self._finish_if_needed(state, actor, other_player_id)
        if terminal_note:
            note += terminal_note
        return MoveResult(state=state, note=note, result=result)

    def progress_after_action(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
        participants: list[dict[str, Any]],
        applied: dict[str, Any] | MoveResult,
    ) -> dict[str, Any] | MoveResult:
        del state, move, participants
        if not isinstance(applied, MoveResult):
            return applied
        action = applied.state.get("last_action")
        if not isinstance(action, dict):
            return applied
        if action.get("action") == "flip":
            delta = {
                key: deepcopy(action[key])
                for key in ("action", "row", "col", "piece")
            }
            first = applied.state.get("first_reveal")
            if isinstance(first, dict) and all(
                first.get(key) == action.get(key) for key in ("row", "col")
            ):
                delta["first_assignment"] = True
                delta["actor_color"] = applied.state.get(
                    "color_by_player", {}
                ).get(str(actor["player_id"]))
        else:
            delta = {
                key: deepcopy(action[key])
                for key in (
                    "action", "from_row", "from_col", "to_row", "to_col",
                    "piece", "captured",
                )
            }
        applied.public_event = {"banqi_delta": delta}
        return applied

    def check_winner(self, state: dict[str, Any]) -> str | None:
        winner = state.get("winner_token")
        return winner if winner in {"X", "O", "draw"} else None

    def result_for(
        self,
        state: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        del participants
        if state.get("draw"):
            return {"draw": True}
        winner = state.get("winner_player_id")
        return {"winner_player_id": winner, "draw": False} if winner else None

    def public_state(
        self,
        state: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any]:
        del participants
        board = []
        for row in state["board"]:
            projected_row = []
            for cell in row:
                if cell is None:
                    projected_row.append(None)
                elif cell.get("revealed"):
                    projected_row.append(str(cell["piece"]))
                else:
                    projected_row.append("hidden")
            board.append(projected_row)
        current_player_id = state.get("current_player_id")
        legal_actions = (
            self._legal_actions(state, str(current_player_id))
            if current_player_id is not None else []
        )
        return {
            "board_kind": "banqi",
            "rows": self.rows,
            "cols": self.cols,
            "board": board,
            "color_by_player": deepcopy(state.get("color_by_player", {})),
            "first_reveal": deepcopy(state.get("first_reveal")),
            "last_action": deepcopy(state.get("last_action")),
            "last_move": self._public_last_move(state.get("last_action")),
            "legal_actions": legal_actions,
            "hidden_count": sum(cell == "hidden" for row in board for cell in row),
            "quiet_turns": int(state.get("quiet_turns", 0)),
            "draw_quiet_turns": self.draw_quiet_turns,
            "terminal_reason": state.get("terminal_reason"),
            "last_action_note": state.get("last_action_note", ""),
        }

    @staticmethod
    def _public_last_move(action: dict[str, Any] | None) -> dict[str, int] | None:
        if not isinstance(action, dict):
            return None
        if action.get("action") == "flip":
            return {"row": int(action["row"]), "col": int(action["col"])}
        return {"row": int(action["to_row"]), "col": int(action["to_col"])}

    def private_state(
        self,
        state: dict[str, Any],
        viewer: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any]:
        del state, viewer, participants
        return {}

    def participant_summary(
        self,
        state: dict[str, Any],
        participant: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, str]:
        del participants
        color = state.get("color_by_player", {}).get(participant["player_id"])
        return {"camp": {"r": "红方", "b": "黑方"}.get(color, "待定")}

    def npc_compact_rules(
        self,
        state: dict[str, Any],
        actor: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> str:
        del participants
        color = state.get("color_by_player", {}).get(str(actor["player_id"]))
        camp = {"r": "红方", "b": "黑方"}.get(color, "尚未定色")
        return (
            f"4×8 翻翻棋，你当前为{camp}。暗子身份未知，只能依据公开局面选择权威行动。"
            "flip 翻一枚暗子；move 移动己方明子。普通棋相邻走/吃并按等级，兵可吃将帅而"
            "将帅不能吃兵；炮相邻走空位，隔恰好一子沿直线吃任意敌方明子，炮架可明可暗。"
            "无子或无合法行动者负；连续 40 手不翻不吃和棋。"
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
        return self._legal_actions(state, str(actor["player_id"]))

    def format_action(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
    ) -> str:
        del actor
        if move.get("action") == "flip":
            row, col = self._coords(move)
            cell = self._cell(state, row, col)
            if cell is None or cell.get("revealed"):
                return f"翻开 {self._square_label(row, col)}"
            return (
                f"翻开 {self._square_label(row, col)} · "
                f"{self._piece_label(str(cell['piece']))}"
            )
        from_row, from_col = self._coords(move, "from_")
        to_row, to_col = self._coords(move, "to_")
        origin = self._cell(state, from_row, from_col)
        target = self._cell(state, to_row, to_col)
        piece_label = (
            self._piece_label(str(origin["piece"]))
            if origin is not None and origin.get("revealed") else "棋子"
        )
        separator = "×" if target is not None else "→"
        target_label = "暗子" if target is not None and not target.get("revealed") else ""
        return (
            f"{piece_label} {self._square_label(from_row, from_col)}"
            f"{separator}{self._square_label(to_row, to_col)}"
            f"{f'（{target_label}）' if target_label else ''}"
        )

    def format_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> str:
        del mark
        return self.format_action(state, move, {})
