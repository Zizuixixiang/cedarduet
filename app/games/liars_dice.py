from __future__ import annotations

import random
from copy import deepcopy
from typing import Any

from .base import GamePlugin, MoveResult
from .tools import advance_flow, ensure_flow
from . import liars_dice_party as party


class LiarsDice(GamePlugin):
    game_type = "liars_dice"
    display_name = "吹牛骰子"
    category = "dice"
    min_players = 2
    max_players = 6
    allowed_player_counts = (2, 3, 4, 5, 6)
    recommended_players = 4
    supports_npcs = True
    supports_stakes = True
    supports_multiplayer_stakes = True
    mcp_immediate_public_events = True
    rules_text = (
        "【目标】\n"
        "通过叫点和质疑让其他玩家失去全部骰子，成为最后留下的人。\n\n"
        "【行动】\n"
        "每人初始 5 枚六面骰，每轮只看自己的当前骰子；本版 1 点不作万能点。首位行动者先叫点，之后的叫点必须数量更大，或数量相同而点数更大；叫点数量不能超过场上当前骰子总数。\n\n"
        "【特殊规则】\n"
        "- 除首叫外，行动者可以质疑上一手。质疑后公开本轮全部骰子。\n"
        "- 实际数量达到上一手叫点时，质疑者失去一枚骰；否则上一位叫点者失去一枚骰。\n"
        "- 失骰者若仍存活，由其开启下一轮；若已淘汰，则由其后下一位存活者开启。非终局时，确认本轮结算后才会重掷，公开区会保留上一轮结果。\n\n"
        "【胜负】\n"
        "失去全部骰子即淘汰，最后一名仍有骰子的玩家获胜。"
    )
    move_format = (
        '叫点：{"move":{"action":"bid","quantity":3,"face":4}}；'
        '质疑：{"move":{"action":"challenge"}}。'
    )

    party_rules = (
        "【酒桌大话骰】每盅始终5骰，基础败方本局-1，胜方不加分；不淘汰、0平台筹码。普通2–6点时1百搭，斋不百搭。"
        "普通起叫按ceil(1.5×盅数)，5人默认8起（向上取整，可在房规改7）；斋/叫1至少盅数。"
        "加叫数量递增或同数点数递增；叫斋/1后本轮锁斋，切斋也须加叫，不实现飞斋。\n"
        "默认围骰：5骰借1凑所叫实点计6；全围：5骰同一实点且正叫此点计7。全1叫2–6仅计5。"
        "斋只允许同实点全围+2，不借1；叫数上限每盅7（有骰计骰为5），上限不依赖隐藏骰面。"
        "首叫前当前行动者若5骰各异可reroll，每盅每轮限1次，不公开骰面；组队共用限次。"
        "有骰计骰(literal_dice)关闭上述加数及单骰重摇，但普通1仍百搭。\n"
        "开骰足数则质疑者败，否则叫点者败。可选cup_penalty：被开骰者持围骰/全围且败，罚×2；"
        "牌型不必等于叫点，有骰计骰也不屏蔽此独立处罚。extreme_penalty：实际严格少于叫数一半且叫点者败、"
        "或严格超过两倍且质疑者败，罚×2，等于边界不加倍。dark_rule：无人持叫点实点则反转开骰输赢，"
        "1百搭不算实点；先反转，再按最终败方判断上述处罚。\n"
        "companion_penalty（陪扣）：额外虚拟d6按固定参与者顺序1起映射，越界重掷，选中其他单位同倍率陪扣；"
        "选本人/同队则本单位再×2；队分只扣一次。所有命中乘算后，每个受罚人/队每轮最多4倍、4分；"
        "结算记录命中、原始倍率、封顶与扣分。allow_surrender：已有叫点时当前行动者可surrender，固定认罚0.5，"
        "不触发开骰加倍/反转/陪扣。surrender_no_reroll须同时开投降：不揭骰且确认后保留各盅；否则揭盅重摇。"
        "以上处罚/投降选项默认关闭，积分以整数半分单位存储。\n"
        "败方先叫，当前行动者acknowledge_round后开始下一轮。任一真实在场参与者可end_session结束整桌；"
        "离场/房间认输也结束，不结算未完成轮次。不涉及实际饮酒、真钱或平台币扣款。"
    )

    pairs_rules = (
        "\n【可选红蓝组队】仅4/6人，固定红、蓝两队，分别2+2或3+3。"
        "每队共用一盅5骰，队友可见己队骰，计数只计每队一次。"
        "按队轮流叫点，队内成员轮换发言，只有当前指定成员可行动。"
        "首叫按两盅计算：普通3起，斋/叫1为2起，总数上限14，有骰计骰10。"
        "败队基础共用本局积分-1，队员显示同一队分，不叠加；赢家不加分。"
        "随机组队直接开始；手动组队只允许本人选空位/换位，20秒后房主可开始。"
        "未选成员和开始时补入的NPC随机分到剩余空位。"
        "确认下一轮后各队重摇，败方发言者先叫；成员离场结束整桌。不启用接力淘汰。"
    )

    classic_rules = rules_text
    rules_text = ("默认原版·淘汰赛；new/invite 可用 move={mode:party,literal_dice:false} 选择酒桌版。\n\n"
                  + classic_rules + "\n\n" + party_rules + pairs_rules)
    move_format += (' 酒桌版叫斋附 exact:true；叫1自动锁斋；'
                    '等待揭盅确认时当前行动者 move={action:acknowledge_round}；'
                    '任一真实在场参与者可 move={action:end_session} 结束酒桌局；均带 revision。')

    @staticmethod
    def is_party(state):
        return state.get("mode", "classic") == "party"

    @classmethod
    def normalize_options(cls, options=None, stake=0):
        if options is None:
            options = {}
        if not isinstance(options, dict) or set(options) - set(party.OPTION_KEYS):
            raise ValueError("未知吹牛骰子房规；请按需查看 guide")
        mode = options.get("mode", "classic")
        literal = options.get("literal_dice", False)
        teams = options.get("team_mode", "none")
        if teams == "pairs":  # Earlier draft requests remain accepted.
            teams = "teams"
        if teams not in ("none", "teams"):
            raise ValueError("team_mode 仅支持 none / teams（红蓝两队）")
        if mode not in ("classic", "party") or not isinstance(literal, bool):
            raise ValueError("mode 须为 classic/party，literal_dice 须为布尔值")
        for key in party.BOOL_OPTIONS:
            if not isinstance(options.get(key, False), bool):
                raise ValueError(f"{key} 须为布尔值")
        opening = options.get("opening_five", 8)
        if type(opening) is not int or opening not in (7, 8):
            raise ValueError("opening_five 仅支持7或8")
        if options.get("surrender_no_reroll") and not options.get("allow_surrender"):
            raise ValueError("投降不重摇须同时开启允许投降")
        if mode == "classic" and (any(options.get(k) for k in party.BOOL_OPTIONS) or teams != "none" or "opening_five" in options):
            raise ValueError("有骰计骰和组队仅适用于酒桌版")
        if mode == "party" and stake != 0:
            raise ValueError("酒桌大话骰仅支持0筹码")
        random_teams = options.get("random_teams", True)
        if not isinstance(random_teams, bool):
            raise ValueError("random_teams 须为布尔值")
        if teams == "none" and "random_teams" in options:
            raise ValueError("random_teams 仅用于酒桌组队")
        return {"mode": mode, "literal_dice": literal,
                **({k: options.get(k, False) for k in party.BOOL_OPTIONS if k != "literal_dice"} if mode == "party" else {}),
                **({"opening_five": opening} if mode == "party" else {}),
                **({"team_mode": teams, "random_teams": random_teams} if teams != "none" else {})}

    def configure(self, state, options, teams=None):
        state.update(self.normalize_options(options))
        if self.is_party(state):
            state["party_rules_version"] = 2
            state["rerolled_cups"] = {}
            state["records"] = {pid: {"wins": 0, "losses": 0} for pid in state["participant_order"]}
            state["round_results"] = []
            state["scores"] = {pid: 0 for pid in state["participant_order"]}
            if state.get("team_mode") == "teams":
                order = state["participant_order"]
                self.validate_player_count(state, len(order))
                if teams is None:
                    shuffled = list(order)
                    random.SystemRandom().shuffle(shuffled)
                    half = len(order) // 2
                    teams = {"red": shuffled[:half], "blue": shuffled[half:]}
                state["teams"] = deepcopy(teams)
                state["team_scores"] = {team: 0 for team in state["teams"]}
                state["team_next_speakers"] = {team: members[0] for team, members in state["teams"].items()}
                # Store exactly one cup per team. No duplicate dice to double-count or drift.
                state["dice_by_player"] = {members[0]: state["dice_by_player"][members[0]]
                                           for members in state["teams"].values()}
        return state

    def room_rules(self, state):
        if not self.is_party(state):
            return "【原版·淘汰赛】\n" + self.classic_rules
        return self.party_rules + ("\n旧酒桌房保留不加围骰数量、不重摇单骰的基础计数；普通1百搭。" if "dice_counts" in state and not party.modern(state) else "") + (self.pairs_rules if state.get("teams") or state.get("team_mode") in ("pairs", "teams") else "") + ("\n本桌有骰计骰：已开启。" if state.get("literal_dice") else "\n本桌有骰计骰：关闭。")

    @staticmethod
    def saved_options(state):
        return {key: state[key] for key in party.OPTION_KEYS if key in state}

    @staticmethod
    def validate_player_count(options, count):
        if options.get("team_mode") in ("pairs", "teams") and count not in (4, 6):
            raise ValueError("红蓝两队仅支持4或6人，每队共用5骰")

    @staticmethod
    def team_for(state, player_id):
        return next((team for team, members in state.get("teams", {}).items() if player_id in members), None)

    def cup_owner(self, state, player_id):
        team = self.team_for(state, player_id)
        return state["teams"][team][0] if team else player_id

    @staticmethod
    def total_dice(state):
        cups = len(state["teams"]) if state.get("teams") else len(state["dice_counts"])
        if state.get("mode") == "party" and party.modern(state) and not state.get("literal_dice"):
            return 7 * cups  # Public upper bound, never derived from hidden actual cups.
        return 5 * len(state["teams"]) if state.get("teams") else sum(state["dice_counts"].values())

    @staticmethod
    def party_scores(state):
        # Upgrade an earlier unscored party save from its persisted losses, without
        # changing classic saves or ever touching platform chip accounting.
        if state.get("teams"):
            return {pid: state["team_scores"][team] for team, members in state["teams"].items() for pid in members}
        return deepcopy(state.get("scores", {pid: -state.get("records", {}).get(pid, {}).get("losses", 0)
                                             for pid in state.get("participant_order", [])}))

    def _next_team_speaker(self, state, player_id):
        team = self.team_for(state, player_id)
        members = state["teams"][team]
        state["team_next_speakers"][team] = members[(members.index(player_id) + 1) % len(members)]
        order = list(state["teams"])
        next_team = order[(order.index(team) + 1) % len(order)]
        return state["team_next_speakers"][next_team]

    def _finish_party(self, state, reason="session_ended"):
        state["scores"] = self.party_scores(state)
        state["flow"]["phase"] = "finished"
        state["pending_next_round"] = None
        state["current_bid"] = None
        state["turn_player_id"] = None
        state["session_result"] = {"draw": True, "mode": "party", "reason": reason,
                                   "result_text": "酒桌局已结束，本局积分和回合记录已保留。",
                                   "scores": deepcopy(state["scores"]),
                                   "records": deepcopy(state["records"])}
        if state.get("teams"):
            state["session_result"]["team_scores"] = deepcopy(state["team_scores"])
        return MoveResult(state=state, note="酒桌局已结束，保留本局积分和回合记录。",
                          result=deepcopy(state["session_result"]))

    def _is_exact(self, state, face, exact=False):
        return bool(exact or face == 1 or (state.get("current_bid") or {}).get("exact"))

    def _valid_bid(self, state, quantity, face, exact=False):
        if not (1 <= quantity <= self.total_dice(state) and 1 <= face <= 6):
            return False
        if not self._bid_is_higher(state.get("current_bid"), quantity, face):
            return False
        if self.is_party(state) and state.get("current_bid") is None:
            count = len(state["teams"]) if state.get("teams") else len(self._active_ids(state))
            normal = state.get("opening_five", 8) if count == 5 else (3 * count + 1) // 2
            minimum = count if self._is_exact(state, face, exact) else normal
            return quantity >= minimum
        return True

    def accepts_out_of_turn_action(self, state, move, player_id):
        return (self.is_party(state) and move == {"action": "end_session"}
                and player_id in self._active_ids(state))

    def __init__(self, rng: random.Random | None = None) -> None:
        # Tests inject random.Random(seed) or a randint-compatible fixture.
        self._rng = rng or random.SystemRandom()

    @staticmethod
    def _state_skeleton() -> dict[str, Any]:
        state: dict[str, Any] = {
            "board_kind": "liars_dice",
            "participant_order": [],
            "dice_by_player": {},
            "dice_counts": {},
            "current_bid": None,
            "round_actions": [],
            "action_history": [],
            "eliminated_player_ids": [],
            "last_round_result": None,
            "pending_next_round": None,
            "winner_player_id": None,
        }
        ensure_flow(state, phase="bidding")
        return state

    def initial_state(self) -> dict[str, Any]:
        return self._state_skeleton()

    def _roll(self, count: int) -> list[int]:
        values = [self._rng.randint(1, 6) for _ in range(count)]
        if len(values) != count or any(
            isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 6
            for value in values
        ):
            raise ValueError("骰子随机源必须返回 1–6 的整数")
        return values

    def initialize(self, participants: list[dict[str, Any]]) -> dict[str, Any]:
        state = self._state_skeleton()
        order = [str(item["player_id"]) for item in participants]
        state["participant_order"] = order
        state["dice_counts"] = {player_id: 5 for player_id in order}
        state["dice_by_player"] = {
            player_id: self._roll(5) for player_id in order
        }
        return state

    @staticmethod
    def _active_ids(state: dict[str, Any]) -> list[str]:
        return [
            player_id for player_id in state["participant_order"]
            if state["dice_counts"].get(player_id, 0) > 0
        ]

    @staticmethod
    def _next_survivor_after(state: dict[str, Any], player_id: str) -> str:
        order = state["participant_order"]
        start = order.index(player_id)
        for offset in range(1, len(order) + 1):
            candidate = order[(start + offset) % len(order)]
            if state["dice_counts"].get(candidate, 0) > 0:
                return candidate
        raise ValueError("没有下一名存活参与者")

    @staticmethod
    def _bid_is_higher(
        current_bid: dict[str, Any] | None, quantity: int, face: int
    ) -> bool:
        if current_bid is None:
            return True
        return quantity > current_bid["quantity"] or (
            quantity == current_bid["quantity"] and face > current_bid["face"]
        )

    @staticmethod
    def _parse_bid(move: dict[str, Any]) -> tuple[int, int]:
        quantity, face = move.get("quantity"), move.get("face")
        if (
            isinstance(quantity, bool) or not isinstance(quantity, int)
            or isinstance(face, bool) or not isinstance(face, int)
        ):
            raise ValueError("quantity 和 face 必须是整数")
        return quantity, face

    def validate_action(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
    ) -> None:
        if move == {"action": "end_session"} and self.is_party(state):
            if (actor.get("player_id") not in self._active_ids(state)
                    or actor.get("participant_kind") == "system_npc" or state["flow"]["phase"] == "finished"):
                raise ValueError("只有真实在场参与者可结束酒桌局")
            return
        if state.get("flow", {}).get("phase") != "bidding":
            raise ValueError("当前阶段不能叫点或质疑")
        player_id = str(actor["player_id"])
        if state["dice_counts"].get(player_id, 0) <= 0:
            raise ValueError("已淘汰参与者不能行动")
        action = move.get("action")
        if self.is_party(state) and action in ("reroll", "surrender"):
            if set(move) != {"action"}:
                raise ValueError("只能操作本人，动作仅接受 action 字段")
            if action == "reroll":
                if not party.can_reroll(state, self.cup_owner(state, player_id)):
                    raise ValueError("仅本轮首叫前单骰可重摇一次；有骰计骰时禁用")
            elif not state.get("allow_surrender") or not state.get("current_bid"):
                raise ValueError("本桌未允许投降或尚无叫点")
            elif self.cup_owner(state, state["current_bid"]["bidder_player_id"]) == self.cup_owner(state, player_id):
                raise ValueError("不能向自己或队友投降")
            return
        if action == "challenge":
            if set(move) != {"action"}:
                raise ValueError("challenge 只接受 action 字段")
            bid = state.get("current_bid")
            if bid is None:
                raise ValueError("首叫之前不能质疑")
            if self.cup_owner(state, bid.get("bidder_player_id")) == self.cup_owner(state, player_id):
                raise ValueError("不能质疑自己或队友的叫点")
            return
        if action != "bid":
            raise ValueError("action 必须是 bid 或 challenge")
        if not self.is_party(state):
            if set(move) != {"action", "quantity", "face"}:
                raise ValueError("bid 只接受 action、quantity、face 字段")
            quantity, face = self._parse_bid(move)
            max_quantity = sum(state["dice_counts"].values())
            if not 1 <= quantity <= max_quantity:
                raise ValueError(f"quantity 必须在 1–{max_quantity} 之间")
            if not 1 <= face <= 6:
                raise ValueError("face 必须在 1–6 之间；1 点不作万能点")
            if not self._bid_is_higher(state.get("current_bid"), quantity, face):
                raise ValueError("叫点必须比上一手更高")
            return
        required = {"action", "quantity", "face"}
        allowed = required | ({"exact"} if self.is_party(state) else set())
        if not required <= set(move) <= allowed or not isinstance(move.get("exact", False), bool):
            raise ValueError("bid 接受 action、quantity、face；酒桌版可附布尔 exact（叫斋）")
        quantity, face = self._parse_bid(move)
        if not self._valid_bid(state, quantity, face, move.get("exact", False)):
            raise ValueError("叫点须符合起叫门槛、骰数上限，并比上一手更高")

    def validate_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> None:
        del state, move, mark
        raise ValueError("吹牛骰子需要 participant-aware action 接口")

    def apply_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> dict[str, Any]:
        del state, move, mark
        raise ValueError("吹牛骰子需要 participant-aware action 接口")

    def _apply_bid(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
    ) -> MoveResult:
        quantity, face = self._parse_bid(move)
        bid = {
            "quantity": quantity,
            "face": face,
            "bidder_player_id": str(actor["player_id"]),
        }
        if self.is_party(state):
            bid["exact"] = self._is_exact(state, face, move.get("exact", False))
        state["current_bid"] = bid
        record = {
            "round": state["flow"]["round_number"],
            "action": "bid",
            **bid,
        }
        state["round_actions"].append(record)
        state["action_history"].append(deepcopy(record))
        advance_flow(state)
        return MoveResult(state=state, note=f"叫 {quantity} 个 {face} 点。",
                          next_player_id=self._next_team_speaker(state, str(actor["player_id"])) if state.get("teams") else None)

    def _apply_challenge(
        self,
        state: dict[str, Any],
        actor: dict[str, Any],
    ) -> MoveResult:
        flow = state["flow"]
        flow["phase"] = "revealing"
        bid = deepcopy(state["current_bid"])
        challenger = str(actor["player_id"])
        bidder = str(bid["bidder_player_id"])
        revealed = deepcopy(state["dice_by_player"])
        bonuses = {}
        dark = False
        if self.is_party(state):
            actual_count, bonuses = party.count_bid(state, bid)
            dark = bool(state.get("dark_rule") and not any(bid["face"] in dice for dice in revealed.values()))
        else:
            actual_count = sum(value == bid["face"] for dice in revealed.values() for value in dice)
        bid_holds = actual_count >= bid["quantity"]
        loser = challenger if bid_holds else bidder
        winner = bidder if bid_holds else challenger
        if dark:
            loser, winner = winner, loser
        settlement = {}
        if self.is_party(state):
            hits = []
            if state.get("cup_penalty") and loser == bidder and party.cup_kind(revealed[self.cup_owner(state, bidder)]):
                hits.append("cup_loser")
            if state.get("extreme_penalty"):
                if loser == bidder and actual_count * 2 < bid["quantity"]:
                    hits.append("under_half")
                elif loser == challenger and actual_count > bid["quantity"] * 2:
                    hits.append("over_double")
            settlement = party.settle(self, state, loser, winner, hits=hits)
        else:
            state["dice_counts"][loser] -= 1
        loser_remaining = state["dice_counts"][loser]
        eliminated = loser_remaining == 0
        if eliminated:
            state["dice_by_player"][loser] = []
            state["eliminated_player_ids"].append(loser)
        result = {
            "phase": "revealed",
            "round": flow["round_number"],
            "bid": bid,
            "bidder_player_id": bidder,
            "challenger_player_id": challenger,
            "actual_count": actual_count,
            "bid_holds": bid_holds,
            "loser_player_id": loser,
            "loser_remaining_dice": loser_remaining,
            "eliminated": eliminated,
            "eliminated_player_id": loser if eliminated else None,
            "next_round": None,
            "next_starter_player_id": None,
            "revealed_dice_by_player": revealed,
        }
        if self.is_party(state):
            result.update(mode="party", winner_player_id=winner,
                          literal_dice=state.get("literal_dice", False),
                          cup_bonuses=bonuses, dark_reversed=dark, **settlement)
            state["round_results"].append(party.history_result(result))
        state["last_round_result"] = result
        state["current_bid"] = None
        record = {
            "round": flow["round_number"],
            "action": "challenge",
            "challenger_player_id": challenger,
            "bidder_player_id": bidder,
            "actual_count": actual_count,
            "bid_holds": bid_holds,
            "loser_player_id": loser,
        }
        state["action_history"].append(record)
        state["round_actions"] = []
        active_ids = self._active_ids(state)
        activity = {loser: "eliminated"} if eliminated else {}
        if len(active_ids) == 1:
            winner = active_ids[0]
            flow["phase"] = "finished"
            state["pending_next_round"] = None
            state["winner_player_id"] = winner
            return MoveResult(
                state=state,
                note=f"质疑结算：实际有 {actual_count} 个 {bid['face']} 点；比赛结束。",
                participant_activity=activity,
                result={"winner_player_id": winner, "draw": False},
            )

        starter = loser if loser_remaining > 0 else self._next_survivor_after(state, loser)
        next_round = flow["round_number"] + 1
        result["next_round"] = next_round
        result["next_starter_player_id"] = starter
        flow["phase"] = "awaiting_round_acknowledgement"
        state["pending_next_round"] = {
            "round_number": next_round,
            "starter_player_id": starter,
        }
        return MoveResult(
            state=state,
            pause_turn=True,
            participant_activity=activity,
            note=(
                f"质疑结算：实际有 {actual_count} 个 {bid['face']} 点；"
                + (f"败方本局积分{result['score_delta']:g}，等待确认下一轮。" if self.is_party(state)
                 else f"{loser} 失去 1 枚骰，等待人类确认下一轮。")
            ),
        )

    def _surrender(self, state, actor):
        bid = deepcopy(state['current_bid'])
        loser = str(actor['player_id'])
        winner = bid['bidder_player_id']
        keep = bool(state.get('surrender_no_reroll'))
        number = state['flow']['round_number']
        result = dict(mode='party', phase='surrendered', round=number, bid=bid,
                      bidder_player_id=winner, challenger_player_id=loser,
                      loser_player_id=loser, winner_player_id=winner,
                      actual_count=None, bid_holds=None, eliminated=False,
                      loser_remaining_dice=5, preserve_dice=keep,
                      next_round=number + 1, next_starter_player_id=loser,
                      revealed_dice_by_player={} if keep else deepcopy(state['dice_by_player']),
                      **party.settle(self, state, loser, winner, surrender=True))
        result['result_summary'] = '本轮投降认罚0.5分；' + ('不揭骰，下一轮保留骰面。' if keep else '揭盅，确认后各盅重摇。')
        state['last_round_result'] = result
        state['round_results'].append(party.history_result(result))
        state['current_bid'] = None
        state['round_actions'] = []
        state['pending_next_round'] = {'round_number':number + 1, 'starter_player_id':loser, 'preserve_dice':keep}
        state['flow']['phase'] = 'awaiting_round_acknowledgement'
        return MoveResult(state=state, pause_turn=True, note=result['result_summary'],
                          public_event={'round_result':deepcopy(result)})

    def acknowledge_round(self, state: dict[str, Any]) -> MoveResult:
        flow = state.get("flow")
        if not isinstance(flow, dict) or flow.get("phase") != "awaiting_round_acknowledgement":
            raise ValueError("当前没有等待确认的轮次结算")
        pending = state.get("pending_next_round")
        if not isinstance(pending, dict):
            raise ValueError("下一轮信息缺失")
        next_round = pending.get("round_number")
        starter = pending.get("starter_player_id")
        if (
            isinstance(next_round, bool)
            or not isinstance(next_round, int)
            or next_round != int(flow.get("round_number", 0)) + 1
        ):
            raise ValueError("下一轮编号无效")
        if starter not in self._active_ids(state):
            raise ValueError("下一轮首位行动者无效")
        cup_owners = list(state["dice_by_player"]) if state.get("teams") else state["participant_order"]
        if not pending.get("preserve_dice"):
            for player_id in cup_owners:
                count = state["dice_counts"][player_id]
                state["dice_by_player"][player_id] = self._roll(count) if count else []
        if self.is_party(state):
            state["rerolled_cups"] = {}
            state["dice_retained"] = bool(pending.get("preserve_dice"))
        state["current_bid"] = None
        state["round_actions"] = []
        state["pending_next_round"] = None
        advance_flow(state, phase="bidding", next_round=True)
        return MoveResult(
            state=state,
            next_player_id=str(starter),
            note=f"已确认结算，第 {next_round} 轮开始。",
        )

    def apply_action(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
    ) -> MoveResult:
        if move["action"] == "end_session":
            return self._finish_party(state)
        if move["action"] == "reroll":
            owner = self.cup_owner(state, actor["player_id"])
            state["dice_by_player"][owner] = self._roll(5)
            state.setdefault("rerolled_cups", {})[owner] = True
            return MoveResult(state=state, retain_turn=True, note="本人骰盅已重摇，本轮不可再次重摇。")
        if move["action"] == "surrender":
            return self._surrender(state, actor)
        if move["action"] == "bid":
            return self._apply_bid(state, move, actor)
        return self._apply_challenge(state, actor)

    def progress_after_action(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
        participants: list[dict[str, Any]],
        applied: dict[str, Any] | MoveResult,
    ) -> dict[str, Any] | MoveResult:
        del state, actor
        if move.get("action") != "challenge" or not isinstance(applied, MoveResult):
            return applied
        outcome = applied.state.get("last_round_result")
        if not isinstance(outcome, dict):
            return applied
        names = {
            str(participant["player_id"]): str(
                participant.get("display_name") or participant["player_id"]
            )
            for participant in participants
        }
        challenger_id = str(outcome["challenger_player_id"])
        bidder_id = str(outcome["bidder_player_id"])
        loser_id = str(outcome["loser_player_id"])
        starter_id = outcome.get("next_starter_player_id")
        challenger_name = names.get(challenger_id, challenger_id)
        bidder_name = names.get(bidder_id, bidder_id)
        loser_name = names.get(loser_id, loser_id)
        outcome["challenger_display_name"] = challenger_name
        outcome["bidder_display_name"] = bidder_name
        outcome["loser_display_name"] = loser_name
        if starter_id is not None:
            starter_id = str(starter_id)
            outcome["next_starter_display_name"] = names.get(starter_id, starter_id)
            outcome["next_round_summary"] = (
                f"第 {outcome['next_round']} 轮 · "
                f"由 {outcome['next_starter_display_name']} 开叫"
            )
        bid = outcome["bid"]
        elimination = "，已淘汰" if outcome["eliminated"] else "，未淘汰"
        result_summary = (
            f"{challenger_name} 质疑 {bidder_name} 的叫点"
            f"“{bid['quantity']} 个 {bid['face']} 点”；实际有 "
            f"{outcome['actual_count']} 个 {bid['face']} 点，"
            f"叫点{'成立' if outcome['bid_holds'] else '失败'}；"
            f"{loser_name} 输掉 1 枚骰，剩余 {outcome['loser_remaining_dice']} 枚"
            f"{elimination}。"
        )
        if self.is_party(applied.state):
            rule = "斋" if bid.get("exact") else "1百搭"
            result_summary = (
                f"{challenger_name} 质疑 {bidder_name} 的{bid['quantity']}个{bid['face']}点（{rule}）；"
                f"实际{outcome['actual_count']}个，叫点{'成立' if outcome['bid_holds'] else '失败'}；"
                f"{loser_name}{'所在队' if outcome.get('loser_team_id') else ''} 本局积分{outcome['score_delta']:g}，现为{outcome['score_after']:g}分；骰数不变。"
            )
        if self.is_party(applied.state):
            if outcome.get("dark_reversed"):
                result_summary += "天黑黑：无人持有所叫实点，胜负反转。"
            for penalty in outcome["penalties"]:
                result_summary += (f"{names.get(penalty['player_id'], penalty['player_id'])}"
                    f"：原始{penalty['raw_multiplier']}倍→{penalty['multiplier']}倍（上限4），扣{-penalty['score_delta']:g}分。")
        outcome["result_summary"] = result_summary
        summary = f"第 {outcome['round']} 轮：{result_summary}"
        if outcome.get("next_round_summary"):
            summary += outcome["next_round_summary"] + "。"
        else:
            summary += "对局结束。"
        outcome["summary"] = summary
        applied.note = summary
        applied.public_event = {
            "round_result": {
                "round": int(outcome["round"]),
                **({key: outcome[key] for key in ("mode", "winner_player_id", "score_delta", "score_after", "loser_team_id", "penalties", "companion", "cup_bonuses", "dark_reversed")} if self.is_party(applied.state) else {}),
                "bid": {
                    "quantity": int(bid["quantity"]),
                    "face": int(bid["face"]),
                    **({"exact": bid["exact"]} if "exact" in bid else {}),
                },
                "actual_count": int(outcome["actual_count"]),
                "bid_holds": bool(outcome["bid_holds"]),
                "loser_player_id": loser_id,
                "loser_remaining_dice": int(outcome["loser_remaining_dice"]),
                "eliminated": bool(outcome["eliminated"]),
                "next_round": outcome.get("next_round"),
                "next_starter_player_id": starter_id,
                "revealed_dice_by_player": deepcopy(
                    outcome["revealed_dice_by_player"]
                ),
            }
        }
        return applied

    def result_for(
        self,
        state: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        del participants
        if self.is_party(state) and state.get("session_result"):
            return deepcopy(state["session_result"])
        winner = state.get("winner_player_id")
        return {"winner_player_id": winner, "draw": False} if winner else None

    def apply_resignation(
        self,
        state: dict[str, Any],
        resigned_player_id: str,
        participants: list[dict[str, Any]],
    ) -> None:
        del participants
        if resigned_player_id not in state.get("participant_order", []):
            raise ValueError("吹牛骰子认输者不属于本桌")
        if self.is_party(state):
            self._finish_party(state, "participant_departed")
            return
        state["dice_counts"][resigned_player_id] = 0
        eliminated = state.setdefault("eliminated_player_ids", [])
        if resigned_player_id not in eliminated:
            eliminated.append(resigned_player_id)
        active = self._active_ids(state)
        if len(active) == 1:
            state["winner_player_id"] = active[0]
            state["current_bid"] = None
            state["pending_next_round"] = None
            state["turn_player_id"] = None
            state["flow"]["phase"] = "finished"
            return
        starter = self._next_survivor_after(state, resigned_player_id)
        if state.get("flow", {}).get("phase") == "awaiting_round_acknowledgement":
            pending = state.get("pending_next_round")
            if isinstance(pending, dict) and pending.get("starter_player_id") == resigned_player_id:
                pending["starter_player_id"] = starter
        else:
            # A departure changes the total dice and can invalidate any live bid;
            # restart the bidding window with the persisted dice of survivors.
            state["current_bid"] = None
            state["round_actions"] = []
            state["pending_next_round"] = None
            state["flow"]["phase"] = "bidding"
        state["turn_player_id"] = starter

    def check_winner(self, state: dict[str, Any]) -> str | None:
        del state
        return None

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
        if winner not in player_ids:
            raise ValueError("吹牛骰子终局缺少有效唯一赢家")
        return {
            player_id: stake * (len(player_ids) - 1)
            if player_id == winner else -stake
            for player_id in player_ids
        }

    def public_state(
        self,
        state: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return self._project_public_state(
            state,
            participants,
            terminal=state.get("flow", {}).get("phase") == "finished",
        )

    def terminal_public_state(
        self,
        state: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return self._project_public_state(state, participants, terminal=True)

    def _project_public_state(
        self,
        state: dict[str, Any],
        participants: list[dict[str, Any]],
        *,
        terminal: bool,
    ) -> dict[str, Any]:
        del participants
        projected = {
            "board_kind": "liars_dice",
            "flow": deepcopy(state["flow"]),
            "dice_counts": deepcopy(state["dice_counts"]),
            "max_bid_quantity": self.total_dice(state),
            "current_bid": deepcopy(state.get("current_bid")),
            "eliminated_player_ids": list(state.get("eliminated_player_ids", [])),
            "last_round_result": deepcopy(state.get("last_round_result")),
            "pending_next_round": deepcopy(state.get("pending_next_round")),
            "last_action_note": state.get("last_action_note", ""),
        }
        projected["mode"] = state.get("mode", "classic")
        if self.is_party(state):
            projected.update(self.saved_options(state))
            projected["dice_retained"] = state.get("dice_retained", False)
            projected["scores"] = self.party_scores(state)
            if state.get("teams"):
                projected.update(teams=deepcopy(state["teams"]), team_scores=deepcopy(state["team_scores"]), team_mode=state.get("team_mode", "pairs"))
            projected.update(literal_dice=state.get("literal_dice", False),
                             records=deepcopy(state.get("records", {})),
                             round_results=deepcopy(state.get("round_results", [])))
        projected["bid_ranges"] = self.bid_ranges(state)
        if terminal:
            projected["terminal_dice"] = {
                player_id: deepcopy(state.get("dice_by_player", {}).get(player_id, []))
                for player_id in (state.get("dice_by_player", {}) if state.get("teams") else state.get("participant_order", []))
            }
        return projected

    def private_state(
        self,
        state: dict[str, Any],
        viewer: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any]:
        owner = self.cup_owner(state, viewer["player_id"])
        return {"dice": deepcopy(state["dice_by_player"][owner]),
                **({"can_reroll": party.can_reroll(state, owner)} if self.is_party(state) else {}),
                **({"team_id": self.team_for(state, viewer["player_id"]), "cup_owner_player_id": owner} if state.get("teams") else {})}

    def mcp_snapshot_state(self, public_state, viewer, participants):
        snapshot = super().mcp_snapshot_state(public_state, viewer, participants)
        snapshot.pop("round_results", None)
        return snapshot

    def mcp_bootstrap_state(self, public_state, viewer, participants):
        return self.mcp_snapshot_state(public_state, viewer, participants)

    def participant_summary(
        self,
        state: dict[str, Any],
        participant: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, int]:
        del participants
        pid = participant["player_id"]
        result = {"dice_count": int(state["dice_counts"][pid])}
        if self.is_party(state):
            score = self.party_scores(state)[pid]
            result["score"] = int(score) if score == int(score) else f"{score:g}"
            if state.get("teams"):
                team = self.team_for(state, pid)
                result["team"] = {"red": "红队", "blue": "蓝队"}.get(team, list(state["teams"]).index(team) + 1)
        return result

    def npc_compact_rules(
        self,
        state: dict[str, Any],
        actor: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> str:
        del actor, participants
        if self.is_party(state):
            descriptions = {
                "cup_penalty": "围骰/全围叫点败方×2。",
                "extreme_penalty": "不足半数叫点败方/超两倍质疑败方×2（严格不含边界）。",
                "dark_rule": "无人持叫点实点先反转胜负，再按最终败方判断处罚。",
                "companion_penalty": "d6按固定座序抽陪扣者，同倍率，抽中本人/同队再×2，仍封顶4。",
                "allow_surrender": "已有叫点可投降固定-0.5，不叠加处罚。",
                "surrender_no_reroll": "投降不揭骰，下一轮保留骰。",
            }
            extras = "".join(text for key, text in descriptions.items() if state.get(key))
            bonus = (party.modern(state) or "dice_counts" not in state) and not state.get("literal_dice")
            return (
                "酒桌每盅5骰。普通1百搭，叫斋/1后本轮锁斋，不飞斋；(数量,点数)递增。"
                f"普通首叫ceil(1.5×盅数)，5盅{state.get('opening_five', 8)}起，斋至少盅数。"
                + ("无围骰加数、无单骰重摇，1仍百搭。" if not bonus
                   else "围骰借1凑叫点+1，同实点全围+2；全1百搭不加数。首叫前5点各异可重摇一次。")
                + ("红蓝队各一盅共用队分、队内轮流发言，只看己队骰。" if state.get("teams") else "只看己骰。")
                + "开骰足数质疑方败，不足叫点方败，基础-1、赢家不加分；倍罚至多4分。"
                + extras + "败方先叫，确认后重摇（已开投降保留骰除外）。仅选权威合法动作，不主动结束整桌。"
            )
        return (
            "每人初始 5d6，1 不万能。叫点按(数量,点数)严格升高且数量不超过场上骰子数；"
            "已有叫点时也可质疑。实际数达到叫点则质疑者失骰，否则叫点者失骰；"
            "零骰淘汰，最后一人胜。只能选权威合法行动。"
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
        if state.get("flow", {}).get("phase") != "bidding" or actor.get("player_id", state["participant_order"][0]) not in self._active_ids(state):
            return []
        actions = self.bid_options(state)
        if self.is_party(state) and party.can_reroll(state, self.cup_owner(state, actor.get("player_id"))):
            actions.append({"action": "reroll"})
        current = state.get("current_bid")
        if current is not None and self.cup_owner(state, current.get("bidder_player_id")) != self.cup_owner(state, actor.get("player_id")):
            actions.append({"action": "challenge"})
            if self.is_party(state) and state.get("allow_surrender"):
                actions.append({"action": "surrender"})
        return actions

    def bid_options(self, state):
        if state.get("flow", {}).get("phase") != "bidding":
            return []
        party = self.is_party(state)
        locked = bool((state.get("current_bid") or {}).get("exact"))
        actions = []
        for quantity in range(1, self.total_dice(state) + 1):
            for face in range(1, 7):
                exacts = ([True] if locked or face == 1 else [False, True]) if party else [False]
                for exact in exacts:
                    if self._valid_bid(state, quantity, face, exact):
                        actions.append({"action": "bid", "quantity": quantity, "face": face,
                                        **({"exact": exact} if party else {})})
        return actions

    def bid_ranges(self, state):
        ranges = {}
        for bid in self.bid_options(state):
            key = (bid["face"], bid.get("exact", False))
            if key not in ranges:
                ranges[key] = {k: v for k, v in bid.items() if k not in ("action", "quantity")}
                ranges[key]["min_quantity"] = bid["quantity"]
        return list(ranges.values())

    def format_action(
        self,
        state: dict[str, Any],
        move: dict[str, Any],
        actor: dict[str, Any],
    ) -> str:
        del actor
        if move.get("action") == "end_session":
            return "结束酒桌局"
        if move.get("action") in ("reroll", "surrender"):
            return {"reroll":"单骰重摇", "surrender":"本轮投降认罚"}[move["action"]]
        if move.get("action") == "challenge":
            return "质疑"
        quantity, face = self._parse_bid(move)
        suffix = "（斋）" if self.is_party(state) and self._is_exact(state, face, move.get("exact", False)) else ""
        return f"叫 {quantity} 个 {face} 点{suffix}"

    def format_move(
        self, state: dict[str, Any], move: dict[str, Any], mark: str
    ) -> str:
        del mark
        return self.format_action(state, move, {})
