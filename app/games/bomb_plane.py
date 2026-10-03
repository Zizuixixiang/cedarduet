"""Original paper-and-pencil plane rules; no third-party implementation.

Only projections enter NPC scoring. At most 168 single-plane candidates are
scored; neither hidden opponent planes nor exponential fleet search are used.
"""
from copy import deepcopy
from functools import lru_cache
import random
import re

from .base import GamePlugin, MoveResult

DIRECTIONS = ('N', 'E', 'S', 'W')
OFFSETS = ((0, 0), (-2, 1), (-1, 1), (0, 1), (1, 1), (2, 1),
           (0, 2), (-1, 3), (0, 3), (1, 3))
SQUARES = tuple(f'{c}{r}' for r in range(1, 11) for c in 'ABCDEFGHIJ')


def square(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-J](?:[1-9]|10)', value):
        raise ValueError('坐标须为 A1–J10（列 A–J，行 1–10）')
    return value


def plane_cells(plane):
    if not isinstance(plane, dict) or set(plane) != {'head', 'direction'}:
        raise ValueError('飞机只接受 head、direction')
    head = square(plane['head'])
    direction = plane['direction']
    if direction not in DIRECTIONS:
        raise ValueError('direction 须为 N/E/S/W')
    x, y = ord(head[0]) - 65, int(head[1:]) - 1
    cells = []
    for dx, dy in OFFSETS:
        for _ in range(DIRECTIONS.index(direction)):
            dx, dy = -dy, dx
        cx, cy = x + dx, y + dy
        if not (0 <= cx < 10 and 0 <= cy < 10):
            raise ValueError('飞机越界，请调整机头或朝向')
        cells.append(f'{chr(65 + cx)}{cy + 1}')
    return tuple(cells)


def validate_layout(planes):
    if not isinstance(planes, list) or len(planes) > 3:
        raise ValueError('布局须为最多三架飞机的数组')
    heads = set()
    for plane in planes:
        plane_cells(plane)  # Every complete plane must remain on the board.
        if plane['head'] in heads:
            raise ValueError('飞机可重叠，但机头不能重合')
        heads.add(plane['head'])


@lru_cache(maxsize=1)
def candidates():
    result = []
    for head in SQUARES:
        for direction in DIRECTIONS:
            plane = {'head': head, 'direction': direction}
            try:
                cells = plane_cells(plane)
            except ValueError:
                continue
            result.append((head, direction, frozenset(cells)))
    return tuple(result)


def random_layout(rng):
    # A single bounded scan; shared body cells are legal, shared heads are not.
    options = list(candidates())
    rng.shuffle(options)
    planes, heads = [], set()
    for head, direction, _cells in options:
        if head not in heads:
            planes.append({'head': head, 'direction': direction})
            heads.add(head)
            if len(planes) == 3:
                break
    return planes


def inferred_attack(shots, rng):
    """A bounded heuristic taking ONLY visible feedback, not game state."""
    known = {shot['cell']: shot['result'] for shot in shots}
    scores = {cell: 0 for cell in SQUARES if cell not in known}
    for head, _direction, cells in candidates():
        if head in known:  # Already downed planes cannot be a new head target.
            continue
        if any((result == 'miss' and cell in cells)
               or (result == 'hit' and cell == head)
               for cell, result in known.items()):
            continue
        matches = sum(known.get(cell) == 'hit' for cell in cells)
        scores[head] += 1 + 6 * matches * matches
    if not scores:
        return None
    best = max(scores.values())
    return rng.choice([cell for cell, score in scores.items() if score == best])


class BombPlane(GamePlugin):
    game_type = 'bomb_plane'
    display_name = '炸飞机'
    category = 'board'
    allowed_player_counts = (2,)
    supports_npcs = True
    uses_local_npc_strategy = True
    supports_stakes = True
    mcp_immediate_public_events = True
    mcp_event_key = 'bomb_plane_shot'
    rules_text = (
        '【寻机头】\n'
        '两人各在10×10棋盘秘密摆三架飞机，列A–J、行1–10。'
        '每架从机头向后依次1、5、1、3格，共10格，可朝上、右、下、左，整架须在棋盘内。'
        '飞机可互相重叠，但三个机头不能重合；机头可以落在另一架飞机的机体上。'
        '例如C1朝上占C1、A2–E2、C3、B4–D4。\n\n'
        '【布阵】\n'
        '双方独立秘密布阵，无需等待对方。点选机头，用“旋转 ↻”调整方向，预览后放置；'
        '放满三架后点“完成布阵”锁定。完成前可撤销上一架、清空、随机或整组替换；'
        '随机布置不自动确认。双方完成后由原先手攻击。\n\n'
        '【攻击】\n'
        '每回合猜一个未猜格：若是任一机头则为“头”，否则命中任一机体为“伤”，其余为“空”；'
        '三种结果都换手。率先击中三个不同机头获胜。\n\n'
        '【本版约定】\n'
        '纸笔玩法没有唯一官方变体。命中头仅公布该格，不展开飞机；'
        '击落后的机体仍反馈伤，原空格仍为空。终局（含认输）才向本局参与者揭示双方全图。'
        '本地NPC按公开反馈作有限推理，不保证最优。\n\n'
        '【娱乐筹码】\n'
        '赢家得一份房间底注、败者扣一份；认输同样结算，零底注为娱乐局。'
    )
    move_format = (
        '外层带revision；private_state.planes为己方飞机；board_state.shots按攻击者记录cell及result(miss/hit/head)。放置 {"action":"place","head":"C1","direction":"N"}；'
        '撤销末架 {"action":"undo"}；清空 {"action":"clear"}；随机 {"action":"shuffle"}；'
        '确认 {"action":"ready"}；自动部署并确认 {"action":"auto_setup"}；'
        '整组替换 {"action":"set_layout","planes":[{"head":"C1","direction":"N"},'
        '{"head":"H1","direction":"N"},{"head":"C6","direction":"N"}]}；'
        '攻击 {"action":"attack","cell":"E5"}。'
    )

    def __init__(self, rng=None):
        self.rng = rng or random.SystemRandom()

    def initial_state(self):
        return {'board_kind': self.game_type, 'rows': 10, 'cols': 10, 'phase': 'setup',
                'participant_order': [], 'first_player_id': None, 'active_player_id': None,
                'planes': {}, 'ready': {}, 'shots': {}, 'winner_player_id': None}

    def initialize(self, participants):
        return self.initialize_for_first_player(participants, participants[0]['player_id'])

    def initialize_for_first_player(self, participants, first_player_id):
        ids = [p['player_id'] for p in participants]
        if len(ids) not in (1, 2) or len(set(ids)) != len(ids) or first_player_id not in ids:
            raise ValueError('炸飞机须为两名不同参与者，先手须在席')
        state = self.initial_state()
        state.update(participant_order=ids, first_player_id=first_player_id,
                     active_player_id=first_player_id, planes={p: [] for p in ids},
                     ready={p: False for p in ids}, shots={p: [] for p in ids})
        if len(ids) == 1:  # Ordinary waiting room; reinitialized when the second seat joins.
            state['active_player_id'] = None
        return state

    def validate_move(self, state, move, mark):
        raise ValueError('炸飞机使用座位身份动作接口')

    def apply_move(self, state, move, mark):
        raise ValueError('炸飞机使用座位身份动作接口')

    def validate_action(self, state, move, actor):
        pid = actor.get('player_id')
        if len(state['participant_order']) != 2 or pid not in state['participant_order']:
            raise ValueError('当前不是你的行动阶段')
        if state['phase'] == 'finished':
            raise ValueError('对局已经结束')
        if state['phase'] != 'setup' and state['active_player_id'] != pid:
            raise ValueError('当前不是你的行动阶段')
        if not isinstance(move, dict) or not isinstance(move.get('action'), str):
            raise ValueError('move须为包含action的对象')
        action = move['action']
        fields = {'place': {'head', 'direction'}, 'set_layout': {'planes'}, 'attack': {'cell'},
                  'undo': set(), 'clear': set(), 'shuffle': set(), 'ready': set(), 'auto_setup': set()}
        if action not in fields or set(move) != fields[action] | {'action'}:
            raise ValueError('动作或字段不合法；布局归属由本人座位确定')
        if state['phase'] == 'setup':
            if state['ready'][pid] or action == 'attack':
                raise ValueError('布阵已锁定或双方尚未就绪')
            planes = state['planes'][pid]
            if action == 'place':
                validate_layout(planes + [{'head': move['head'], 'direction': move['direction']}])
            elif action == 'set_layout':
                validate_layout(move['planes'])
            elif action == 'ready' and len(planes) != 3:
                raise ValueError('须摆满三架再确认')
            elif action == 'undo' and not planes:
                raise ValueError('没有可撤销的飞机')
        else:
            if action != 'attack':
                raise ValueError('开战后布局锁定，只可攻击')
            cell = square(move['cell'])
            if any(s['cell'] == cell for s in state['shots'][pid]):
                raise ValueError('该格已经攻击过')

    def apply_action(self, state, move, actor):
        self.validate_action(state, move, actor)
        updated = deepcopy(state)
        pid = actor['player_id']
        other = next(p for p in state['participant_order'] if p != pid)
        action = move['action']
        if updated['phase'] == 'setup':
            planes = updated['planes'][pid]
            if action == 'place':
                planes.append({'head': move['head'], 'direction': move['direction']})
            elif action == 'set_layout':
                updated['planes'][pid] = deepcopy(move['planes'])
            elif action == 'undo':
                planes.pop()
            elif action == 'clear':
                planes.clear()
            elif action in ('shuffle', 'auto_setup'):
                updated['planes'][pid] = random_layout(self.rng)
            # Setup is independent. Keep the scheduler cursor stable when the
            # other participant edits, and advance only once its owner locks.
            next_id = updated['active_player_id']
            if action in ('ready', 'auto_setup'):
                updated['ready'][pid] = True
                if all(updated['ready'].values()):
                    updated['phase'] = 'play'
                    next_id = updated['first_player_id']
                elif updated['ready'].get(next_id):
                    next_id = other
            updated['active_player_id'] = next_id
            return MoveResult(updated, next_player_id=next_id,
                              note='布阵已确认' if updated['ready'][pid] else '己方布阵已保存',
                              event_visible_to_player_ids=[pid])
        cell = move['cell']
        targets = updated['planes'][other]
        result = ('head' if any(p['head'] == cell for p in targets) else
                  'hit' if any(cell in plane_cells(p) for p in targets) else 'miss')
        shot = {'cell': cell, 'result': result}
        updated['shots'][pid].append(shot)
        won = sum(s['result'] == 'head' for s in updated['shots'][pid]) == 3
        updated['active_player_id'] = None if won else other
        if won:
            updated.update(phase='finished', winner_player_id=pid)
        return MoveResult(updated, next_player_id=None if won else other,
                          result={'winner_player_id': pid, 'draw': False} if won else None,
                          note=f"{cell}：{dict(miss='空', hit='伤', head='机头')[result]}",
                          public_event={'bomb_plane_shot': {'player_id': pid, **shot}})

    def check_winner(self, state):
        return None  # Participant-aware result_for is authoritative.

    def result_for(self, state, participants):
        pid = state['winner_player_id']
        return {'winner_player_id': pid, 'draw': False} if pid else None

    def public_state(self, state, participants):
        public = {k: deepcopy(state[k]) for k in ('board_kind', 'rows', 'cols', 'phase',
                  'participant_order', 'active_player_id', 'ready', 'shots', 'winner_player_id')}
        if state['phase'] == 'finished':
            public['revealed_planes'] = deepcopy(state['planes'])
        return public

    def terminal_public_state(self, state, participants):
        public = self.public_state(state, participants)
        public.update(phase='finished', active_player_id=None, revealed_planes=deepcopy(state['planes']))
        return public

    def private_state(self, state, viewer, participants):
        pid = viewer.get('player_id')
        if pid not in state['participant_order']:
            return {}
        private = {'planes': deepcopy(state['planes'][pid]), 'legal_actions': [], 'legal_action_spec': {}}
        if state['phase'] == 'finished' or len(state['participant_order']) != 2:
            return private
        if state['phase'] == 'setup':
            if state['ready'][pid]:
                return private
            private['legal_actions'] = [{'action': a} for a in ('shuffle', 'auto_setup', 'clear')]
            if state['planes'][pid]:
                private['legal_actions'].append({'action': 'undo'})
            if len(state['planes'][pid]) == 3:
                private['legal_actions'].append({'action': 'ready'})
            private['legal_action_spec'] = {'place': {'head': 'A1–J10', 'direction': list(DIRECTIONS)},
                                           'set_layout': 'planes: 0..3 × {head,direction}; overlap allowed, unique heads, no out-of-bounds'}
        elif state['active_player_id'] == pid:
            private['legal_action_spec'] = {'attack': {'cell': 'A1–J10; exclude own shots'}}
        return private

    def mcp_snapshot_state(self, public_state, viewer, participants):
        public = deepcopy(public_state)
        public['shots'] = {pid: {result: [s['cell'] for s in shots if s['result'] == result]
                                for result in ('miss', 'hit', 'head')}
                           for pid, shots in public['shots'].items()}
        public['delta_format'] = 'shots grouped by attacker then miss/hit/head. Each bomb_plane_shot adds one public attack. Event revision <= snapshot revision is already included. Own layout is returned after setup edits, not on each attack.'
        return public

    mcp_bootstrap_state = mcp_snapshot_state

    def mcp_turn_private_state(self, private, public):
        private = deepcopy(private)
        if public['phase'] != 'setup':
            private.pop('planes', None)
        return private

    def mcp_move_private_state(self, private, move):
        # auto_setup/shuffle generate private random coordinates. Deliver them
        # even when locking the layout hands control to the other participant.
        if move.get('action') != 'attack':
            return {'planes': deepcopy(private['planes'])}
        return None

    def mcp_event(self, event, previous):
        from .mcp_incremental import compact_event
        return compact_event(event)

    def participant_summary(self, state, participant, participants):
        pid = participant['player_id']
        if state['phase'] == 'setup':
            return {'status': '已锁定' if state['ready'].get(pid) else '待布阵'}
        return {'hits': f"机头 {sum(s['result'] == 'head' for s in state['shots'].get(pid, []))}/3"}

    def project_event(self, event, viewer, participants):
        move = event.get('move') or {}
        if move.get('action') in ('place', 'set_layout', 'undo', 'clear', 'shuffle', 'ready', 'auto_setup'):
            sender = event.get('sender')
            pid = sender.get('player_id') if isinstance(sender, dict) else event.get('sender_player_id')
            if pid != viewer.get('player_id'):
                return None
        return deepcopy(event)

    def format_action(self, state, move, actor):
        if isinstance(move, dict) and move.get('action') == 'attack':
            return f"攻击 {square(move.get('cell'))}"
        return '秘密布阵'  # No coordinates or submitted layout in public labels/errors.

    def npc_legal_actions(self, state, actor, participants):
        pid = actor['player_id']
        if (len(state['participant_order']) != 2 or pid not in state['participant_order']
                or state['phase'] == 'finished'):
            return []
        if state['phase'] == 'setup':
            return [] if state['ready'][pid] else [{'action': 'auto_setup'}]
        if state['active_player_id'] != pid:
            return []
        guessed = {s['cell'] for s in state['shots'][pid]}
        return [{'action': 'attack', 'cell': cell} for cell in SQUARES if cell not in guessed]

    def choose_local_npc_action(self, state, actor, participants):
        # Construct a safe boundary before any strategy work.
        public = self.public_state(state, participants)
        pid = actor['player_id']
        if (len(public['participant_order']) != 2 or pid not in public['participant_order']
                or public['phase'] == 'finished'):
            return None
        if public['phase'] == 'setup':
            return None if public['ready'][pid] else {'action': 'auto_setup'}
        if public['active_player_id'] != pid:
            return None
        return {'action': 'attack', 'cell': inferred_attack(public['shots'][pid], self.rng)}

    def npc_public_actions(self, state, actor, participants):
        return [{'player_id': p, **s} for p in state['participant_order'] for s in state['shots'][p][-5:]]
