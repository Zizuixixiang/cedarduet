"""Server-authoritative Monopoly. All pending choices and shuffled decks are JSON data.

Classic estate numbers adapted from Daniel Moyer's MIT classicedition.js; see
third_party/intrepid_monopoly/NOTICE.md. No upstream browser engine or artwork.
"""
from __future__ import annotations

import json
import random
from copy import deepcopy
from pathlib import Path

from .base import GamePlugin, MoveResult

_DATA = json.loads((Path(__file__).resolve().parents[2] / 'third_party/intrepid_monopoly/estate_data.json').read_text())
_NAMES = ('起点', '松巷', '公益', '杉巷', '所得税', '东站', '溪街', '机会', '泉街', '湖街',
          '监狱', '花街', '电厂', '叶街', '林街', '南站', '日街', '公益', '月街', '星街',
          '停车场', '云街', '机会', '霞街', '虹街', '西站', '山街', '岭街', '水厂', '峰街',
          '去监狱', '海街', '湾街', '公益', '港街', '北站', '机会', '金街', '奢侈税', '银街')
_COLORS = {3: '#aa755d', 4: '#69b5cb', 5: '#cc76a4', 6: '#e09a4e', 7: '#cf686c', 8: '#c5ad45', 9: '#6caa83', 10: '#788bc8'}
# Original short Chinese event copy; numeric effects implement the classic base deck.
_CARDS = {
    'chance': [('jail_card', '保留出狱卡', 0), ('repairs', '房屋维修：每屋25，旅馆100', (25, 100)),
               ('pay', '交通罚款15', 15), ('pay_each', '社区宴会：付每位玩家50', 50),
               ('back', '退后三格', 3), ('utility', '前往最近设施，特殊租金为新骰点的10倍', 0),
               ('gain', '分红50', 50), ('railroad', '前往最近车站，支付双倍租金', 0),
               ('pay', '公共服务费15', 15), ('advance', '前往东站', 5),
               ('advance', '前往银街', 39), ('advance', '前往虹街', 24),
               ('gain', '储蓄到期150', 150), ('advance', '前往起点领取200', 0),
               ('jail', '立即入狱', 0), ('advance', '前往花街', 11)],
    'chest': [('jail_card', '保留出狱卡', 0), ('gain', '比赛奖励10', 10),
              ('gain', '旧物出售50', 50), ('gain', '保险到期100', 100), ('gain', '退税20', 20),
              ('gain', '假期储蓄100', 100), ('gain', '遗产100', 100), ('gain', '顾问费25', 25),
              ('pay', '住院费100', 100), ('gain', '银行补款200', 200), ('pay', '学费50', 50),
              ('pay', '诊疗费50', 50), ('collect_each', '生日：每位玩家付你10', 10),
              ('advance', '前往起点领取200', 0), ('repairs', '街道维修：每屋40，旅馆115', (40, 115)),
              ('jail', '立即入狱', 0)]}


def new_tiles():
    special = {0: 'start', 2: 'chest', 4: 'tax', 7: 'chance', 10: 'jail', 17: 'chest',
               20: 'parking', 22: 'chance', 30: 'go_to_jail', 33: 'chest', 36: 'chance', 38: 'tax'}
    tiles = [dict(id=i, name=name, kind=special.get(i, 'property'), group=None, color='#d8cbdc',
                  price=0, rents=[], build_cost=0, mortgage_value=0, redemption_cost=0,
                  owner=None, level=0, mortgaged=False) for i, name in enumerate(_NAMES)]
    for i, price, group, *rents in _DATA:
        tiles[i].update(price=price, group=group, color=_COLORS.get(group, '#9486ab'),
                        kind='railroad' if group == 1 else 'utility' if group == 2 else 'property',
                        rents=rents, build_cost=((group - 3) // 2 + 1) * 50 if group >= 3 else 0,
                        mortgage_value=price // 2, redemption_cost=(price * 55 + 99) // 100)
    return tiles


class Monopoly(GamePlugin):
    game_type = 'monopoly'
    display_name = '大富翁'
    category = 'tabletop'
    min_players, max_players, recommended_players = 2, 6, 4
    allowed_player_counts = (2, 3, 4, 5, 6)
    supports_npcs = True
    uses_local_npc_strategy = True
    mcp_immediate_public_events = True
    mcp_event_key = 'monopoly'
    # Cash here never reaches platform chip settlement.
    supports_stakes = False
    rules_text = '''【目标】
2～6人，推荐4人。每人局内现金1500，经过或停在起点领取200。经营地产，使其他玩家破产，最后存活者获胜；没有固定回合或时长上限。局内现金与平台筹码完全分离。

【掷骰与地产】
- 两颗骰子由服务端掷出并保存。双骰可再掷；连续第三次双骰直接入狱。完成落地结算后可经营，再结束回合或继续掷骰。
- 无主地产可按标价购买，放弃或买不起则公开拍卖。包括放弃购买者在内的存活玩家依次出价或退出；至少加价1，最多出到当前现金。无人应价则留归银行，最高出价者自动交割。
- 踩对手地产自动付租。集齐同色时未抵押空地租金翻倍；有房按租金表。车站按持有数量收25/50/100/200；设施按本次骰点×4，两处齐全×10。已抵押地块不收租，在押者仍收租。

【经营与交易】
- 自己掷骰前、落地后及筹款时可管理资产。整套无抵押才能均衡建房，每次只升一级，最高四屋再升旅馆；卖房反向均衡，退还每级造价的一半。
- 银行共有32屋、12旅馆。旅馆降级需银行有4屋；破产清算不受库存限制。
- 同组全部无建筑才能抵押或交易。抵押获得标价一半；赎回付抵押本金加10%利息，向上取整。
- 交易可交换多块地产和双方现金。提出即发起者确认，报价挂起且不打断当前回合；接收方在自己的下个正常回合先接受或拒绝，随后继续该回合。全桌最多一笔待处理报价，条件失效自动取消，成交时同时重新验证并交割。每个正常回合最多提出3笔交易；抵押状态随产权转移，不额外收转让税。出狱卡不可交易。

【事件与监狱】
- 机会、公益各16张，洗牌后逐张循环，顺序不公开。事件含收支、玩家间支付、移动、维修及出狱卡。出狱卡保留至使用或破产，再放回牌堆底。
- 入狱不领起点奖励。回合开始可付50或用卡出狱，再正常掷骰；也可尝试双骰出狱并移动，此次不奖励再掷。第三次失败必须付50后按本次点数移动，可筹款或破产。
- 免费停车没有奖金。所得税200，奢侈税100。随机移动按事件自动结算，倒退不领起点奖励。

【债务与胜负】
- 不足支付时暂停结算，允许卖房、抵押筹款；交易报价仍须等接收方的正常回合，不能立即解债。筹足自动扣款一次。也可确认破产离场。
- 破产先以半价清算建筑，剩余现金与地产交给玩家债权人，抵押状态保留且不即时收利息；欠银行则地产解除抵押后逐块拍卖。剩余欠款免除。主动认输按欠当前债权人处理，无债务则归银行。
- 本版交易和经营只在上述窗口进行；抵押转让不另收即时利息。这些约定替代不同桌游版本的争议细则。'''
    move_format = ('从 legal_actions 复制动作（含 action_seq）作为 move 提交，并携带房间 revision。'
                   '自定义出价：{"action":"bid","amount":整数,"action_seq":当前值}。'
                   '交易按 private_state.trade_options 组装：{"action":"propose_trade","to":"参与者ID",'
                   '"give_cash":0,"take_cash":0,"give_tiles":[地块ID],"take_tiles":[],"action_seq":当前值}。'
                   '只有接收方本人在自己的正常回合可 respond_trade，accept 为布尔值；拒绝也必须匹配当前序号。')

    def __init__(self, rng=None):
        self.rng = rng or random.SystemRandom()

    def initial_state(self):
        return self.initialize([])

    def tokens_for(self, participants):
        return [f'P{i+1}' for i in range(len(participants))]

    def initialize(self, participants):
        if len(participants) > 6:
            raise ValueError('最多6人')
        players = [dict(player_id=p['player_id'], name=p.get('display_name', f'玩家{i+1}'),
                        token=p.get('token', f'P{i+1}'), cash=1500, position=0, bankrupt=False,
                        jailed=False, jail_turns=0, jail_cards=[]) for i, p in enumerate(participants)]
        first = players[0]['player_id'] if players else None
        decks = {key: list(range(len(cards))) for key, cards in _CARDS.items()}
        for deck in decks.values():
            self.rng.shuffle(deck)
        return dict(version=1, players=players, tiles=new_tiles(), phase='roll', current_player_id=first,
                    turn_player_id=first, turn_number=1, action_seq=0, dice=[], doubles=0, extra_roll=False,
                    auction=None, trade=None, debt=None, last_event='', last_action_note='', last_card_events=[],
                    _decks=decks, _charges=[], _resume=None, _auction_queue=[], _auction_return=None,
                    _trade_return=None, _trade_assets=None, _trade_proposed_turn=None, trades_this_turn=0)

    def prepare_opening_state(self, state, first_player_id, participants):
        state['turn_player_id'] = state['current_player_id'] = first_player_id
        return state

    @staticmethod
    def player(state, pid):
        return next(p for p in state['players'] if p['player_id'] == pid)

    @staticmethod
    def active(state):
        return [p['player_id'] for p in state['players'] if not p['bankrupt']]

    @staticmethod
    def group(state, tile):
        return [t for t in state['tiles'] if t['group'] == tile['group']] if tile['group'] else [tile]

    def monopoly(self, state, tile):
        return bool(tile['owner'] and all(t['owner'] == tile['owner'] for t in self.group(state, tile)))

    @staticmethod
    def supply(state):
        return {'houses': 32 - sum(t['level'] for t in state['tiles'] if t['level'] < 5),
                'hotels': 12 - sum(t['level'] == 5 for t in state['tiles'])}

    def rent(self, state, tile, dice=None):
        if not tile['owner'] or tile['mortgaged']:
            return 0
        owned = sum(t['owner'] == tile['owner'] for t in self.group(state, tile))
        if tile['kind'] == 'railroad':
            return 25 * 2 ** (owned - 1)
        if tile['kind'] == 'utility':
            return (10 if owned == 2 else 4) * sum(dice if dice is not None else state['dice'])
        return tile['rents'][tile['level']] * (2 if tile['level'] == 0 and self.monopoly(state, tile) else 1)

    def _asset_actions(self, state, pid):
        actions = []
        cash = self.player(state, pid)['cash']
        supply = self.supply(state)
        debt = state['phase'] == 'debt'
        for t in state['tiles']:
            if t['owner'] != pid:
                continue
            group = self.group(state, t)
            if t['level'] and t['level'] == max(g['level'] for g in group):
                if t['level'] < 5 or supply['houses'] >= 4:
                    actions.append(dict(action='sell_building', tile_id=t['id']))
            if all(g['level'] == 0 for g in group) and not t['mortgaged']:
                actions.append(dict(action='mortgage', tile_id=t['id']))
            if not debt and t['mortgaged'] and cash >= t['redemption_cost']:
                actions.append(dict(action='redeem', tile_id=t['id']))
            if (not debt and t['kind'] == 'property' and self.monopoly(state, t)
                    and not any(g['mortgaged'] for g in group) and t['level'] < 5
                    and t['level'] == min(g['level'] for g in group) and cash >= t['build_cost']
                    and supply['hotels' if t['level'] == 4 else 'houses'] > 0):
                actions.append(dict(action='build', tile_id=t['id']))
        return actions

    def _trade_gate(self, state, pid):
        # A temporary debt actor may offer to the normal turn owner. Even then
        # wait for a later normal turn, not the continuation of this one.
        trade = state.get('trade')
        return bool(trade and trade['to'] == pid and pid == state['turn_player_id'] and (
            state['phase'] == 'trade' or (
                pid == state['current_player_id'] and state['phase'] in ('roll', 'purchase', 'manage')
                and state['turn_number'] > (state.get('_trade_proposed_turn') or 0))))

    def _clear_trade(self, state):
        # Only legacy saves used an interrupting trade phase. Finish those in
        # their saved continuation; new offers never change the normal phase.
        if state['phase'] == 'trade':
            resume = state.get('_trade_return') or {}
            state.update(phase=resume.get('phase', 'roll'),
                         turn_player_id=resume.get('player_id', state['current_player_id']))
        state.update(trade=None, _trade_return=None, _trade_assets=None, _trade_proposed_turn=None)

    def _cancel_invalid_trade(self, state):
        if state.get('trade'):
            try:
                self._validate_trade(state, state['trade'])
            except ValueError:
                self._clear_trade(state)
                self.note(state, '交易条件已失效，报价已取消。')

    def legal_actions(self, state, pid):
        if state['phase'] == 'finished' or pid != state['turn_player_id'] or pid not in self.active(state):
            return []
        p = self.player(state, pid)
        phase = state['phase']
        actions = []
        if self._trade_gate(state, pid):
            actions = [dict(action='respond_trade', accept=False), dict(action='respond_trade', accept=True)]
            if phase == 'trade':
                # An invalid legacy offer can always be declined to resume play.
                try:
                    self._validate_trade(state, state['trade'])
                except ValueError:
                    actions.pop()
        elif phase == 'auction':
            bid = state['auction']['bid']
            actions.append(dict(action='pass_bid'))
            for amount in sorted({bid + 1, bid + 10, bid + 50}):
                if amount <= p['cash']:
                    actions.append(dict(action='bid', amount=amount))
        else:
            actions = self._asset_actions(state, pid)
            if phase == 'roll':
                actions.insert(0, dict(action='roll'))
                if p['jailed']:
                    if p['cash'] >= 50:
                        actions.append(dict(action='pay_bail'))
                    if p['jail_cards']:
                        actions.append(dict(action='use_jail_card'))
            elif phase == 'purchase':
                t = state['tiles'][p['position']]
                if p['cash'] >= t['price']:
                    actions.insert(0, dict(action='buy'))
                actions.append(dict(action='auction'))
            elif phase == 'manage':
                actions.insert(0, dict(action='roll' if state['extra_roll'] else 'end_turn'))
            elif phase == 'debt':
                actions.append(dict(action='bankrupt'))
        return [dict(a, action_seq=state['action_seq']) for a in actions]

    def trade_options(self, state, pid):
        if (state.get('trade') or pid != state['turn_player_id'] or state['phase'] not in ('roll', 'purchase', 'manage', 'debt')
                or state['trades_this_turn'] >= 3 or pid not in self.active(state)):
            return {'partners': []}
        tradable = lambda who: [t['id'] for t in state['tiles'] if t['owner'] == who
                                and not any(g['level'] for g in self.group(state, t))]
        partners = [other for other in self.active(state) if other != pid]
        return dict(partners=partners, give_tiles=tradable(pid),
                    take_tiles_by_player={other: tradable(other) for other in partners},
                    cash_by_player={p['player_id']: p['cash'] for p in state['players'] if not p['bankrupt']})

    @staticmethod
    def _integer(value, maximum=10**9):
        return type(value) is int and 0 <= value <= maximum

    def _validate_trade(self, state, trade):
        a, b = trade['from'], trade['to']
        if a == b or a not in self.active(state) or b not in self.active(state):
            raise ValueError('交易对象必须是另一名存活玩家')
        for field, pid in (('give_cash', a), ('take_cash', b)):
            amount = trade[field]
            if not self._integer(amount) or amount > self.player(state, pid)['cash']:
                raise ValueError('交易现金必须是余额范围内的非负整数')
        for field, pid in (('give_tiles', a), ('take_tiles', b)):
            ids = trade[field]
            if (not isinstance(ids, list) or len(ids) > 28 or any(not self._integer(i, 39) for i in ids)
                    or len(ids) != len(set(ids))):
                raise ValueError('地产列表必须为不重复的合法整数ID')
            for i in ids:
                t = state['tiles'][i]
                if t['owner'] != pid or any(g['level'] for g in self.group(state, t)):
                    raise ValueError('只能交易本人产权且整组无建筑的地产')
        # Newly proposed terms use live assets; pending offers keep the agreed
        # mortgage status, including estates already mortgaged at proposal time.
        if trade is state.get('trade'):
            for original in state.get('_trade_assets') or []:
                tile = state['tiles'][original['id']]
                if tile['mortgaged'] != original['mortgaged']:
                    raise ValueError('交易地产的抵押状态已变化')
        if not any(trade[f] for f in ('give_cash', 'take_cash', 'give_tiles', 'take_tiles')):
            raise ValueError('不能提交空交易')

    def validate_action(self, state, move, actor):
        pid = actor['player_id']
        if not isinstance(move, dict) or not self._integer(move.get('action_seq')) or move['action_seq'] != state['action_seq']:
            raise ValueError('action_seq 已变化或缺失，请刷新局面后重试')
        if pid != state['turn_player_id'] or pid not in self.active(state) or state['phase'] == 'finished':
            raise ValueError('当前没有行动权')
        action = move.get('action')
        if action == 'propose_trade':
            if state.get('trade'):
                raise ValueError('已有待处理报价，请等待接收方回应或报价失效')
            if set(move) != {'action', 'action_seq', 'to', 'give_cash', 'take_cash', 'give_tiles', 'take_tiles'}:
                raise ValueError('交易参数不完整或包含未知字段')
            if move['to'] not in self.trade_options(state, pid)['partners']:
                raise ValueError('此时不可向该玩家提出交易（每回合最多3笔）')
            self._validate_trade(state, dict(move, **{'from': pid}))
            return
        if action == 'bid' and state['phase'] == 'auction':
            if (set(move) != {'action', 'action_seq', 'amount'} or not self._integer(move.get('amount'))
                    or not state['auction']['bid'] < move['amount'] <= self.player(state, pid)['cash']):
                raise ValueError('出价必须高于现价且不能超过现金')
            return
        # Exact shapes and exact scalar types prevent bool-as-int ambiguity.
        if not any(move == a and all(type(move[k]) is type(a[k]) for k in a) for a in self.legal_actions(state, pid)):
            raise ValueError('该动作不在当前合法行动中')
        if action == 'respond_trade' and move['accept']:
            self._validate_trade(state, state['trade'])

    def validate_move(self, state, move, mark):
        raise ValueError('大富翁需要参与者身份')

    def apply_move(self, state, move, mark):
        raise ValueError('大富翁需要参与者身份')

    @staticmethod
    def note(state, text):
        state['last_action_note'] = (state['last_action_note'] + ' ' + text).strip()

    def _next_turn(self, state):
        ids = [p['player_id'] for p in state['players']]
        start = ids.index(state['current_player_id'])
        for delta in range(1, len(ids) + 1):
            nxt = ids[(start + delta) % len(ids)]
            if nxt in self.active(state):
                state.update(current_player_id=nxt, turn_player_id=nxt, phase='roll', dice=[], doubles=0,
                             extra_roll=False, turn_number=state['turn_number'] + 1, trades_this_turn=0)
                self._cancel_invalid_trade(state)
                return

    def _jail(self, state, p):
        p.update(position=10, jailed=True, jail_turns=0)
        state.update(extra_roll=False, doubles=0, phase='manage')
        self.note(state, f"{p['name']}入狱。")

    def _charge(self, state, payer, amount, creditor=None, reason='费用'):
        if amount > 0 and payer != creditor:
            state['_charges'].append(dict(payer=payer, amount=amount, creditor=creditor, reason=reason))

    def _move_to(self, state, p, dest, salary=True, special=None):
        if salary and dest < p['position']:
            p['cash'] += 200
            self.note(state, '经过起点，领取200。')
        p['position'] = dest
        self._land(state, p, special)

    def _land(self, state, p, special=None):
        state['phase'] = 'manage'
        t = state['tiles'][p['position']]
        self.note(state, f"停在{t['name']}。")
        if t['price']:
            if t['owner'] is None:
                state['phase'] = 'purchase'
            elif t['owner'] != p['player_id'] and not t['mortgaged']:
                rent = self.rent(state, t)
                if special == 'railroad':
                    rent *= 2
                elif special == 'utility':
                    dice = [self.rng.randint(1, 6), self.rng.randint(1, 6)]
                    state['event_dice'] = dice
                    rent = sum(dice) * 10
                    self.note(state, f'设施事件骰点{dice[0]}+{dice[1]}。')
                self._charge(state, p['player_id'], rent, t['owner'], t['name'] + '租金')
        elif t['kind'] == 'tax':
            self._charge(state, p['player_id'], 200 if t['id'] == 4 else 100, reason=t['name'])
        elif t['kind'] == 'go_to_jail':
            self._jail(state, p)
        elif t['kind'] in _CARDS:
            self._event(state, p, t['kind'])

    def _event(self, state, p, deck_name):
        deck = state['_decks'][deck_name]
        index = deck.pop(0)
        kind, text, value = _CARDS[deck_name][index]
        # Only already drawn cards are public. A backwards move can draw a
        # second card in the same action, so retain that small ordered batch.
        seq = state['action_seq'] + 1
        if not state.get('last_card_events') or state['last_card_events'][-1]['action_seq'] != seq:
            state['last_card_events'] = []
        events = state['last_card_events']
        event = dict(event_id=f'{seq}:{len(events) + 1}', action_seq=seq,
                     player_id=p['player_id'], deck=deck_name, text=text, summary='')
        events.append(event)
        state['last_event'] = text
        self.note(state, text + '。')
        if kind == 'jail_card':
            p['jail_cards'].append(deck_name)
            event['summary'] = '出狱卡已收入手中'
            return
        deck.append(index)
        pid = p['player_id']
        if kind == 'gain':
            p['cash'] += value
            event['summary'] = f'现金 +{value}'
        elif kind == 'pay':
            self._charge(state, pid, value, reason=text)
            event['summary'] = f'应付 {value}'
        elif kind in ('pay_each', 'collect_each'):
            event['summary'] = f'向每位其他玩家支付 {value}' if kind == 'pay_each' else f'每位其他玩家付你 {value}'
            for other in self.active(state):
                if other != pid:
                    self._charge(state, pid if kind == 'pay_each' else other, value,
                                 other if kind == 'pay_each' else pid, text)
        elif kind == 'repairs':
            amount = sum((value[1] if t['level'] == 5 else value[0] * t['level'])
                         for t in state['tiles'] if t['owner'] == pid)
            self._charge(state, pid, amount, reason=text)
            event['summary'] = f'维修费 {amount}'
        elif kind == 'jail':
            self._jail(state, p)
            event['summary'] = '进入监狱，不领取起点奖励'
        elif kind == 'advance':
            event['summary'] = f"前往{state['tiles'][value]['name']}"
            if value < p['position']:
                event['summary'] += ' · 经过起点领取 200'
            self._move_to(state, p, value)
        elif kind == 'back':
            dest = (p['position'] - value) % 40
            event['summary'] = f"退后{value}格，抵达{state['tiles'][dest]['name']}"
            self._move_to(state, p, dest, salary=False)
        elif kind in ('utility', 'railroad'):
            dest = next((p['position'] + n) % 40 for n in range(1, 41)
                        if state['tiles'][(p['position'] + n) % 40]['kind'] == kind)
            event['summary'] = f"前往{state['tiles'][dest]['name']}"
            self._move_to(state, p, dest, special=kind)

    def _roll(self, state, p):
        state['dice'] = dice = [self.rng.randint(1, 6), self.rng.randint(1, 6)]
        state.pop('event_dice', None)
        double = dice[0] == dice[1]
        self.note(state, f"{p['name']}掷出{dice[0]}+{dice[1]}。")
        state['extra_roll'] = False
        if p['jailed']:
            p['jail_turns'] += 1
            if double:
                p.update(jailed=False, jail_turns=0)
            elif p['jail_turns'] < 3:
                state['phase'] = 'manage'
                self.note(state, '未掷出双骰，继续在押。')
                return
            else:
                state['_resume'] = {'kind': 'jail_move', 'player_id': p['player_id'], 'steps': sum(dice)}
                self._charge(state, p['player_id'], 50, reason='第三次出狱保释金')
                state['phase'] = 'manage'
                return
        else:
            state['doubles'] = state['doubles'] + 1 if double else 0
            if state['doubles'] == 3:
                self._jail(state, p)
                return
            state['extra_roll'] = double
        self._move_to(state, p, (p['position'] + sum(dice)) % 40)

    def _drain_charges(self, state):
        # The continuation is persisted, including third-jail-roll movement.
        if state['_charges'] and state['_resume'] is None:
            state['_resume'] = {'kind': 'phase', 'phase': state['phase']}
        while state['_charges']:
            charge = state['_charges'][0]
            payer = self.player(state, charge['payer'])
            if payer['bankrupt'] or (charge['creditor'] and charge['creditor'] not in self.active(state)):
                state['_charges'].pop(0)
                continue
            if payer['cash'] < charge['amount']:
                state.update(phase='debt', turn_player_id=payer['player_id'], debt=deepcopy(charge))
                self.note(state, f"{payer['name']}需筹款偿还{charge['amount']}（{charge['reason']}）。")
                return
            payer['cash'] -= charge['amount']
            if charge['creditor']:
                self.player(state, charge['creditor'])['cash'] += charge['amount']
            self.note(state, f"{payer['name']}支付{charge['amount']}（{charge['reason']}）。")
            state['_charges'].pop(0)
        state['debt'] = None
        resume = state['_resume']
        state['_resume'] = None
        if resume:
            state['turn_player_id'] = state['current_player_id']
            if state['current_player_id'] not in self.active(state):
                self._next_turn(state)
            elif resume['kind'] == 'jail_move':
                p = self.player(state, resume['player_id'])
                p.update(jailed=False, jail_turns=0)
                self._move_to(state, p, (p['position'] + resume['steps']) % 40)
                self._drain_charges(state)
            else:
                state['phase'] = resume['phase']
        if state['phase'] != 'debt' and state['_auction_queue'] and state['auction'] is None:
            self._start_auction(state)

    def _start_auction(self, state):
        if not state['_auction_return']:
            state['_auction_return'] = {'phase': state['phase'], 'player_id': state['turn_player_id']}
        tile_id = state['_auction_queue'].pop(0)
        ids = self.active(state)
        start = state['turn_player_id'] if state['turn_player_id'] in ids else ids[0]
        state.update(phase='auction', turn_player_id=start,
                     auction=dict(tile_id=tile_id, bid=0, highest_bidder=None, eligible=ids))

    def _auction_step(self, state, pid, move):
        auction = state['auction']
        if move['action'] == 'bid':
            auction.update(bid=move['amount'], highest_bidder=pid)
            self.note(state, f"{self.player(state, pid)['name']}出价{move['amount']}。")
        else:
            auction['eligible'].remove(pid)
        candidates = [p for p in auction['eligible'] if p != auction['highest_bidder']]
        if not candidates:
            self._finish_auction(state)
            return
        ids = [p['player_id'] for p in state['players']]
        start = ids.index(pid)
        state['turn_player_id'] = next(ids[(start + i) % len(ids)] for i in range(1, len(ids) + 1)
                                       if ids[(start + i) % len(ids)] in candidates)

    def _finish_auction(self, state):
        auction = state['auction']
        tile = state['tiles'][auction['tile_id']]
        if auction['highest_bidder']:
            winner = self.player(state, auction['highest_bidder'])
            winner['cash'] -= auction['bid']
            tile['owner'] = winner['player_id']
            self.note(state, f"{winner['name']}以{auction['bid']}买下{tile['name']}。")
        else:
            self.note(state, f"{tile['name']}流拍，留归银行。")
        state['auction'] = None
        if state['_auction_queue']:
            self._start_auction(state)
        else:
            resume = state['_auction_return']
            state.update(phase=resume['phase'], turn_player_id=resume['player_id'], _auction_return=None)
            if state['current_player_id'] not in self.active(state):
                self._next_turn(state)

    def _eliminate(self, state, pid, creditor):
        p = self.player(state, pid)
        if p['bankrupt']:
            return
        if creditor not in self.active(state) or creditor == pid:
            creditor = None
        for t in state['tiles']:
            if t['owner'] == pid:
                p['cash'] += t['level'] * t['build_cost'] // 2
                t['level'] = 0
                t['owner'] = creditor
                if not creditor:
                    t['mortgaged'] = False
                    state['_auction_queue'].append(t['id'])
        if creditor:
            self.player(state, creditor)['cash'] += p['cash']
        p.update(cash=0, bankrupt=True, jailed=False, jail_turns=0)
        for deck_name in p['jail_cards']:
            state['_decks'][deck_name].append(0)
        p['jail_cards'] = []
        self.note(state, f"{p['name']}破产离场。")
        self._cancel_invalid_trade(state)

    def _terminal(self, state):
        ids = self.active(state)
        if len(ids) <= 1 and len(state['players']) >= 2:
            state.update(phase='finished', turn_player_id=None, auction=None, trade=None, debt=None,
                         _charges=[], _auction_queue=[], _resume=None, _auction_return=None, _trade_return=None,
                         _trade_assets=None, _trade_proposed_turn=None)
            return {'winner_player_id': ids[0], 'draw': False} if ids else {'draw': True}
        return None

    def apply_action(self, state, move, actor):
        self.validate_action(state, move, actor)
        state = deepcopy(state)
        state['last_action_note'] = ''
        pid = actor['player_id']
        p = self.player(state, pid)
        action = move['action']
        if action == 'roll':
            self._roll(state, p)
            self._drain_charges(state)
        elif action == 'end_turn':
            self._next_turn(state)
        elif action == 'buy':
            t = state['tiles'][p['position']]
            p['cash'] -= t['price']
            t['owner'] = pid
            state['phase'] = 'manage'
            self.note(state, f"{p['name']}以{t['price']}买下{t['name']}。")
        elif action == 'auction':
            state['phase'] = 'manage'
            state['_auction_queue'].append(p['position'])
            self._start_auction(state)
        elif action in ('bid', 'pass_bid'):
            self._auction_step(state, pid, move)
        elif action == 'propose_trade':
            state['trade'] = {k: deepcopy(move[k]) for k in ('to', 'give_cash', 'take_cash', 'give_tiles', 'take_tiles')}
            state['trade']['from'] = pid
            state['_trade_assets'] = [dict(id=i, mortgaged=state['tiles'][i]['mortgaged'])
                                      for i in move['give_tiles'] + move['take_tiles']]
            state['_trade_proposed_turn'] = state['turn_number']
            state['trades_this_turn'] += 1
            self.note(state, '已提出交易，等待接收方在自己的正常回合回应。')
        elif action == 'respond_trade':
            trade = state['trade']
            if move['accept']:
                a, b = self.player(state, trade['from']), self.player(state, trade['to'])
                delta = trade['give_cash'] - trade['take_cash']
                a['cash'] -= delta
                b['cash'] += delta
                for ids, owner in ((trade['give_tiles'], trade['to']), (trade['take_tiles'], trade['from'])):
                    for i in ids:
                        state['tiles'][i]['owner'] = owner
            self.note(state, '交易已同时交割。' if move['accept'] else '交易已拒绝。')
            self._clear_trade(state)
            # Legacy interruptions may have queued debt or bank auctions.
            self._drain_charges(state)
        elif action in ('build', 'sell_building', 'mortgage', 'redeem'):
            t = state['tiles'][move['tile_id']]
            if action == 'build':
                t['level'] += 1
                p['cash'] -= t['build_cost']
            elif action == 'sell_building':
                t['level'] -= 1
                p['cash'] += t['build_cost'] // 2
            elif action == 'mortgage':
                t['mortgaged'] = True
                p['cash'] += t['mortgage_value']
            else:
                t['mortgaged'] = False
                p['cash'] -= t['redemption_cost']
            self.note(state, f"{t['name']}：" + {'build': '建造一级', 'sell_building': '出售一级建筑', 'mortgage': '已抵押', 'redeem': '已赎回'}[action] + '。')
            if state['phase'] == 'debt':
                self._drain_charges(state)
        elif action in ('pay_bail', 'use_jail_card'):
            if action == 'pay_bail':
                p['cash'] -= 50
            else:
                state['_decks'][p['jail_cards'].pop(0)].append(0)
            p.update(jailed=False, jail_turns=0)
            self.note(state, '已出狱，可以掷骰。')
        elif action == 'bankrupt':
            self._eliminate(state, pid, state['debt']['creditor'])
            if not self._terminal(state):
                self._drain_charges(state)
        self._cancel_invalid_trade(state)
        state['action_seq'] += 1
        result = self._terminal(state)
        return MoveResult(state=state, next_player_id=state['turn_player_id'], result=result,
                          participant_activity={p['player_id']: 'eliminated' for p in state['players'] if p['bankrupt']},
                          note=state['last_action_note'], public_event=self._public_delta(state))

    def _public_delta(self, state):
        return dict(monopoly=dict(phase=state['phase'], dice=state['dice'], action_seq=state['action_seq'],
                                  turn_player_id=state['turn_player_id'], note=state['last_action_note'],
                                  current_player_id=state['current_player_id'],
                                  turn_number=state['turn_number'], extra_roll=state['extra_roll'],
                                  doubles=state['doubles'], trades_this_turn=state['trades_this_turn'],
                                  bank_supply=self.supply(state), last_card_events=deepcopy(state.get('last_card_events', [])),
                                  auction=deepcopy(state['auction']), trade=deepcopy(state['trade']), debt=deepcopy(state['debt']),
                                  tiles=[{k: t[k] for k in ('id', 'owner', 'level', 'mortgaged')}
                                         for t in state['tiles'] if t['price']],
                                  players=[{k: p[k] for k in ('player_id', 'cash', 'position', 'bankrupt', 'jailed', 'jail_turns')}
                                           for p in state['players']]))

    def apply_resignation(self, state, resigned_player_id, participants):
        pid = resigned_player_id
        state['last_action_note'] = ''
        creditor = state['debt']['creditor'] if state['debt'] and state['debt']['payer'] == pid else None
        self._eliminate(state, pid, creditor)
        state['action_seq'] += 1
        if self._terminal(state):
            return
        if state['phase'] == 'trade':
            # Preserve a still-valid legacy interruption until its response.
            return
        if state['auction']:
            auction = state['auction']
            auction['eligible'] = [p for p in auction['eligible'] if p != pid]
            if auction['highest_bidder'] == pid:
                auction.update(highest_bidder=None, bid=0, eligible=self.active(state))
            if state['current_player_id'] == pid:
                self._next_turn(state)
                state['_auction_return'] = {'phase': 'roll', 'player_id': state['current_player_id']}
                state['phase'] = 'auction'
            if not [p for p in auction['eligible'] if p != auction['highest_bidder']]:
                self._finish_auction(state)
            elif state['turn_player_id'] == pid or state['turn_player_id'] not in auction['eligible'] or state['turn_player_id'] == auction['highest_bidder']:
                state['turn_player_id'] = next(p for p in auction['eligible'] if p != auction['highest_bidder'])
            return
        if state['current_player_id'] == pid and state['phase'] != 'debt':
            self._next_turn(state)
        self._drain_charges(state)

    def check_winner(self, state):
        result = self._terminal(deepcopy(state))
        if not result:
            return None
        return 'draw' if result.get('draw') else self.player(state, result['winner_player_id'])['token']

    def result_for(self, state, participants):
        return self._terminal(deepcopy(state))

    def public_state(self, state, participants):
        public = {k: deepcopy(v) for k, v in state.items() if not k.startswith('_')}
        for p in public['players']:
            p.pop('jail_cards', None)
            owned = [t for t in state['tiles'] if t['owner'] == p['player_id']]
            p['property_count'] = len(owned)
            p['asset_value'] = p['cash'] + sum(t['price'] - (t['mortgage_value'] if t['mortgaged'] else 0)
                                             + t['level'] * t['build_cost'] for t in owned)
        for t in public['tiles']:
            t['rent'] = self.rent(state, t)
        public['bank_supply'] = self.supply(state)
        public['legal_actions'] = self.legal_actions(state, state['turn_player_id'])
        return public

    def private_state(self, state, viewer, participants):
        pid = viewer['player_id']
        p = next((p for p in state['players'] if p['player_id'] == pid), None)
        return dict(legal_actions=self.legal_actions(state, pid), trade_options=self.trade_options(state, pid),
                    # Generic resign/leave events do not carry plugin deltas.
                    # Give the decision owner enough public context even when
                    # such a lifecycle change just opened a bank auction.
                    decision_context=self._public_delta(state)['monopoly'],
                    jail_cards=len(p['jail_cards']) if p else 0,
                    guide='复制legal_actions并携带revision；action_seq防重放。交易由trade_options构造，接收方在自己的正常回合先响应，待处理报价不打断其他玩家。先保证现金，再集齐同色均衡建房。债务筹足自动清偿。')

    def terminal_public_state(self, state, participants):
        public = self.public_state(state, participants)
        public.update(phase='finished', turn_player_id=None, legal_actions=[])
        return public

    def mcp_event(self, event, previous):
        from .mcp_incremental import compact_event, changed
        event = compact_event(event)
        value = (event.get('move') or {}).get('monopoly')
        if value is not None:
            old = previous.get('monopoly', {})
            delta = changed(value, old)
            for key, identity, fields in (
                ('tiles', 'id', ('id', 'owner', 'level', 'mortgaged')),
                ('players', 'player_id', ('player_id', 'cash', 'position', 'bankrupt', 'jailed', 'jail_turns')),
            ):
                before = {v[identity]: v for v in old.get(key, [])}
                updates = [[v[k] for k in fields] for v in value[key]
                           if v != before.get(v[identity])]
                delta.pop(key, None)
                if updates:
                    delta[key] = updates
            event['move'] = {'monopoly': delta}
        return event

    def mcp_private_state(self, private_state, viewer, participants):
        private = deepcopy(private_state)
        private.pop('decision_context', None)
        private.pop('guide', None)
        # Cash and tradable estates can be derived from the public ledger and
        # bootstrap rules. Keep availability, not a second copy of that ledger.
        options = private.pop('trade_options', {})
        if options.get('partners'):
            private['trade_options'] = {'partners': options['partners']}
        return private

    def mcp_bootstrap_state(self, public_state, viewer, participants):
        public = super().mcp_bootstrap_state(public_state, viewer, participants)
        public['delta_format'] = {
            'monopoly': 'Shallow replacements; omitted keys unchanged, null clears. tiles/players upsert by first column.',
            'events': 'Apply in order; skip revision <= restored snapshot revision. public_state after resign/leave repairs the current public ledger.',
            'tiles': ['id', 'owner', 'level', 'mortgaged'],
            'players': ['player_id', 'cash', 'position', 'bankrupt', 'jailed', 'jail_turns'],
            'trade_options': 'partners only; cash/ownership from public state. An entire color group must have no buildings to trade its estates.',
        }
        public['trade_action_spec'] = {'action': 'propose_trade', 'to': 'partner player_id',
            'give_cash': 'nonnegative integer', 'take_cash': 'nonnegative integer',
            'give_tiles': 'owned tradable tile IDs', 'take_tiles': 'partner tradable tile IDs',
            'action_seq': 'current action_seq; cash must not exceed each owner balance'}
        return public

    mcp_snapshot_state = mcp_bootstrap_state

    def mcp_lifecycle_state(self, public):
        return self._public_delta(public)['monopoly']

    def participant_summary(self, state, participant, participants):
        p = next((p for p in state['players'] if p['player_id'] == participant['player_id']), None)
        return {'现金': p['cash'], '地产': p.get('property_count', 0)} if p else {}

    def npc_legal_actions(self, state, actor, participants):
        pid = actor['player_id']
        actions = self.legal_actions(state, pid)
        # Only actual system NPCs may initiate asset transfers. Temporary
        # assistance to a real seat must leave financial agreements to its owner.
        options = self.trade_options(state, pid)
        if (actor.get('participant_kind') == 'system_npc' and options['partners']
                and state['phase'] in ('roll', 'manage') and state['trades_this_turn'] == 0):
            cash = self.player(state, pid)['cash']
            for t in state['tiles']:
                if (t['kind'] != 'property' or t['owner'] not in options['partners'] or
                        t['id'] not in options['take_tiles_by_player'][t['owner']] or
                        not any(g['owner'] == pid for g in self.group(state, t))):
                    continue
                offer = t['price'] * 2
                if cash - offer >= 200:
                    actions.append(dict(action='propose_trade', to=t['owner'], give_cash=offer,
                                        take_cash=0, give_tiles=[], take_tiles=[t['id']], action_seq=state['action_seq']))
        return actions

    def npc_compact_rules(self, state, actor, participants):
        return ('局内现金1500，经过起点200；集齐同色并均衡建房收租，最后存活获胜。'
                '产权/现金/骰子服务端权威，复制legal_actions含action_seq。拍卖轮流加价或退出。'
                '债务时卖房抵押筹款；交易只能本席确认。设施4/10倍骰点，车站25/50/100/200。')

    def choose_local_npc_action(self, state, actor, participants):
        actions = self.npc_legal_actions(state, actor, participants)
        p = self.player(state, actor['player_id'])
        def find(action):
            return next((a for a in actions if a['action'] == action), None)
        # Temporary assistance always declines a financial agreement; a real
        # system NPC may accept only a non-losing offer according to book value.
        if find('respond_trade'):
            accept = False
            if actor.get('participant_kind') == 'system_npc':
                t = state['trade']
                value = lambda ids: sum(state['tiles'][i]['price'] - (state['tiles'][i]['redemption_cost'] if state['tiles'][i]['mortgaged'] else 0) for i in ids)
                accept = value(t['give_tiles']) + t['give_cash'] >= value(t['take_tiles']) + t['take_cash']
            return next((a for a in actions if a['accept'] == accept), find('respond_trade'))
        if state['phase'] == 'debt':
            return find('sell_building') or find('mortgage') or find('bankrupt')
        if state['phase'] == 'auction':
            tile = state['tiles'][state['auction']['tile_id']]
            affordable = [a for a in actions if a['action'] == 'bid' and a['amount'] <= min(tile['price'], p['cash'] - 150)]
            return affordable[-1] if affordable else find('pass_bid')
        if state['phase'] == 'purchase':
            tile = state['tiles'][p['position']]
            return find('buy') if find('buy') and p['cash'] - tile['price'] >= 100 else find('auction')
        build = next((a for a in actions if a['action'] == 'build' and p['cash'] - state['tiles'][a['tile_id']]['build_cost'] >= 180), None)
        redeem = next((a for a in actions if a['action'] == 'redeem' and p['cash'] - state['tiles'][a['tile_id']]['redemption_cost'] >= 250), None)
        return build or redeem or find('propose_trade') or find('use_jail_card') or find('roll') or find('end_turn') or actions[0]

    def format_action(self, state, move, actor):
        labels = {'roll': '掷骰', 'buy': '买地', 'auction': '开启拍卖', 'end_turn': '结束回合',
                  'build': '建房', 'sell_building': '卖房', 'mortgage': '抵押', 'redeem': '赎回',
                  'bid': '出价', 'pass_bid': '退出拍卖', 'propose_trade': '提出交易',
                  'respond_trade': '回应交易', 'bankrupt': '破产', 'pay_bail': '付保释金', 'use_jail_card': '使用出狱卡'}
        return labels.get(move.get('action'), '大富翁行动') if isinstance(move, dict) else '大富翁行动'
