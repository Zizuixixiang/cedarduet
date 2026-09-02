from __future__ import annotations

from collections import deque
from copy import deepcopy
from typing import Any

from .base import GamePlugin, MoveResult


Axial = tuple[int, int]

_DIRECTIONS: tuple[Axial, ...] = (
    (1, 0),
    (0, 1),
    (-1, 1),
    (-1, 0),
    (0, -1),
    (1, -1),
)


def _rotate_axial(point: Axial, steps: int) -> Axial:
    q, r = point
    for _ in range(steps % 6):
        q, r = -r, q + r
    return q, r


def _build_board() -> tuple[
    tuple[Axial, ...],
    dict[Axial, str],
    dict[str, Axial],
    tuple[frozenset[str], ...],
    dict[str, int],
]:
    central = {
        (q, r)
        for q in range(-4, 5)
        for r in range(-4, 5)
        if max(abs(q), abs(r), abs(q + r)) <= 4
    }
    top_camp = {
        (q, r)
        for r in range(-8, -4)
        for q in range(-r - 4, 5)
    }
    camp_coords = tuple(
        frozenset(_rotate_axial(point, index) for point in top_camp)
        for index in range(6)
    )
    coordinates = tuple(sorted(
        central.union(*(set(camp) for camp in camp_coords)),
        key=lambda point: (point[1], point[0]),
    ))
    if len(coordinates) != 121 or any(len(camp) != 10 for camp in camp_coords):
        raise RuntimeError("中国跳棋棋盘拓扑生成失败")
    node_by_coord = {
        point: f"n{index:03d}" for index, point in enumerate(coordinates)
    }
    coord_by_node = {node_id: point for point, node_id in node_by_coord.items()}
    camps = tuple(
        frozenset(node_by_coord[point] for point in camp)
        for camp in camp_coords
    )
    camp_by_node = {
        node_id: camp_index
        for camp_index, camp in enumerate(camps)
        for node_id in camp
    }
    return coordinates, node_by_coord, coord_by_node, camps, camp_by_node


(
    _COORDINATES,
    _NODE_BY_COORD,
    _COORD_BY_NODE,
    _CAMPS,
    _CAMP_BY_NODE,
) = _build_board()


class ChineseCheckers(GamePlugin):
    """Standard public-information 121-hole Chinese Checkers."""

    game_type = "chinese_checkers"
    display_name = "中国跳棋"
    category = "board"
    min_players = 2
    max_players = 6
    allowed_player_counts = (2, 3, 4, 6)
    recommended_players = 4
    supports_npcs = True
    supports_stakes = True
    supports_multiplayer_stakes = True
    uses_custom_stake_settlement = True
    # If every wallet-backed participant has forfeited, system NPCs do not
    # receive those chips. The negative wallet deltas intentionally leave the
    # participant economy instead of being credited to an NPC wallet.
    allows_non_zero_sum_settlement = True
    rules_text = (
        "【目标】\n"
        "把自己的 10 颗弹珠送入正对面的目标营。游戏使用标准 121 孔六角星棋盘，支持 2、3、4、6 人，不支持 5 人；2 人使用一对对角营，3 人隔一角入座，4 人使用两对对角营，6 人六角全开。\n\n"
        "【行动】\n"
        "每回合只移动一颗弹珠。可以沿六个方向走到相邻空孔并立即结束；也可以沿六方向直线以遇到的第一颗任意玩家弹珠为跳板。若当前位置与跳板之间有 k 个连续空孔，跳板另一侧也必须有 k 个连续空孔，且与当前位置关于跳板对称的等距落点必须为空；相邻跳是 k=0 的特例。同一回合可连续跳跃任意次，相邻跳与等距跳可以混合。跳跃不吃子，同一跳跃链不能重复落点，也不能混入普通一步。\n\n"
        "【特殊规则】\n"
        "- 自己的起始营和目标营可以停留；其他四个角营不能作为回合终点，但连续跳的中间落点可以穿过。\n"
        "- 弹珠一旦进入自己的目标营便不能离开；连续跳跃中一旦某个落点进入目标营，"
        "后续每个落点也必须留在目标营。\n"
        "- 本局采用 anti-spoiling 防拖延规则：目标营十孔全部被占、其中至少一颗是你的棋，且其余阻挡棋只属于该营的原始拥有者时，也立即判你获胜。开局不会因此误判，第三方棋也不能冒充有效阻挡。\n\n"
        "【胜负】\n"
        "通常先把自己的 10 颗弹珠全部送入目标营者获胜；anti-spoiling 条件成立时同样立即获胜。"
        "开局后认输或离开均按弃权：该席及其弹珠从行动顺序中移除，只要仍有至少两名有效参与者就继续，"
        "不要求剩余人数仍是可开局桌型。终局赢家和正向筹码只从最终仍 active 的有效参与者产生；"
        "此前 inactive 的真人或绑定小机统一记负。若只剩系统 NPC，房间立即结束且不再推进 NPC；"
        "系统 NPC 的筹码变动恒为 0。"
    )
    move_format = (
        '提交稳定 node id：{"move":{"from":"n000","to":"n014"}}；'
        '可附 kind="step" 或 "jump"。服务端从当前棋面计算'
        "权威 legal_moves，并为每个跳跃终点选择一条稳定 canonical path；客户端无需逐跳"
        "提交，也不得提交自选 path。"
    )

    _SEAT_CAMPS: dict[int, tuple[int, ...]] = {
        2: (0, 3),
        3: (0, 2, 4),
        4: (0, 1, 3, 4),
        6: (0, 1, 2, 3, 4, 5),
    }

    @staticmethod
    def tokens_for(participants: list[dict[str, Any]]) -> list[str]:
        return [f"P{index + 1}" for index, _item in enumerate(participants)]

    def first_player_id(
        self, participants: list[dict[str, Any]], mode: str
    ) -> str:
        opener = super().first_player_id(participants, mode)
        # ``initialize`` follows immediately with this same room-local list.
        # The hint lets the initial public legal_moves honor human/AI-first
        # without mutable plugin-global state.
        for participant in participants:
            participant["_chinese_checkers_opener"] = (
                str(participant["player_id"]) == opener
            )
        return opener

    @staticmethod
    def _participant_fixture(count: int = 2) -> list[dict[str, Any]]:
        return [
            {
                "player_id": f"player-{index + 1}",
                "token": f"P{index + 1}",
                "seat_index": index,
                "role": "human" if index == 0 else "ai",
                "_chinese_checkers_opener": index == 0,
            }
            for index in range(count)
        ]

    def initial_state(self) -> dict[str, Any]:
        return self.initialize(self._participant_fixture())

    def initialize(self, participants: list[dict[str, Any]]) -> dict[str, Any]:
        count = len(participants)
        if count not in self._SEAT_CAMPS:
            raise ValueError("中国跳棋只支持 2、3、4、6 人桌，明确不支持 5 人")
        starts = self._SEAT_CAMPS[count]
        player_ids = [str(item["player_id"]) for item in participants]
        tokens_by_player = {
            str(item["player_id"]): str(item["token"]) for item in participants
        }
        start_by_player = {
            player_id: starts[index]
            for index, player_id in enumerate(player_ids)
        }
        target_by_player = {
            player_id: (camp_index + 3) % 6
            for player_id, camp_index in start_by_player.items()
        }
        camp_owners: dict[str, str | None] = {
            str(index): None for index in range(6)
        }
        for player_id, camp_index in start_by_player.items():
            camp_owners[str(camp_index)] = player_id
        pieces = {
            node_id: tokens_by_player[player_id]
            for player_id, camp_index in start_by_player.items()
            for node_id in _CAMPS[camp_index]
        }
        nodes = []
        for q, r in _COORDINATES:
            node_id = _NODE_BY_COORD[(q, r)]
            nodes.append({
                "id": node_id,
                "q": q,
                "r": r,
                "camp": _CAMP_BY_NODE.get(node_id),
            })
        opener = next(
            (
                str(item["player_id"])
                for item in participants
                if item.get("_chinese_checkers_opener")
            ),
            player_ids[0],
        )
        state: dict[str, Any] = {
            "board_kind": "chinese_checkers",
            "rows": 17,
            "cols": 25,
            "node_count": 121,
            "nodes": nodes,
            "camps": {
                str(index): sorted(camp) for index, camp in enumerate(_CAMPS)
            },
            "participant_order": player_ids,
            "tokens_by_player": tokens_by_player,
            "start_camps_by_player": start_by_player,
            "target_camps_by_player": target_by_player,
            "camp_owner_player_ids": camp_owners,
            "pieces": pieces,
            "action_history": [],
            "winner_player_id": None,
            "winner_token": None,
            "terminal_reason": None,
        }
        self._update_progress(state)
        self._sync_turn(state, opener)
        # The common framework normally calls ``first_player_id`` before
        # initialize, so ``opener`` is exact. Rooms created with an explicit
        # opener bypass that callback; keep a compact authoritative opening map
        # so their UI/NPC can still select the proper seat's actions. It is
        # removed atomically after the first move.
        state["legal_moves_by_player"] = {
            player_id: self.legal_actions(state, tokens_by_player[player_id])
            for player_id in player_ids
        }
        return state

    @staticmethod
    def _node_id(value: Any, field: str) -> str:
        if not isinstance(value, str) or value not in _COORD_BY_NODE:
            raise ValueError(f"{field} 必须是棋盘上的稳定 node id")
        return value

    def _parse_move(self, move: dict[str, Any]) -> tuple[str, str, str | None]:
        if not isinstance(move, dict) or set(move) not in (
            {"from", "to"},
            {"from", "to", "kind"},
        ):
            raise ValueError("走法只接受 from、to，以及可选的 kind；不得提交 path")
        from_node = self._node_id(move.get("from"), "from")
        to_node = self._node_id(move.get("to"), "to")
        if from_node == to_node:
            raise ValueError("起点和终点不能相同")
        kind = move.get("kind")
        if kind is not None and kind not in {"step", "jump"}:
            raise ValueError("kind 只能是 step 或 jump")
        return from_node, to_node, kind

    @staticmethod
    def _player_id_for_token(state: dict[str, Any], token: str) -> str:
        player_id = next(
            (
                player_id
                for player_id, candidate in state["tokens_by_player"].items()
                if candidate == token
            ),
            None,
        )
        if player_id is None:
            raise ValueError("棋子 token 不属于本局参与者")
        return str(player_id)

    @staticmethod
    def _allowed_terminal(
        state: dict[str, Any], player_id: str, node_id: str
    ) -> bool:
        camp_index = _CAMP_BY_NODE.get(node_id)
        if camp_index is None:
            return True
        return camp_index in {
            state["start_camps_by_player"][player_id],
            state["target_camps_by_player"][player_id],
        }

    def _jump_paths(
        self,
        state: dict[str, Any],
        from_node: str,
        player_id: str,
    ) -> dict[str, list[str]]:
        pieces = state["pieces"]
        fixed_occupied = set(pieces) - {from_node}
        target_camp = state["target_camps_by_player"][player_id]
        paths: dict[str, list[str]] = {}
        visited = {from_node}
        queue: deque[tuple[str, list[str], bool]] = deque([
            (from_node, [from_node], from_node in _CAMPS[target_camp])
        ])
        while queue:
            current, path, entered_target = queue.popleft()
            q, r = _COORD_BY_NODE[current]
            for dq, dr in _DIRECTIONS:
                distance = 1
                while True:
                    jumped = _NODE_BY_COORD.get((
                        q + distance * dq,
                        r + distance * dr,
                    ))
                    if jumped is None or jumped in fixed_occupied:
                        break
                    distance += 1
                if jumped is None:
                    continue
                landing = _NODE_BY_COORD.get((
                    q + 2 * distance * dq,
                    r + 2 * distance * dr,
                ))
                if (
                    landing is None
                    or landing in fixed_occupied
                    or landing in visited
                ):
                    continue
                if any(
                    (
                        mirrored := _NODE_BY_COORD.get((
                            q + offset * dq,
                            r + offset * dr,
                        ))
                    ) is None or mirrored in fixed_occupied
                    for offset in range(distance + 1, 2 * distance)
                ):
                    continue
                if entered_target and landing not in _CAMPS[target_camp]:
                    continue
                visited.add(landing)
                canonical = [*path, landing]
                paths[landing] = canonical
                queue.append((
                    landing,
                    canonical,
                    entered_target or landing in _CAMPS[target_camp],
                ))
        return paths

    def legal_actions(
        self, state: dict[str, Any], token: str
    ) -> list[dict[str, Any]]:
        player_id = self._player_id_for_token(state, token)
        pieces = state["pieces"]
        target_camp = state["target_camps_by_player"][player_id]
        actions: list[dict[str, Any]] = []
        for from_node in sorted(
            node_id for node_id, owner in pieces.items() if owner == token
        ):
            q, r = _COORD_BY_NODE[from_node]
            locked_in_target = from_node in _CAMPS[target_camp]
            for dq, dr in _DIRECTIONS:
                to_node = _NODE_BY_COORD.get((q + dq, r + dr))
                if to_node is None or to_node in pieces:
                    continue
                if locked_in_target and to_node not in _CAMPS[target_camp]:
                    continue
                if not self._allowed_terminal(state, player_id, to_node):
                    continue
                actions.append({
                    "from": from_node,
                    "to": to_node,
                    "kind": "step",
                    "path": [from_node, to_node],
                })
            for to_node, path in self._jump_paths(
                state, from_node, player_id
            ).items():
                if not self._allowed_terminal(state, player_id, to_node):
                    continue
                actions.append({
                    "from": from_node,
                    "to": to_node,
                    "kind": "jump",
                    "path": path,
                })
        actions.sort(key=lambda action: (
            action["from"],
            action["to"],
            0 if action["kind"] == "step" else 1,
            tuple(action["path"]),
        ))
        return actions

    def _sync_turn(self, state: dict[str, Any], player_id: str) -> None:
        token = state["tokens_by_player"].get(player_id)
        if token is None:
            raise ValueError("下一行动者不属于本局")
        state["turn_player_id"] = player_id
        state["turn_token"] = token
        state["legal_moves"] = self.legal_actions(state, token)

    @staticmethod
    def _active_participants(
        participants: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return [
            item for item in sorted(
                participants, key=lambda participant: participant.get("seat_index", 0)
            )
            if item.get("join_status", "joined") == "joined"
            and item.get("active", True)
            and item.get("activity_state", "active") == "active"
        ]

    @staticmethod
    def _is_wallet_participant(participant: dict[str, Any]) -> bool:
        kind = participant.get("participant_kind")
        return kind in {"human", "bound_machine"} or (
            kind is None and participant.get("role") in {"human", "ai"}
        )

    def accepts_active_count_after_resignation(self, count: int) -> bool:
        # Five players cannot start a fresh table, but a six-player game remains
        # valid after one forfeit. A live game only needs two active seats.
        return 2 <= count <= self.max_players

    def apply_resignation(
        self,
        state: dict[str, Any],
        resigned_player_id: str,
        participants: list[dict[str, Any]],
    ) -> None:
        order = list(state.get("participant_order", []))
        if resigned_player_id not in order:
            raise ValueError("中国跳棋弃权者不属于当前有效行动顺序")
        resigned_index = order.index(resigned_player_id)
        active_ids = {
            str(item["player_id"])
            for item in self._active_participants(participants)
        }
        # Framework marks the current participant inactive before invoking the
        # hook. Discard explicitly as a defensive contract for direct callers.
        active_ids.discard(resigned_player_id)
        remaining = [
            player_id for player_id in order
            if player_id in active_ids
        ]
        inactive_ids = [
            player_id for player_id in order if player_id not in active_ids
        ]
        removed_tokens = {
            token
            for player_id in inactive_ids
            if (token := state.get("tokens_by_player", {}).get(player_id))
            is not None
        }
        opening_actions_present = "legal_moves_by_player" in state

        state["participant_order"] = remaining
        resigned = state.setdefault("resigned_player_ids", [])
        for player_id in inactive_ids:
            if player_id not in resigned:
                resigned.append(player_id)
        if removed_tokens:
            state["pieces"] = {
                node_id: owner
                for node_id, owner in state.get("pieces", {}).items()
                if owner not in removed_tokens
            }
        for player_id in inactive_ids:
            start_camp = state.get("start_camps_by_player", {}).pop(
                player_id, None
            )
            state.get("target_camps_by_player", {}).pop(player_id, None)
            state.get("tokens_by_player", {}).pop(player_id, None)
            state.get("marks_by_player", {}).pop(player_id, None)
            state.get("target_progress_by_player", {}).pop(player_id, None)
            if (
                start_camp is not None
                and state.get("camp_owner_player_ids", {}).get(str(start_camp))
                == player_id
            ):
                state["camp_owner_player_ids"][str(start_camp)] = None
        if removed_tokens and isinstance(state.get("marks"), dict):
            state["marks"] = {
                role: mark for role, mark in state["marks"].items()
                if mark not in removed_tokens
            }

        if not remaining:
            state["turn_player_id"] = None
            state["turn_token"] = None
            state["legal_moves"] = []
            state.pop("legal_moves_by_player", None)
            self._update_progress(state)
            return

        current = state.get("turn_player_id")
        if current not in remaining:
            current = next(
                str(order[(resigned_index + offset) % len(order)])
                for offset in range(1, len(order) + 1)
                if order[(resigned_index + offset) % len(order)] in remaining
            )
        self._update_progress(state)
        self._sync_turn(state, str(current))
        if opening_actions_present:
            state["legal_moves_by_player"] = {
                player_id: self.legal_actions(
                    state, state["tokens_by_player"][player_id]
                )
                for player_id in remaining
            }

    def _legal_move(
        self, state: dict[str, Any], move: dict[str, Any], token: str
    ) -> dict[str, Any]:
        from_node, to_node, requested_kind = self._parse_move(move)
        if (
            state.get("action_history")
            and state.get("turn_token") not in {None, token}
        ):
            raise ValueError("当前行动者与服务端行棋方不一致")
        value = state["pieces"].get(from_node)
        if value is None:
            raise ValueError("起点没有弹珠")
        if value != token:
            raise ValueError("只能移动自己的弹珠")
        candidates = [
            action
            for action in self.legal_actions(state, token)
            if action["from"] == from_node and action["to"] == to_node
        ]
        if requested_kind is not None:
            candidates = [
                action for action in candidates
                if action["kind"] == requested_kind
            ]
        if not candidates:
            if requested_kind is not None:
                raise ValueError("kind 与该终点的权威移动类型不一致，不能混用步行与跳跃")
            raise ValueError("该走法不合法；请从服务端 legal_moves 中选择")
        return candidates[0]

    def validate_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> None:
        self._legal_move(state, move, mark)

    def validate_action(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
    ) -> None:
        player_id = str(actor["player_id"])
        if (
            state.get("action_history")
            and state.get("turn_player_id") not in {None, player_id}
        ):
            raise ValueError("当前行动者与服务端行棋方不一致")
        self._legal_move(state, move, str(actor["token"]))

    @staticmethod
    def _next_player_id(state: dict[str, Any], player_id: str) -> str:
        order = state["participant_order"]
        return str(order[(order.index(player_id) + 1) % len(order)])

    def _update_progress(self, state: dict[str, Any]) -> None:
        progress: dict[str, int] = {}
        for player_id, token in state["tokens_by_player"].items():
            target = _CAMPS[state["target_camps_by_player"][player_id]]
            progress[player_id] = sum(
                state["pieces"].get(node_id) == token for node_id in target
            )
        state["target_progress_by_player"] = progress

    def _winning_reason(
        self, state: dict[str, Any], player_id: str
    ) -> str | None:
        token = state["tokens_by_player"][player_id]
        target_index = state["target_camps_by_player"][player_id]
        target = _CAMPS[target_index]
        occupants = [state["pieces"].get(node_id) for node_id in target]
        if all(owner == token for owner in occupants):
            return "target_complete"
        original_owner = state["camp_owner_player_ids"].get(str(target_index))
        blocker_token = (
            state["tokens_by_player"].get(original_owner)
            if original_owner is not None else None
        )
        if (
            blocker_token is not None
            and any(owner == token for owner in occupants)
            and all(owner is not None for owner in occupants)
            and all(owner in {token, blocker_token} for owner in occupants)
        ):
            return "anti_spoiling"
        return None

    def _apply_for_player(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        player_id: str,
        token: str,
    ) -> MoveResult:
        canonical = self._legal_move(state, move, token)
        state.pop("legal_moves_by_player", None)
        pieces = state["pieces"]
        pieces.pop(canonical["from"])
        pieces[canonical["to"]] = token
        record = {
            "from": canonical["from"],
            "to": canonical["to"],
            "kind": canonical["kind"],
            "path": list(canonical["path"]),
            "jump_count": (
                len(canonical["path"]) - 1
                if canonical["kind"] == "jump" else 0
            ),
            "player_id": player_id,
            "token": token,
        }
        state["last_move"] = deepcopy(record)
        state.setdefault("action_history", []).append(deepcopy(record))
        self._update_progress(state)
        reason = self._winning_reason(state, player_id)
        if reason is not None:
            state["winner_player_id"] = player_id
            state["winner_token"] = token
            state["terminal_reason"] = reason
            state["turn_player_id"] = None
            state["turn_token"] = None
            state["legal_moves"] = []
            note = (
                "目标营十孔已由自己的弹珠填满，本方获胜。"
                if reason == "target_complete"
                else "目标营已填满；按 anti-spoiling 规则，原营主人留下的阻挡棋不能拖延胜利。"
            )
            return MoveResult(
                state=state,
                note=note,
                result={
                    "winner_player_id": player_id,
                    "draw": False,
                    "tied_player_ids": [],
                    "terminal_reason": reason,
                },
            )
        next_player_id = self._next_player_id(state, player_id)
        self._sync_turn(state, next_player_id)
        note = (
            f"连续跳跃 {record['jump_count']} 次；服务端已使用 canonical path。"
            if canonical["kind"] == "jump" and record["jump_count"] > 1
            else ""
        )
        return MoveResult(
            state=state,
            next_player_id=next_player_id,
            note=note,
        )

    def apply_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> MoveResult:
        player_id = self._player_id_for_token(state, mark)
        return self._apply_for_player(state, move, player_id, mark)

    def apply_action(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
    ) -> MoveResult:
        return self._apply_for_player(
            state,
            move,
            str(actor["player_id"]),
            str(actor["token"]),
        )

    def check_winner(self, state: dict[str, Any]) -> str | None:
        token = state.get("winner_token")
        return str(token) if isinstance(token, str) else None

    def result_for(
        self,
        state: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        winner = state.get("winner_player_id")
        eligible_ids = {
            str(item["player_id"])
            for item in self._active_participants(participants)
        }
        if not isinstance(winner, str) or winner not in eligible_ids:
            return None
        return {
            "winner_player_id": winner,
            "winning_player_ids": [winner],
            "draw": False,
            "tied_player_ids": [],
            "terminal_reason": state.get("terminal_reason"),
        }

    def settlement_deltas(
        self,
        state: dict[str, Any],
        result: dict[str, Any],
        participants: list[dict[str, Any]],
        stake: int,
    ) -> dict[str, int]:
        del state
        ordered = sorted(participants, key=lambda item: item.get("seat_index", 0))
        player_ids = [str(item["player_id"]) for item in ordered]
        eligible_ids = {
            str(item["player_id"])
            for item in self._active_participants(ordered)
        }
        raw_winners = result.get("winning_player_ids")
        if raw_winners is None:
            raw_winners = [result.get("winner_player_id")]
        if not isinstance(raw_winners, list) or any(
            not isinstance(player_id, str) for player_id in raw_winners
        ):
            raise ValueError("中国跳棋终局获胜者列表无效")
        winner_ids = list(dict.fromkeys(raw_winners))
        if not winner_ids or not set(winner_ids).issubset(eligible_ids):
            if not (
                result.get("reason") == "resignation_forfeit"
                and not winner_ids
                and not eligible_ids
            ):
                raise ValueError("中国跳棋终局获胜者必须仍为 active eligible")
        if result.get("draw"):
            raise ValueError("中国跳棋终局必须有唯一有效赢家")
        wallet_ids = {
            str(item["player_id"])
            for item in ordered if self._is_wallet_participant(item)
        }
        wallet_winners = [
            player_id for player_id in winner_ids if player_id in wallet_ids
        ]
        wallet_losers = [
            player_id for player_id in player_ids
            if player_id in wallet_ids and player_id not in winner_ids
        ]
        deltas = {player_id: 0 for player_id in player_ids}
        if wallet_winners:
            for player_id in wallet_losers:
                deltas[player_id] = -stake * len(wallet_winners)
            for player_id in wallet_winners:
                deltas[player_id] = stake * len(wallet_losers)
        else:
            # All active winners are system NPCs (or nobody remains). They have
            # no wallet and never receive chips; inactive wallet seats still
            # record one forfeited stake as their loss.
            for player_id in wallet_losers:
                deltas[player_id] = -stake
        return deltas

    def result_for_resignation(
        self,
        state: dict[str, Any],
        resigned_player_id: str,
        participants: list[dict[str, Any]],
    ) -> dict[str, Any]:
        ordered = sorted(participants, key=lambda item: item.get("seat_index", 0))
        all_player_ids = [str(item["player_id"]) for item in ordered]
        if resigned_player_id not in all_player_ids:
            raise ValueError("中国跳棋认输终局缺少有效参与者")
        active = self._active_participants(ordered)
        winners = [str(item["player_id"]) for item in active]
        only_system_npcs = bool(active) and all(
            item.get("participant_kind") == "system_npc" for item in active
        )
        terminal_reason = (
            "only_system_npcs_remaining" if only_system_npcs
            else "no_active_participants" if not winners
            else "resignation_forfeit"
        )
        winner = winners[0] if winners else None
        state["winner_player_id"] = winner
        state["winner_token"] = (
            state.get("tokens_by_player", {}).get(winner) if winner else None
        )
        state["winning_player_ids"] = list(winners)
        state["terminal_reason"] = terminal_reason
        state["turn_player_id"] = None
        state["turn_token"] = None
        state["legal_moves"] = []
        state.pop("legal_moves_by_player", None)
        return {
            "draw": False,
            "reason": "resignation_forfeit",
            "terminal_reason": terminal_reason,
            "resigned_player_id": resigned_player_id,
            "winner_player_id": winner,
            "winning_player_ids": winners,
            "result_text": (
                "所有真人参与者均已退出；房间立即结束，系统 NPC 不参与筹码。"
                if only_system_npcs
                else "弃权席统一记负，终局只认定仍 active 的有效参与者。"
            ),
        }

    def mcp_snapshot_state(
        self,
        public_state: dict[str, Any],
        viewer: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any]:
        snapshot = super().mcp_snapshot_state(
            public_state, viewer, participants
        )
        # Node coordinates and camp membership are immutable and arrive in the
        # one-time bootstrap. Current pieces/camp ownership/progress are kept.
        snapshot.pop("nodes", None)
        snapshot.pop("camps", None)
        snapshot.pop("legal_moves_by_player", None)
        snapshot["legal_moves"] = self._mcp_legal_moves(snapshot)
        return snapshot

    @staticmethod
    def _mcp_legal_moves(state: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {
                key: move[key]
                for key in ("from", "to", "kind")
                if key in move
            }
            for move in state.get("legal_moves", [])
        ]

    def mcp_bootstrap_state(
        self,
        public_state: dict[str, Any],
        viewer: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any]:
        bootstrap = super().mcp_bootstrap_state(
            public_state, viewer, participants
        )
        bootstrap.pop("legal_moves_by_player", None)
        bootstrap["legal_moves"] = self._mcp_legal_moves(bootstrap)
        return bootstrap

    def participant_summary(
        self,
        state: dict[str, Any],
        participant: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, int | bool]:
        del participants
        player_id = str(participant["player_id"])
        progress = int(state["target_progress_by_player"].get(player_id, 0))
        return {
            "target_progress": progress,
            "target_total": 10,
            "completed": state.get("winner_player_id") == player_id,
        }

    def npc_compact_rules(
        self,
        state: dict[str, Any],
        actor: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> str:
        del state, actor, participants
        return (
            "每回合从权威动作中选一项，只动一颗。step 是六方向相邻一步；jump 是不吃子"
            "的完整连续跳，服务端已选 canonical path。其它角营不可作终点但跳链可穿过；"
            "跳链一旦进入自己的目标营，后续落点都必须留在营内。inactive 席及其弹珠会"
            "退出行动顺序；只从当前 active eligible 参与者产生合法动作与终局赢家。"
        )

    def npc_public_actions(
        self,
        state: dict[str, Any],
        actor: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        del actor, participants
        return deepcopy(state.get("action_history", [])[-16:])

    def npc_legal_actions(
        self,
        state: dict[str, Any],
        actor: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        del participants
        player_id = str(actor.get("player_id", ""))
        token = str(actor.get("token", ""))
        if state.get("action_history") and state.get("turn_player_id") != player_id:
            return []
        return [
            {
                "from": action["from"],
                "to": action["to"],
                "kind": action["kind"],
            }
            for action in self.legal_actions(state, token)
        ]

    def format_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> str:
        action = self._legal_move(state, move, mark)
        if action["kind"] == "step":
            return f"{action['from']}–{action['to']}"
        return (
            f"{action['from']}⇢{action['to']}"
            f"（{len(action['path']) - 1} 跳）"
        )
