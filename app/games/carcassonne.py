"""Carcassonne base rules: explicit feature graph, atomic turns, private deck."""
from collections import Counter
from copy import deepcopy
import random

from .base import GamePlugin, MoveResult
from .carcassonne_tiles import TILES, START_TILE, build_deck, oriented

DIRECTIONS = ((0,-1), (1,0), (0,1), (-1,0))


def board_index(board):
    return {(t["x"], t["y"]): t for t in board}


def legal_placements(board, name):
    occupied = board_index(board)
    frontier = {(x+dx,y+dy) for x,y in occupied for dx,dy in DIRECTIONS} - occupied.keys()
    out = []
    for x,y in sorted(frontier, key=lambda p: (p[1],p[0])):
        for rot in range(4):
            edges = oriented(name, rot)["edges"]
            if all((x+dx,y+dy) not in occupied or edges[side] == oriented(
                    occupied[(x+dx,y+dy)]["tile"], occupied[(x+dx,y+dy)]["rotation"])["edges"][(side+2)%4]
                   for side,(dx,dy) in enumerate(DIRECTIONS)):
                out.append((x,y,rot))
    return out


class FeatureGraph:
    """Transient graph reconstructed from the persisted board (at most 72 tiles).

    Separate local regions remain separate even if they have the same terrain.
    Union only across actual edge ports, never through a tile's corners.
    """
    def __init__(self, board):
        self.board = board_index(board)
        self.parent, self.regions, self.ports = {}, {}, {}
        for (x,y), t in self.board.items():
            for rid, region in oriented(t["tile"], t["rotation"])["regions"].items():
                node = (x,y,rid)
                self.parent[node] = node
                self.regions[node] = region
                for port in region["ports"]:
                    self.ports[(x,y,region["kind"],port)] = node
        for (x,y,kind,port), node in self.ports.items():
            side = port//2 if kind == "field" else port
            dx,dy = DIRECTIONS[side]
            opposite = (((side+2)%4)*2 + (1-port%2)) if kind == "field" else (side+2)%4
            neighbor = self.ports.get((x+dx,y+dy,kind,opposite))
            if neighbor:
                self.union(node, neighbor)
        self.components = {}
        for node,r in self.regions.items():
            root = self.find(node)
            c = self.components.setdefault(root, {"kind": r["kind"], "nodes": [], "tiles": set(),
                "open": 0, "shields": 0, "meeples": [], "cities": set()})
            c["nodes"].append(node)
            c["tiles"].add(node[:2])
            c["shields"] += r.get("shields", 0)
            if r["kind"] in ("road", "city"):
                c["open"] += sum((node[0]+DIRECTIONS[p][0], node[1]+DIRECTIONS[p][1]) not in self.board for p in r["ports"])
            elif r["kind"] == "monastery":
                c["open"] = sum((node[0]+dx,node[1]+dy) not in self.board
                                for dx in (-1,0,1) for dy in (-1,0,1) if dx or dy)
            elif r["kind"] == "field":
                c["cities"].update(self.find((node[0],node[1],city)) for city in r["cities"])
        for (x,y), t in self.board.items():
            m = t.get("meeple")
            if m:
                self.components[self.find((x,y,m["region"]))]["meeples"].append((x,y,m["player_id"]))

    def find(self, node):
        while self.parent[node] != node:
            self.parent[node] = self.parent[self.parent[node]]
            node = self.parent[node]
        return node

    def union(self, a, b):
        a,b = self.find(a), self.find(b)
        if a != b:
            self.parent[max(a,b)] = min(a,b)

    def region(self, x,y,rid):
        return self.components[self.find((x,y,rid))]

    def available(self, name, x, y, rotation):
        """Occupancy after joining this candidate; no board copy/graph rebuild.

        If the candidate joins several formerly separate components of one
        region, every one must be unoccupied. Local candidate regions which
        rejoin externally inherit that same occupied component as well.
        """
        regions = oriented(name,rotation)["regions"]
        links = {rid:set() for rid in regions}
        parent = {rid:rid for rid in regions}
        def find(rid):
            while parent[rid] != rid:
                parent[rid] = parent[parent[rid]]
                rid = parent[rid]
            return rid
        seen = {}
        for rid,r in regions.items():
            for port in r["ports"]:
                field = r["kind"] == "field"
                side = port//2 if field else port
                dx,dy = DIRECTIONS[side]
                opposite = ((side+2)%4)*2+1-port%2 if field else (side+2)%4
                node = self.ports.get((x+dx,y+dy,r["kind"],opposite))
                if node:
                    root = self.find(node)
                    links[rid].add(root)
                    if root in seen:
                        parent[find(rid)] = find(seen[root])
                    seen[root] = rid
        blocked = {find(rid) for rid, roots in links.items()
                   if any(self.components[root]["meeples"] for root in roots)}
        return [rid for rid in regions if find(rid) not in blocked]



class Carcassonne(GamePlugin):
    game_type = "carcassonne"
    display_name = "卡卡颂"
    category = "tabletop"
    min_players, max_players, recommended_players = 2, 5, 4
    allowed_player_counts = (2,3,4,5)
    supports_npcs = uses_local_npc_strategy = True
    supports_stakes = supports_multiplayer_stakes = uses_custom_stake_settlement = True
    mcp_immediate_public_events = True
    mcp_event_key = 'carcassonne_delta'
    rules_text = """【基础玩法】
2–5 人，经典 72 块地形（含起始块），每人 7 名随从。采用基础道路、城市、修道院与农夫规则；不加入河流、修道院长或其他扩展。花园和装饰不产生额外功能。

【每回合】
- 服务端抽一块公开地形。旋转后放在地图空位，至少一边相邻，所有相接边都须同为道路、城市或田地。仅角接不算相邻。
- 可以从库存放一名随从到刚放板块的一个区域，也可以不放。所选区域连接的整片道路、城市或田地必须没有任何人的随从。
- 然后结算本回合完成的道路、城市和修道院，收回对应随从，轮到下一席。刚收回的随从不能倒过来在本回合再放。
- 抽到在所有方向、所有空位都无法放的板块，公开弃掉并重抽，不跳过当前玩家。最后一块放完或剩余块全部不可放时进行终局结算。

【得分】
- 完整道路：每块板块 1 分；路口是各条道路的端点。闭合环路也完成。
- 完整城市：每块板块 2 分，每枚盾徽额外 2 分。分离城段后来在外部连成同一城时，同一板块只计一次。
- 修道院：中心与周围八格齐全时得 9 分。
- 新板块可以把已被不同随从占据的区域合并。计分时随从最多者得全分；人数并列最多者各得全分，随后所有该区域随从回库存。

【农夫与终局】
- 道路和城市隔开田地。农夫一直留到终局，不会在途中回收。
- 终局未完成道路每块 1 分；未完成城市每块与每枚盾徽各 1 分；未完成修道院按中心及已有周边格数计分。
- 每片田地统计相邻的不同已完成城市，每城 3 分。绕城接触多处只算一次；同一城市可分别给不同田地计分。田地也按农夫多数／并列多数给全分。
- 总分最高者获胜，可以并列。

【平台约定】
开局顺序沿用双弈房间设置；邀请房随机排座。离席或认输者退出争胜，其随从移回库存；其余玩家继续，剩一人直接获胜。普通房可再来一局，邀请房沿用另开邀请。娱乐筹码与局分分开：唯一赢家获得每名败者的一份底注；并列第一时本桌筹码全退（0 结算），局分仍保留。此为平台结算约定。"""
    move_format = '''一次提交 {"action":"place","x":0,"y":-1,"rotation":0,"meeple":null}，附 params.revision。
x 向东、y 向南，可为负；rotation=0/1/2/3 表示顺时针 0/90/180/270 度。
meeple=null 为不放，否则为当前板块的 c0/r0/f0/m0 等区域 ID，旋转不改变 ID。
private_state.placements 每项为 [x,y,rotation,[可放随从的区域ID]]；空数组仍可不放。
current_tile.regions 给出连通区域、边口、盾徽及田地相邻的本块城市ID。
城市/道路 ports 的 0/1/2/3 为北/东/南/西；田地 ports 的 0..7 顺时针为北左、北右、东上、东下、南右、南左、西下、西上。城市边无田地口。
同一 regions 项内部连通，不同项不直接连通；跨边田地两半口反向对应。m0 是本块修道院。
当前牌和合法落点均由裁判给出，不提交 tile 或抽牌序。地图 board + topology 可重建全部连通关系；事件 carcassonne_delta 只发新增块、随从回收、得分与新抽牌。'''

    mcp_move_format = move_format.replace(
        '[x,y,rotation,[可放随从的区域ID]]；空数组仍可不放',
        '[x,y,rotation,region_set_index]；区域列表查 private_state.placement_regions[index]，仍可不放'
    ).replace('current_tile.regions', 'topology[current_tile].regions')

    def __init__(self, rng=None):
        self.rng = rng or random.SystemRandom()

    def initial_state(self):
        return {"board_kind": self.game_type, "rules_version": "classic72-farmers-v1",
                "flow": {"phase": "waiting", "turn_number": 0}, "board": [], "deck": [],
                "current_tile": None, "discarded": [], "scores": {}, "supply": {},
                "participant_order": [], "active_player_ids": [], "resigned_player_ids": [],
                "turn_player_id": None, "result": None, "last_action": None, "last_scoring": []}

    def initialize(self, participants):
        order = [str(p["player_id"]) for p in sorted(participants, key=lambda p: p.get("seat_index",0))]
        if not 1 <= len(order) <= 5 or len(set(order)) != len(order):
            raise ValueError("卡卡颂需要 2–5 位不同玩家")
        s = self.initial_state()
        s.update(participant_order=order, active_player_ids=order[:],
                 scores={p:0 for p in order}, supply={p:7 for p in order})
        if len(order) == 1:
            return s
        deck = build_deck()
        self.rng.shuffle(deck)
        s.update(board=[{"x":0,"y":0,"tile":START_TILE,"rotation":0}], deck=deck,
                 turn_player_id=order[0])
        s["flow"]["phase"] = "playing"
        self._draw(s)
        return s

    def tokens_for(self, participants):
        return [f"P{i+1}" for i in range(len(participants))]

    def prepare_opening_state(self, state, first_player_id, participants):
        state["turn_player_id"] = first_player_id
        return state

    def validate_move(self, state, move, mark):
        raise ValueError("请使用带身份的 action 接口")

    def apply_move(self, state, move, mark):
        raise ValueError("请使用带身份的 action 接口")

    def check_winner(self, state):
        return None

    def _draw(self, state):
        state["current_tile"] = None
        while state["deck"]:
            name = state["deck"].pop()
            if legal_placements(state["board"], name):
                state["current_tile"] = name
                return
            state["discarded"].append(name)
        self._finish(state)

    def validate_action(self, state, move, actor):
        pid = str(actor["player_id"])
        if state["result"] or state["flow"]["phase"] != "playing" or state["turn_player_id"] != pid or pid not in state["active_player_ids"]:
            raise ValueError("当前不是该玩家回合")
        if not isinstance(move,dict) or set(move) != {"action","x","y","rotation","meeple"} or move.get("action") != "place":
            raise ValueError("提交 place 与 x、y、rotation、meeple；不放随从请传 null")
        if any(type(move[k]) is not int for k in ("x","y","rotation")) or move["rotation"] not in range(4):
            raise ValueError("坐标须为整数，rotation 须为 0–3 的整数")
        x,y,rot = move["x"],move["y"],move["rotation"]
        if (x,y,rot) not in legal_placements(state["board"], state["current_tile"]):
            raise ValueError("落点须为空且边邻接地图，所有接边地形匹配")
        rid = move["meeple"]
        if rid is not None:
            if not isinstance(rid,str) or rid not in TILES[state["current_tile"]]["regions"]:
                raise ValueError("未知随从区域")
            if state["supply"][pid] <= 0:
                raise ValueError("没有剩余随从")
            if rid not in FeatureGraph(state["board"]).available(state["current_tile"],x,y,rot):
                raise ValueError("整个连通区域已有随从")

    @staticmethod
    def _next(state, pid):
        order = state["participant_order"]
        start = order.index(pid)
        return next(order[(start+n)%len(order)] for n in range(1,len(order)+1)
                    if order[(start+n)%len(order)] in state["active_player_ids"])

    def _score(self, state, final=False):
        graph = FeatureGraph(state["board"])
        events = []
        for root,c in graph.components.items():
            if not c["meeples"] or (not final and (c["kind"] == "field" or c["open"])):
                continue
            cities = []
            if c["kind"] == "field":
                cities = sorted(city for city in c["cities"] if not graph.components[city]["open"])
                points = len(cities)*3
            elif c["kind"] == "monastery":
                points = 9-c["open"]
            elif c["kind"] == "city":
                points = (len(c["tiles"])+c["shields"])*(1 if c["open"] else 2)
            else:
                points = len(c["tiles"])
            counts = Counter(p for x,y,p in c["meeples"])
            winners = sorted(p for p,n in counts.items() if n == max(counts.values()))
            for p in winners:
                state["scores"][p] += points
            returned = []
            for x,y,p in c["meeples"]:
                state["supply"][p] += 1
                graph.board[(x,y)].pop("meeple")
                returned.append([x,y,p])
            events.append({"feature":list(root), "kind":c["kind"], "points":points,
                           "players":winners, "returned":returned, "cities":[list(city) for city in cities]})
        return events

    def _finish(self, state, reason="tiles_exhausted", winners=None):
        if state["result"]:
            return state["result"]
        state["last_scoring"].extend(self._score(state,final=True))
        active = state["active_player_ids"]
        if winners is None:
            high = max(state["scores"][p] for p in active)
            winners = [p for p in active if state["scores"][p] == high]
        state["result"] = {"reason":reason, "draw":len(winners)!=1,
            "winner_player_id":winners[0] if len(winners)==1 else None,
            "winning_player_ids":winners, "scores":deepcopy(state["scores"])}
        state["flow"]["phase"] = "finished"
        state["turn_player_id"] = None
        return state["result"]

    def apply_action(self, state, move, actor):
        self.validate_action(state,move,actor)
        s = deepcopy(state)
        pid = str(actor["player_id"])
        t = {"x":move["x"], "y":move["y"], "rotation":move["rotation"], "tile":s["current_tile"]}
        if move["meeple"] is not None:
            t["meeple"] = {"player_id":pid, "region":move["meeple"]}
            s["supply"][pid] -= 1
        s["board"].append(t)
        s["last_action"] = {"player_id":pid, **deepcopy(t)}
        s["last_scoring"] = self._score(s)
        s["flow"]["turn_number"] += 1
        s["turn_player_id"] = self._next(s,pid)
        self._draw(s)
        delta = {"placed":deepcopy(s["last_action"]), "scoring":deepcopy(s["last_scoring"]),
                 "scores":deepcopy(s["scores"]), "supply":deepcopy(s["supply"]),
                 "current_tile":deepcopy(oriented(s["current_tile"])) if s["current_tile"] else None,
                 "deck_count":len(s["deck"]), "discarded":s["discarded"][len(state["discarded"]):],
                 "result":deepcopy(s["result"])}
        return MoveResult(state=s, next_player_id=s["turn_player_id"], result=deepcopy(s["result"]),
            note=f"放置板块；本次结算 {sum(e['points']*len(e['players']) for e in s['last_scoring'])} 分。",
            public_event={"carcassonne_delta":delta})

    def public_state(self, state, participants):
        public = {k:deepcopy(state[k]) for k in ("board_kind","rules_version","flow","board","scores","supply",
            "participant_order","active_player_ids","resigned_player_ids","turn_player_id","result","last_action","last_scoring","discarded")}
        public["deck_count"] = len(state["deck"])
        public["current_tile"] = deepcopy(oriented(state["current_tile"])) if state["current_tile"] else None
        # Only types already on the public map, never the full unused library.
        public["topology"] = {name:deepcopy(oriented(name)) for name in sorted({t["tile"] for t in state["board"]})}
        return public

    def private_state(self, state, viewer, participants):
        pid = str(viewer["player_id"])
        if state["result"] or pid != state["turn_player_id"]:
            return {}
        graph = FeatureGraph(state["board"])
        placements = [[x,y,r,graph.available(state["current_tile"],x,y,r) if state["supply"][pid] else []]
                      for x,y,r in legal_placements(state["board"],state["current_tile"])]
        return {"placements":placements, "current_tile":deepcopy(oriented(state["current_tile"])),
                "decision_context":{"board":[[t["x"],t["y"],t["tile"],t["rotation"],t.get("meeple")] for t in state["board"]],
                    "scores":deepcopy(state["scores"]), "supply":deepcopy(state["supply"]), "deck_count":len(state["deck"])},
                "legal_action_spec":{"action":"place", "x":"integer", "y":"integer", "rotation":[0,1,2,3],
                                     "meeple":"null or region ID from placements"}}

    def mcp_snapshot_state(self, public_state, viewer, participants):
        public = deepcopy(public_state)
        public.pop("last_scoring",None)
        # Static inventory is public rules, not the shuffled hidden deck. Send
        # it once so subsequent draws only need the tile ID, including fields.
        public['topology'] = {name: deepcopy(oriented(name)) for name in TILES}
        public['current_tile'] = public['current_tile']['tile'] if public['current_tile'] else None
        public['delta_format'] = 'placed adds one tile; scoring.returned removes meeples at [x,y,player]; scores/supply merge by player; current_tile is topology ID. placements=[x,y,rotation,region_set_index], indexing placement_regions; null meeple always allowed. Skip events with revision <= restored snapshot revision.'
        public['coordinates'] = 'x east, y south; rotation clockwise quarter turns, region IDs unchanged. Edge ports N/E/S/W=0/1/2/3; field ports NW,NE,EN,ES,SE,SW,WS,WN=0..7; rotate field ports by 2 per quarter turn. Opposite field half-ports connect in reverse order.'
        return public

    mcp_bootstrap_state = mcp_snapshot_state

    def mcp_private_state(self, private_state, viewer, participants):
        private = deepcopy(private_state)
        private.pop('decision_context', None)
        private.pop('current_tile', None)
        if 'placements' in private:
            regions = []
            placements = []
            for x, y, rotation, allowed in private['placements']:
                if allowed not in regions:
                    regions.append(allowed)
                placements.append([x, y, rotation, regions.index(allowed)])
            private['placements'] = placements
            private['placement_regions'] = regions
            private['legal_action_spec']['meeple'] = 'null or region ID from placement_regions[placement[3]]'
        return private

    def mcp_event(self, event, previous):
        from .mcp_incremental import compact_event, changed
        event = compact_event(event)
        if event['event_type'] == 'move' and (event.get('move') or {}).get('action') == 'place':
            event['move'] = {'action': 'place'}
        value = (event.get('move') or {}).get('carcassonne_delta')
        if value is not None:
            delta = deepcopy(value)
            old = previous.get('carcassonne_delta', {})
            delta['current_tile'] = value['current_tile']['tile'] if value['current_tile'] else None
            for key in ('scores', 'supply'):
                delta[key] = changed(value[key], old.get(key, {}))
            event['move'] = {'carcassonne_delta': {k:v for k,v in delta.items()
                              if v or k in ('current_tile', 'deck_count')}}
        return event

    def mcp_lifecycle_state(self, public):
        return {k: deepcopy(public[k]) for k in ('board', 'scores', 'supply',
                'active_player_ids', 'resigned_player_ids', 'turn_player_id', 'result')}

    def participant_summary(self, state, participant, participants):
        pid = str(participant["player_id"])
        return {"score":state.get("scores",{}).get(pid,0), "随从":state.get("supply",{}).get(pid,7)}

    def result_for(self, state, participants):
        return deepcopy(state["result"])

    def apply_resignation(self, state, resigned_player_id, participants):
        if resigned_player_id in state["active_player_ids"]:
            state["active_player_ids"].remove(resigned_player_id)
            state["resigned_player_ids"].append(resigned_player_id)
        for tile in state["board"]:
            if tile.get("meeple",{}).get("player_id") == resigned_player_id:
                tile.pop("meeple")
                state["supply"][resigned_player_id] += 1
        if len(state["active_player_ids"]) == 1:
            self._finish(state,"resignation",state["active_player_ids"][:])
        elif state["turn_player_id"] == resigned_player_id:
            state["turn_player_id"] = self._next(state,resigned_player_id)

    def result_for_resignation(self, state, resigned_player_id, participants):
        return state["result"] or self._finish(state,"only_system_npcs_remaining",[])

    def settlement_deltas(self, state, result, participants, stake):
        ids = [str(p["player_id"]) for p in participants]
        if result.get("draw"):
            return {p:0 for p in ids}
        winner = result.get("winner_player_id")
        if winner not in ids:
            raise ValueError("缺少有效赢家")
        return {p:stake*(len(ids)-1) if p==winner else -stake for p in ids}

    def format_action(self, state, move, actor):
        return f"拼接 ({move.get('x')},{move.get('y')})，旋转 {move.get('rotation')}"

    def choose_local_npc_action(self, state, actor, participants):
        pid = str(actor["player_id"])
        if state["result"] or state["turn_player_id"] != pid:
            return None
        positions = legal_placements(state["board"],state["current_tile"])
        # Evaluate at most 32 compact-map candidates. Never inspect deck order.
        positions.sort(key=lambda p:(abs(p[0])+abs(p[1]), p))
        graph = FeatureGraph(state["board"])
        best, choice = float("-inf"), None
        for x,y,r in positions[:32]:
            t = {"x":x,"y":y,"rotation":r,"tile":state["current_tile"]}
            candidate = FeatureGraph(state["board"]+[t])
            score = 0
            for c in candidate.components.values():
                if c["kind"] != "field" and not c["open"]:
                    count = Counter(p for _,_,p in c["meeples"])
                    if count:
                        score += (5 if count[pid] == max(count.values()) else -2)*len(c["tiles"])
            regions = graph.available(t["tile"],x,y,r) if state["supply"][pid] else []
            rid, value = None, 0
            for region in regions:
                c = candidate.region(x,y,region)
                if c["kind"] == "field":
                    # Conserve scarce farmers; local, bounded heuristic only.
                    completed = sum(not candidate.components[city]["open"] for city in c["cities"])
                    v = completed*2-3 if state["supply"][pid]>2 else -1
                elif c["kind"] == "monastery":
                    v = 4-c["open"]*.35
                else:
                    v = len(c["tiles"])*(2 if c["kind"]=="city" else 1)-c["open"]*.3
                    if not c["open"]:
                        v += 5
                    if state["supply"][pid] < 3 and c["open"]:
                        v -= 4
                if v > value:
                    rid,value = region,v
            score += value
            if score > best:
                best,choice = score,{"action":"place","x":x,"y":y,"rotation":r,"meeple":rid}
        return choice

    def npc_legal_actions(self, state, actor, participants):
        move = self.choose_local_npc_action(state,actor,participants)
        return [move] if move else []
