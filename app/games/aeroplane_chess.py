from __future__ import annotations

import random
from copy import deepcopy
from typing import Any

from .base import GamePlugin, MoveResult
from .tools import ensure_flow, roll_dice


COLORS = ("red", "yellow", "blue", "green")
COLOR_LABELS = {
    "red": "红方",
    "yellow": "黄方",
    "blue": "蓝方",
    "green": "绿方",
}
START_RING_INDEX = {
    color: index * 13 for index, color in enumerate(COLORS)
}
RING_LENGTH = 52
HOME_LANE_LENGTH = 6
FINISH_ROUTE_STEP = RING_LENGTH + HOME_LANE_LENGTH + 1

# A route step is relative to one colour: -1 is the hangar, 0 the safe launch
# area, 1..52 the shared ring, 53..58 the private home lane, and 59 home.
# The special tile at step 21 replaces that tile's ordinary four-space jump.
OWN_COLOR_JUMP_STEPS = frozenset(
    step for step in range(1, 49, 4) if step != 21
)
SHORTCUT_FROM_STEP = 21
SHORTCUT_TO_STEP = 33
SHORTCUT_CROSS_STEP = 27


class AeroplaneChess(GamePlugin):
    """Authoritative Chinese Aeroplane Chess with persisted d6 rolls."""

    game_type = "aeroplane_chess"
    display_name = "飞行棋"
    category = "board"
    min_players = 2
    max_players = 4
    allowed_player_counts = (2, 3, 4)
    recommended_players = 4
    supports_npcs = True
    supports_stakes = True
    supports_multiplayer_stakes = True
    mcp_immediate_public_events = True
    rules_text = (
        "【目标】\n"
        "抢先把己方 4 架飞机全部送到中心。标准中国飞行棋支持 2–4 人：2 人使用相对的红、蓝两色，3 人使用红、黄、蓝，4 人使用红、黄、蓝、绿。\n\n"
        "【行动】\n"
        "- 只有掷出 6 才能把一架飞机从机场移到安全起飞区；起飞动作不再额外前进 6 格。\n"
        "- 已经起飞的飞机按骰点沿 52 格公共环线前进。己方飞机可以同格，但每次只移动所选的一架，不组成叠机单位。\n"
        "- 掷出 6 并完成本次动作后继续掷骰；即使没有飞机可动，系统自动跳过移动后仍可续掷。\n\n"
        "【特殊规则】\n"
        "- 连续第三个 6 不允许移动：本轮前两个 6 实际移动过的飞机全部回机场，随后立即结束回合；此前造成的碰撞不撤销。\n"
        "- 落在己方颜色的普通跳跃格会自动前跳 4 格。每色相对路线第 21 格是跨盘飞跃格，直达第 33 格；从前一同色格自动跳到第 21 格时也继续飞跃，飞跃落到第 33 格后不再追加普通跳跃。\n"
        "- 骰点落点、普通跳跃落点、飞跃跨越点和飞跃落点都会结算碰撞；普通环线同格的所有对手机全部回机场。\n"
        "- 机场、起飞区、己方 6 格终点航道与中心都是安全区。绕完环线后进入终点航道：点数刚好时到达中心；点数超出时先到中心，再按超出点数沿终点航道反向退回。\n\n"
        "【胜负】\n"
        "首位让 4 架飞机全部到达中心的玩家获胜。本版不采用偶数起飞、叠机同行等可选规则；终点按上述规则反弹。骰子结果会随局面保存，刷新不会重掷。"
    )
    move_format = (
        '掷骰：{"move":{"action":"roll"},"revision":当前版本}；掷骰后只能从'
        '服务端 legal_actions/legal_moves 选择飞机，例如 '
        '{"move":{"action":"move","plane_id":"red-0","plane_index":0},'
        '"revision":当前版本}。'
    )

    def __init__(self, rng: random.Random | None = None) -> None:
        self._rng = rng or random.SystemRandom()

    @staticmethod
    def _colors_for_count(count: int) -> tuple[str, ...]:
        # The generic framework can persist a one-seat waiting room, although
        # only the declared 2/3/4-player table sizes are playable.
        if count == 1:
            return ("red",)
        if count == 2:
            return ("red", "blue")
        if count == 3:
            return ("red", "yellow", "blue")
        if count == 4:
            return COLORS
        raise ValueError("飞行棋只支持 2、3 或 4 名参与者")

    def tokens_for(self, participants: list[dict[str, Any]]) -> list[str]:
        return list(self._colors_for_count(len(participants)))

    @staticmethod
    def _path_mapping(color: str) -> dict[str, Any]:
        start = START_RING_INDEX[color]
        return {
            "start_ring_index": start,
            "home_entry_ring_index": (start + RING_LENGTH - 1) % RING_LENGTH,
            "ring_indices": [
                (start + offset) % RING_LENGTH for offset in range(RING_LENGTH)
            ],
            "jump_route_steps": sorted(OWN_COLOR_JUMP_STEPS),
            "shortcut": {
                "from_route_step": SHORTCUT_FROM_STEP,
                "to_route_step": SHORTCUT_TO_STEP,
                "cross_route_step": SHORTCUT_CROSS_STEP,
                "from_ring_index": (
                    start + SHORTCUT_FROM_STEP - 1
                ) % RING_LENGTH,
                "to_ring_index": (
                    start + SHORTCUT_TO_STEP - 1
                ) % RING_LENGTH,
                "cross_ring_index": (
                    start + SHORTCUT_CROSS_STEP - 1
                ) % RING_LENGTH,
            },
        }

    @classmethod
    def _new_plane(cls, color: str, plane_index: int) -> dict[str, Any]:
        plane = {
            "plane_id": f"{color}-{plane_index}",
            "plane_index": plane_index,
        }
        cls._set_plane_step(plane, color, -1)
        return plane

    @staticmethod
    def _set_plane_step(
        plane: dict[str, Any], color: str, route_step: int
    ) -> None:
        if (
            isinstance(route_step, bool)
            or not isinstance(route_step, int)
            or not -1 <= route_step <= FINISH_ROUTE_STEP
        ):
            raise ValueError("飞机路线进度越界")
        plane["route_step"] = route_step
        plane["ring_index"] = None
        plane["home_lane_index"] = None
        if route_step == -1:
            plane["zone"] = "airport"
        elif route_step == 0:
            plane["zone"] = "launch"
        elif route_step <= RING_LENGTH:
            plane["zone"] = "track"
            plane["ring_index"] = (
                START_RING_INDEX[color] + route_step - 1
            ) % RING_LENGTH
        elif route_step < FINISH_ROUTE_STEP:
            plane["zone"] = "home_lane"
            plane["home_lane_index"] = route_step - RING_LENGTH
        else:
            plane["zone"] = "home"

    @classmethod
    def _location(cls, color: str, route_step: int) -> dict[str, Any]:
        scratch: dict[str, Any] = {}
        cls._set_plane_step(scratch, color, route_step)
        return {
            key: scratch[key]
            for key in ("zone", "route_step", "ring_index", "home_lane_index")
        }

    @staticmethod
    def _state_skeleton() -> dict[str, Any]:
        state: dict[str, Any] = {
            "board_kind": "aeroplane_chess",
            "participant_order": [],
            "color_by_player": {},
            "player_by_color": {},
            "planes": {},
            "path_mappings": {
                color: AeroplaneChess._path_mapping(color) for color in COLORS
            },
            "ring_length": RING_LENGTH,
            "home_lane_length": HOME_LANE_LENGTH,
            "finish_route_step": FINISH_ROUTE_STEP,
            "dice_rolls": [],
            "last_roll": None,
            "movable_plane_ids": [],
            "legal_actions": [{"action": "roll"}],
            "legal_moves": [],
            "turn_player_id": None,
            "consecutive_sixes": 0,
            "turn_six_move_plane_ids": [],
            "completed_turns": 0,
            "action_history": [],
            "last_action": None,
            "winner_player_id": None,
        }
        ensure_flow(state, phase="awaiting_roll")
        return state

    def initial_state(self) -> dict[str, Any]:
        return self._state_skeleton()

    def initialize(self, participants: list[dict[str, Any]]) -> dict[str, Any]:
        colors = self._colors_for_count(len(participants))
        state = self._state_skeleton()
        for participant, color in zip(participants, colors):
            player_id = str(participant["player_id"])
            token = str(participant.get("token", color))
            if token != color:
                raise ValueError("参与者 token 与飞行棋颜色映射不一致")
            state["participant_order"].append(player_id)
            state["color_by_player"][player_id] = color
            state["player_by_color"][color] = player_id
            state["planes"][player_id] = [
                self._new_plane(color, index) for index in range(4)
            ]
        return state

    @staticmethod
    def _plane_for_identity(
        state: dict[str, Any],
        player_id: str,
        *,
        plane_id: str | None,
        plane_index: int | None,
    ) -> dict[str, Any]:
        planes = state.get("planes", {}).get(player_id)
        if not isinstance(planes, list):
            raise ValueError("行动者不在本局飞机列表中")
        if plane_index is not None and (
            isinstance(plane_index, bool)
            or not isinstance(plane_index, int)
            or not 0 <= plane_index < 4
        ):
            raise ValueError("plane_index 必须是 0–3 的整数")
        candidates = [
            plane for plane in planes
            if (plane_id is None or plane.get("plane_id") == plane_id)
            and (plane_index is None or plane.get("plane_index") == plane_index)
        ]
        if len(candidates) != 1:
            raise ValueError("plane_id/plane_index 没有唯一对应己方飞机")
        return candidates[0]

    @staticmethod
    def _capture_ids_at_ring(
        state: dict[str, Any],
        moving_player_id: str,
        ring_index: int,
        already_captured: set[str] | None = None,
    ) -> list[str]:
        excluded = already_captured or set()
        return [
            str(plane["plane_id"])
            for player_id, planes in state.get("planes", {}).items()
            if player_id != moving_player_id
            for plane in planes
            if plane.get("zone") == "track"
            and plane.get("ring_index") == ring_index
            and plane.get("plane_id") not in excluded
        ]

    @classmethod
    def _movement_for_plane(
        cls,
        state: dict[str, Any],
        player_id: str,
        plane: dict[str, Any],
        die: int,
    ) -> dict[str, Any] | None:
        color = state["color_by_player"][player_id]
        route_step = plane.get("route_step")
        if isinstance(route_step, bool) or not isinstance(route_step, int):
            raise ValueError("飞机缺少有效路线进度")
        if route_step == FINISH_ROUTE_STEP:
            return None
        if route_step == -1:
            if die != 6:
                return None
            raw_step = 0
        else:
            raw_step = route_step + die

        bounce_steps = max(0, raw_step - FINISH_ROUTE_STEP)
        target_step = (
            FINISH_ROUTE_STEP - bounce_steps
            if bounce_steps
            else raw_step
        )
        if bounce_steps:
            landings = [
                {
                    "kind": "dice",
                    "location": cls._location(color, FINISH_ROUTE_STEP),
                },
                {
                    "kind": "bounce",
                    "steps": bounce_steps,
                    "location": cls._location(color, target_step),
                },
            ]
        else:
            landings = [{
                "kind": "takeoff" if route_step == -1 else "dice",
                "location": cls._location(color, target_step),
            }]
        collision_steps: list[tuple[str, int]] = []
        if 1 <= target_step <= RING_LENGTH:
            collision_steps.append(("dice", target_step))

        if target_step in OWN_COLOR_JUMP_STEPS:
            target_step += 4
            landings.append({
                "kind": "jump",
                "location": cls._location(color, target_step),
            })
            collision_steps.append(("jump", target_step))
        if target_step == SHORTCUT_FROM_STEP:
            collision_steps.append(("shortcut_cross", SHORTCUT_CROSS_STEP))
            target_step = SHORTCUT_TO_STEP
            landings.append({
                "kind": "shortcut",
                "location": cls._location(color, target_step),
            })
            collision_steps.append(("shortcut", target_step))

        captures: list[str] = []
        capture_events: list[dict[str, Any]] = []
        captured_set: set[str] = set()
        for kind, step in collision_steps:
            ring_index = int(cls._location(color, step)["ring_index"])
            found = cls._capture_ids_at_ring(
                state, player_id, ring_index, captured_set
            )
            if found:
                captures.extend(found)
                captured_set.update(found)
                capture_events.append({
                    "kind": kind,
                    "ring_index": ring_index,
                    "plane_ids": found,
                })
        return {
            "action": "move",
            "plane_id": str(plane["plane_id"]),
            "plane_index": int(plane["plane_index"]),
            "die": die,
            "from": cls._location(color, route_step),
            "to": cls._location(color, target_step),
            "landings": landings,
            "capture_plane_ids": captures,
            "capture_events": capture_events,
            "bounced": bool(bounce_steps),
            "bounce_steps": bounce_steps,
            "reached_home": target_step == FINISH_ROUTE_STEP,
        }

    @classmethod
    def _legal_moves_for_roll(
        cls, state: dict[str, Any], player_id: str, die: int
    ) -> list[dict[str, Any]]:
        return [
            movement
            for plane in state.get("planes", {}).get(player_id, [])
            if (movement := cls._movement_for_plane(
                state, player_id, plane, die
            )) is not None
        ]

    @staticmethod
    def _move_action(movement: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": "move",
            "plane_id": movement["plane_id"],
            "plane_index": movement["plane_index"],
        }

    @staticmethod
    def _append_history(state: dict[str, Any], action: dict[str, Any]) -> None:
        state["action_history"].append(deepcopy(action))
        state["last_action"] = deepcopy(action)

    @staticmethod
    def _set_awaiting_roll(state: dict[str, Any]) -> None:
        state["flow"]["phase"] = "awaiting_roll"
        state["legal_actions"] = [{"action": "roll"}]
        state["legal_moves"] = []
        state["movable_plane_ids"] = []

    @classmethod
    def _finish_turn(cls, state: dict[str, Any]) -> None:
        cls._set_awaiting_roll(state)
        state["turn_player_id"] = None
        state["consecutive_sixes"] = 0
        state["turn_six_move_plane_ids"] = []
        state["completed_turns"] = int(state.get("completed_turns", 0)) + 1
        player_count = max(1, len(state.get("participant_order", [])))
        state["flow"]["round_number"] = (
            int(state["completed_turns"]) // player_count + 1
        )

    @staticmethod
    def _winner(state: dict[str, Any]) -> str | None:
        for player_id in state.get("participant_order", []):
            planes = state.get("planes", {}).get(player_id, [])
            if len(planes) == 4 and all(
                plane.get("zone") == "home" for plane in planes
            ):
                return str(player_id)
        return None

    def validate_action(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
    ) -> None:
        if not isinstance(move, dict):
            raise ValueError("move 必须是对象")
        if state.get("flow", {}).get("phase") == "finished":
            raise ValueError("对局已经结束")
        player_id = str(actor["player_id"])
        if player_id not in state.get("planes", {}):
            raise ValueError("行动者不在本局参与者中")
        turn_player_id = state.get("turn_player_id")
        if turn_player_id is not None and turn_player_id != player_id:
            raise ValueError("当前骰子与待选飞机属于另一名参与者")

        action = move.get("action")
        phase = state.get("flow", {}).get("phase")
        if action == "roll":
            if set(move) != {"action"}:
                raise ValueError("roll 只接受 action 字段")
            if phase != "awaiting_roll":
                raise ValueError("当前必须先选择本次要移动的飞机")
            if {"action": "roll"} not in state.get("legal_actions", []):
                raise ValueError("服务端当前未发布掷骰行动")
            return
        if action != "move":
            raise ValueError("action 必须是 roll 或 move")
        if phase != "awaiting_plane_choice":
            raise ValueError("当前没有待执行的飞机移动")
        if not set(move) <= {"action", "plane_id", "plane_index"}:
            raise ValueError("move 只接受 action、plane_id 和 plane_index")
        plane_id = move.get("plane_id")
        plane_index = move.get("plane_index")
        if plane_id is None and plane_index is None:
            raise ValueError("move 必须提供 plane_id 或 plane_index")
        if plane_id is not None and not isinstance(plane_id, str):
            raise ValueError("plane_id 必须是字符串")
        plane = self._plane_for_identity(
            state,
            player_id,
            plane_id=plane_id,
            plane_index=plane_index,
        )
        if not any(
            action_item.get("plane_id") == plane["plane_id"]
            and action_item.get("plane_index") == plane["plane_index"]
            for action_item in state.get("legal_actions", [])
        ):
            raise ValueError("该飞机不在服务端本次发布的 legal_actions 中")

    def validate_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> None:
        del state, move, mark
        raise ValueError("飞行棋需要 participant-aware action 接口")

    def apply_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> dict[str, Any]:
        del state, move, mark
        raise ValueError("飞行棋需要 participant-aware action 接口")

    def _apply_roll(
        self,
        state: dict[str, Any],
        actor: dict[str, Any],
    ) -> MoveResult:
        player_id = str(actor["player_id"])
        record = roll_dice(
            state,
            roller_player_id=player_id,
            count=1,
            sides=6,
            key="dice_rolls",
            rng=self._rng,
        )
        value = record["values"][0]
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 1 <= value <= 6
        ):
            raise ValueError("骰子随机源必须返回 1–6 的整数")
        state["turn_player_id"] = player_id
        state["flow"]["turn_number"] = len(state["dice_rolls"])
        state["consecutive_sixes"] = (
            int(state.get("consecutive_sixes", 0)) + 1 if value == 6 else 0
        )
        record.update({
            "value": value,
            "consecutive_sixes": state["consecutive_sixes"],
        })
        state["last_roll"] = deepcopy(record)

        roll_action: dict[str, Any] = {
            "action": "roll",
            "player_id": player_id,
            "color": state["color_by_player"][player_id],
            "value": value,
            "roll_sequence": record["sequence"],
            "consecutive_sixes": state["consecutive_sixes"],
            "auto_pass": False,
            "returned_plane_ids": [],
        }

        if state["consecutive_sixes"] >= 3:
            moved_ids = list(dict.fromkeys(
                state.get("turn_six_move_plane_ids", [])
            ))
            returned: list[str] = []
            for plane_id in moved_ids:
                for owner_id, planes in state["planes"].items():
                    plane = next(
                        (item for item in planes if item["plane_id"] == plane_id),
                        None,
                    )
                    if plane is not None:
                        self._set_plane_step(
                            plane, state["color_by_player"][owner_id], -1
                        )
                        returned.append(plane_id)
                        break
            roll_action.update({
                "auto_pass": True,
                "penalty": "third_consecutive_six",
                "returned_plane_ids": returned,
            })
            record["penalty"] = "third_consecutive_six"
            record["returned_plane_ids"] = list(returned)
            self._append_history(state, roll_action)
            self._finish_turn(state)
            returned_copy = "、".join(returned) if returned else "无"
            return MoveResult(
                state=state,
                note=(
                    f"连续第三个 6：本次不能移动，本轮前两个 6 移动过的飞机"
                    f"（{returned_copy}）回机场，回合结束。"
                ),
            )

        legal_moves = self._legal_moves_for_roll(state, player_id, value)
        state["legal_moves"] = legal_moves
        state["legal_actions"] = [
            self._move_action(movement) for movement in legal_moves
        ]
        state["movable_plane_ids"] = [
            movement["plane_id"] for movement in legal_moves
        ]
        if legal_moves:
            state["flow"]["phase"] = "awaiting_plane_choice"
            roll_action["movable_plane_ids"] = list(state["movable_plane_ids"])
            self._append_history(state, roll_action)
            return MoveResult(
                state=state,
                retain_turn=True,
                note=f"掷出 {value} 点，请从 {len(legal_moves)} 架可移动飞机中选择。",
            )

        roll_action["auto_pass"] = True
        self._append_history(state, roll_action)
        if value == 6:
            self._set_awaiting_roll(state)
            return MoveResult(
                state=state,
                retain_turn=True,
                note="掷出 6 点但没有可移动飞机，服务端已自动跳过移动并保留续掷。",
            )
        self._finish_turn(state)
        return MoveResult(
            state=state,
            note=f"掷出 {value} 点但没有可移动飞机，服务端已自动结束本回合。",
        )

    def _apply_plane_move(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
    ) -> MoveResult:
        player_id = str(actor["player_id"])
        plane = self._plane_for_identity(
            state,
            player_id,
            plane_id=move.get("plane_id"),
            plane_index=move.get("plane_index"),
        )
        die = int(state["last_roll"]["value"])
        movement = self._movement_for_plane(state, player_id, plane, die)
        if movement is None:
            raise ValueError("该飞机在当前骰点下不可移动")
        color = state["color_by_player"][player_id]

        returned: list[str] = []
        for captured_id in movement["capture_plane_ids"]:
            for owner_id, planes in state["planes"].items():
                captured = next(
                    (
                        item for item in planes
                        if item.get("plane_id") == captured_id
                        and item.get("zone") == "track"
                    ),
                    None,
                )
                if captured is not None:
                    self._set_plane_step(
                        captured, state["color_by_player"][owner_id], -1
                    )
                    returned.append(captured_id)
                    break
        self._set_plane_step(plane, color, movement["to"]["route_step"])
        if die == 6:
            state["turn_six_move_plane_ids"].append(plane["plane_id"])

        action = {
            "action": "move",
            "player_id": player_id,
            "color": color,
            "plane_id": plane["plane_id"],
            "plane_index": plane["plane_index"],
            "die": die,
            "from": movement["from"],
            "to": movement["to"],
            "landings": movement["landings"],
            "capture_events": movement["capture_events"],
            "captured_plane_ids": returned,
            "returned_plane_ids": returned,
            "bounced": movement["bounced"],
            "bounce_steps": movement["bounce_steps"],
            "reached_home": movement["reached_home"],
        }
        self._append_history(state, action)

        winner = self._winner(state)
        actor_name = str(
            actor.get("display_name") or COLOR_LABELS[color]
        ).strip() or COLOR_LABELS[color]
        arrival_note = (
            f"{actor_name}的 {plane['plane_index'] + 1} 号机到达终点"
        )
        if winner is not None:
            state["winner_player_id"] = winner
            state["flow"]["phase"] = "finished"
            state["legal_actions"] = []
            state["legal_moves"] = []
            state["movable_plane_ids"] = []
            state["turn_player_id"] = None
            return MoveResult(
                state=state,
                note=f"{arrival_note}，4 架飞机全部到家，赢得本局。",
                result={"winner_player_id": winner, "draw": False},
            )

        effects: list[str] = []
        kinds = [item["kind"] for item in movement["landings"]]
        if "jump" in kinds:
            effects.append("同色跳跃")
        if "shortcut" in kinds:
            effects.append("跨盘飞跃")
        if returned:
            effects.append(f"击落 {len(returned)} 架")
        suffix = f"，{'、'.join(effects)}" if effects else ""
        if movement["reached_home"]:
            note = f"{arrival_note}。"
        elif movement["bounced"]:
            note = (
                f"{COLOR_LABELS[color]} {plane['plane_index'] + 1} 号机"
                f"掷出 {die} 点，到达中心后反弹 {movement['bounce_steps']} 格，"
                f"停在终点航道第 {movement['to']['home_lane_index']} 格。"
            )
        else:
            note = (
                f"{COLOR_LABELS[color]} {plane['plane_index'] + 1} 号机"
                f"前进 {die} 点{suffix}。"
            )
        if die == 6:
            self._set_awaiting_roll(state)
            return MoveResult(state=state, retain_turn=True, note=note + " 可继续掷骰。")
        self._finish_turn(state)
        return MoveResult(state=state, note=note)

    def apply_action(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
    ) -> MoveResult:
        self.validate_action(state, move, actor)
        if move["action"] == "roll":
            return self._apply_roll(state, actor)
        return self._apply_plane_move(state, move, actor)

    def progress_after_action(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
        participants: list[dict[str, Any]],
        applied: dict[str, Any] | MoveResult,
    ) -> dict[str, Any] | MoveResult:
        del state, move, actor, participants
        if not isinstance(applied, MoveResult):
            return applied
        action = applied.state.get("last_action")
        if not isinstance(action, dict):
            return applied
        if action.get("action") == "roll":
            keys = (
                "action", "color", "value", "consecutive_sixes",
                "auto_pass", "movable_plane_ids", "penalty",
                "returned_plane_ids",
            )
        else:
            keys = (
                "action", "color", "plane_id", "plane_index", "die",
                "from", "to", "landings", "capture_events",
                "captured_plane_ids", "returned_plane_ids", "bounced",
                "bounce_steps", "reached_home",
            )
        applied.public_event = {
            "aeroplane_delta": {
                key: deepcopy(action[key]) for key in keys if key in action
            }
        }
        return applied

    def check_winner(self, state: dict[str, Any]) -> str | None:
        winner = self._winner(state)
        return state.get("color_by_player", {}).get(winner) if winner else None

    def result_for(
        self,
        state: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        del participants
        winner = self._winner(state)
        return (
            {"winner_player_id": winner, "draw": False}
            if winner is not None else None
        )

    def settlement_deltas(
        self,
        state: dict[str, Any],
        result: dict[str, Any],
        participants: list[dict[str, Any]],
        stake: int,
    ) -> dict[str, int]:
        del state
        player_ids = [str(item["player_id"]) for item in participants]
        winner = result.get("winner_player_id")
        if winner not in player_ids or result.get("draw"):
            raise ValueError("飞行棋终局必须有一名有效赢家")
        return {
            player_id: stake * (len(player_ids) - 1)
            if player_id == winner else -stake
            for player_id in player_ids
        }

    def participant_summary(
        self,
        state: dict[str, Any],
        participant: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, str | int]:
        del participants
        player_id = str(participant["player_id"])
        planes = state.get("planes", {}).get(player_id, [])
        return {
            "color": state.get("color_by_player", {}).get(player_id, ""),
            "home": sum(plane.get("zone") == "home" for plane in planes),
            "airborne": sum(
                plane.get("zone") in {"launch", "track", "home_lane"}
                for plane in planes
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
        # Route geometry is immutable bootstrap data; plane route_step and the
        # current authoritative actions fully describe the live position.
        for key in (
            "path_mappings", "ring_length", "home_lane_length",
            "finish_route_step",
        ):
            snapshot.pop(key, None)
        return snapshot

    def npc_compact_rules(
        self,
        state: dict[str, Any],
        actor: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> str:
        del state, actor, participants
        return (
            "标准飞行棋：6 才能起飞且移动后续掷，第三个连续 6 由服务端自动惩罚；"
            "环线碰撞会送回全部同格对手机，同色格与跨盘飞跃自动结算；"
            "终点点数超出时先到中心，再按超出点数沿终点航道反向退回。"
            "不要推导规则或构造动作，只能原样选择服务端 authoritative legal_actions。"
        )

    def npc_public_actions(
        self,
        state: dict[str, Any],
        actor: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        del actor, participants
        return deepcopy(state.get("action_history", [])[-24:])

    def npc_legal_actions(
        self,
        state: dict[str, Any],
        actor: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        del participants
        player_id = str(actor["player_id"])
        if player_id not in state.get("planes", {}):
            return []
        if state.get("flow", {}).get("phase") == "finished":
            return []
        if state.get("turn_player_id") not in {None, player_id}:
            return []
        return deepcopy(state.get("legal_actions", []))

    def format_action(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
    ) -> str:
        if move.get("action") == "roll":
            return "掷骰"
        plane = self._plane_for_identity(
            state,
            str(actor["player_id"]),
            plane_id=move.get("plane_id"),
            plane_index=move.get("plane_index"),
        )
        return f"移动 {plane['plane_index'] + 1} 号机"

    def format_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> str:
        del mark
        return str(move.get("action", "move"))
