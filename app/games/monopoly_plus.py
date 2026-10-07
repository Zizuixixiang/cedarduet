"""大富翁·改（monopoly_plus）：在原版大富翁引擎上加速度骰、温柔监狱、拍卖行、
道具卡、事件卡、银行借贷、观战押注与地名牌。

原版 ``Monopoly`` 玩法不变：本插件继承它复用交易、拍卖、建房、抵押、债务与破产
清算，只覆盖需要扩展的步骤。所有随机结果（骰子、速度骰、牌序、巴士票、事件里的
随机目标）都在服务端动作里产生并存进 JSON 状态；道具手牌只进本人 private_state。
版本（地图/卡池/规则开关）见 ``monopoly_plus_editions``。
"""
from __future__ import annotations

import math
import re
from copy import deepcopy

from .base import MoveResult
from .monopoly import Monopoly, new_tiles
from . import monopoly_plus_editions as editions
from .monopoly_plus_editions import FINE_KINDS, ITEM_LABELS

SPEED_FACES = (1, 2, 3, 'bus', 'bus', 'mr')
SPEED_LABELS = {1: '1', 2: '2', 3: '3', 'bus': '巴士', 'mr': '大富翁先生'}
LOG_LIMIT = 40
PROPERTY_GROUPS = tuple(range(3, 11))
GROUP_NAMES = {3: '棕色组', 4: '浅蓝组', 5: '粉色组', 6: '橙色组', 7: '红色组', 8: '黄色组', 9: '绿色组', 10: '蓝色组'}
# 立牌/聊天文本的基本过滤：不收控制字符、尖括号、链接和几句常见脏话。
_BLOCKED_WORDS = ('傻逼', '傻b', 'sb', '操你', '草泥马', '妈的', '去死', '滚蛋', '贱人', '脑残', 'fuck', 'shit')
_URL = re.compile(r'(https?://|www\.|\.com\b|\.cn\b)', re.I)
_CONTROL = re.compile(r'[\x00-\x1f\x7f<>]')

NPC_CHAT_LINES = {
    '讲故事': '从前有只小机想学大富翁。它第一圈就踩进了监狱。出狱那天它买下了整条街。',
    '夸夸卡': '这一桌每个人都又聪明又好看，连掷骰子的手法都很优雅！',
    '藏头诗': '大街小巷灯如昼，富贵不过一掷间，翁然笑看满城楼。',
    '推销员': '我这块地交通便利、风水极佳，谁踩谁发财！',
    '真心话': '今天开心的事，是能和大家坐在一桌玩。',
    '天气预报': '大富翁城明天晴转多金，局部有租金雨，请带好钱包出门。',
}


def clean_text(value, limit: int, *, allow_empty: bool = False) -> str:
    """Normalize user text for public display; raise ValueError when unsafe."""
    if not isinstance(value, str):
        raise ValueError('文字必须是字符串')
    text = ' '.join(value.split())
    if not text and not allow_empty:
        raise ValueError('文字不能为空')
    if len(text) > limit:
        raise ValueError(f'文字最多 {limit} 个字')
    if _CONTROL.search(text) or _URL.search(text):
        raise ValueError('文字里不能有链接、尖括号或控制字符')
    lowered = text.lower().replace(' ', '')
    if any(word in lowered for word in _BLOCKED_WORDS):
        raise ValueError('文字里有不太友好的词，换个说法吧')
    return text


class MonopolyPlus(Monopoly):
    game_type = 'monopoly_plus'
    display_name = '大富翁·改'
    category = 'tabletop'
    min_players, max_players, recommended_players = 2, 6, 4
    allowed_player_counts = (2, 3, 4, 5, 6)
    supports_npcs = True
    uses_local_npc_strategy = True
    mcp_immediate_public_events = True
    # The compact delta below is already wire-ready; no per-viewer re-diff.
    mcp_event_key = None
    supports_stakes = False
    rules_text = '''【三十秒上手】
- 跟普通大富翁一样：掷骰、买地、收租、凑齐一组盖房，让别人破产。
- 多了几样好玩的：第三颗「速度骰」、抽到才能用的道具卡（强行交易、收购、「不行」）、30种事件卡（有的要开口讲故事、大家投票）、能借钱的银行、没轮到你也能押大小、给自己的地起名挂牌子。
- 坐牢很温柔：最多关一回合，牢里一次只关一个人。
- 看棋盘：格子里的小圆章＝这块地是谁的；格子外面带尖角的实心棋子＝人现在站在哪。

【目标】
2～6人，推荐4人。开局由先手选择大富翁版本（目前可玩「经典·改」，其他版本敬请期待）。每人局内现金1500，经过起点领200。经营地产让对手破产，最后存活者获胜。局内现金与平台筹码完全分离。

【沿用经典】
- 买地、拍卖、收租、成套翻倍、均衡建房、卖房、抵押赎回、交易、债务与破产清算都与「大富翁」相同，规则弹层里的经典细则仍然有效。
- 免费停车格改成了「拍卖行」，17、22 号格改成了「事件」格；其余格子不变。

【速度骰】
- 第一次经过起点之后，每次掷骰会多掷一颗速度骰：1/2/3 加到步数里；「巴士」领一张巴士票，照常按两颗白骰走；「大富翁先生」照常走完并结算后，再前进到下一块无主地产（可买或拍卖），没有无主地就前进到下一块要付租的地。
- 两颗白骰与速度骰点数相同（三同）时，可以任选棋盘上任意一格作为落点，本回合不再掷。
- 双骰判定只看两颗白骰；连续三次双骰照常入狱。在押时不掷速度骰。
- 巴士票：可在之后某个回合开始时代替掷骰，前进到本条边上任意一格（最远到下一个角）。票池16张，其中3张是「所有已发巴士票作废」；抽完就没有了。

【温柔监狱】
- 入狱后的第一回合：可交50或用出狱卡出狱再掷骰，也可试掷双骰。最晚第二回合必须出狱：掷骰时强制交50，然后按点数移动。
- 牢里同时只关一个人：新入狱的人会把原来那位挤到「探监」，直接出狱。在押照常收租。

【拍卖行】
- 停在拍卖行的人挑一块无主地产开拍（也可以放弃），所有存活玩家都能出价，流程同普通拍卖。

【道具卡（只能抽到，手牌最多3张，别人只看到张数）】
- 强行交易卡（全场2张）：自己回合用你的一块地强制换对手的一块地；成套的、盖了房的、抵押中的地都不能选。
- 收购卡（全场1张）：本回合踩到对手地产并付完租后，可按标价2倍强制买下；那块地必须无建筑、不在对方的成套组里。
- 「不行」卡（全场3张）：被强行交易、被收购、或抽到针对你的罚款事件时打出，抵消这一次。被攻击的一方总会收到确认窗口。
- 道具卡混在机会、公益牌堆里；手牌已满时新抽到的道具卡放回牌堆底。用过的道具卡回到原牌堆底。

【事件卡】
- 停在事件格抽一张事件，约30种：全桌的（某色组租金本轮翻倍/减半、所有人挪一格、随机两人换位置、济贫、修路封闭等）、地产的（涨价、促销、评估）、温馨的（红包、请客、拾金不昧）和聊天卡。「本轮」指到抽卡人下个回合开始前。
- 聊天卡需要抽卡人说一段话（写进卡里），桌上其他人一键投赞成/反对；超时按不通过。

【银行借贷】
- 额度＝名下未抵押地产标价总和的50%，封顶1000；同时只能有一笔。可在自己回合（含筹款时）借，随时可提前还（部分或全部）。
- 每过一次起点计息10%（向上取整），利息先从200工资里扣。借款后第3次经过起点时到期：先从现金扣，不够的由银行逐块拍卖你的地产抵债（你不能参与竞拍），拍卖多出的钱还给你；地产拍光仍不够，剩下的变成欠银行的债。
- 破产清算时银行欠款优先：建筑折现后先还贷款；还不清的话，你的地产全部交给银行拍卖。

【观战押注】
- 不是自己回合的玩家，可以在当前玩家本回合掷骰前押一次：大（白骰和≥8）、小（≤6）或7，押金固定50。押中大小拿回100，押中7拿回200；没押中押金归银行。当前玩家改坐巴士不掷骰时押金退回。

【地名牌】
- 自己回合可以给名下地产起名（最多8个字）并挂一句话（最多30个字）；别人踩到交租时会在事件里看到。地产换主人时牌子自动摘下。文字不能有链接和不友好的词。

【胜负】
- 最后一位未破产的玩家获胜；没有回合上限。

制作：顾屿、相顾｜小红书：苏苏脆脆'''
    move_format = ('从 private_state.legal_actions 复制动作（含 action_seq）提交，并携带房间 revision。'
                   '开局：{"action":"choose_edition","edition":"classic_plus"}。'
                   '参数动作：bid{amount}；propose_trade 同大富翁；take_loan{amount}；repay_loan{amount}；'
                   'name_tile{tile_id,name≤8字,motto≤30字}；use_item{item:"swap",tile_id:自己的地,target_tile_id:对手的地}；'
                   'use_item{item:"acquire"}；use_bus{tile_id}；choose_destination{tile_id}；pick_auction{tile_id}；'
                   'give_gift{to,amount 50~200}；perform{text≤150字}；vote{accept:布尔}；use_nope / decline_nope。'
                   '不是自己回合时可押注：{"action":"bet","choice":"big|small|seven","action_seq":当前值}（private_state.side_actions 列出可押项）。'
                   '所有动作都要带当前 action_seq。')

    # ------------------------------------------------------------------
    # setup

    def initialize(self, participants):
        state = super().initialize(participants)
        self._apply_edition(state, editions.DEFAULT_EDITION, shuffle=False)
        for p in state['players']:
            p.update(items=[], bus=0, loan=None, passed_go=False)
        state.update(version=1, edition=None, phase='setup', speed=None, rolled_this_turn=False,
                     bets={}, last_bets=[], choice=None, modifiers=[], log=[], acquire_window=None,
                     _queue=[], _choice_return=None, _seizure=None)
        return state

    def _apply_edition(self, state, key, *, shuffle=True):
        config = editions.edition(key)
        rules = editions.edition_rules(key)
        tiles = new_tiles()
        for tile_id, (name, kind) in config['tiles'].items():
            tiles[tile_id].update(name=name, kind=kind)
        for t in tiles:
            t.update(title='', motto='')
        decks = {name: list(range(len(cards))) for name, cards in config['decks'].items()}
        if shuffle:
            for deck in decks.values():
                self.rng.shuffle(deck)
        pool = ['expire'] * rules['bus_expire_tickets'] + ['ticket'] * (rules['bus_tickets'] - rules['bus_expire_tickets'])
        if shuffle:
            self.rng.shuffle(pool)
        state.update(tiles=tiles, rules=rules, _decks=decks, _bus_pool=pool, edition_name=config['name'])
        for p in state['players']:
            p['cash'] = rules['start_cash']

    @staticmethod
    def cards(state, deck):
        return editions.edition(state.get('edition') or editions.DEFAULT_EDITION)['decks'][deck]

    # ------------------------------------------------------------------
    # helpers

    def _log(self, state, kind, text, **extra):
        entry = dict(seq=state['action_seq'] + 1, kind=kind, text=text, **extra)
        state['log'].append(entry)
        del state['log'][:-LOG_LIMIT]
        return entry

    def price_of(self, state, tile):
        price = tile['price']
        for m in state.get('modifiers', []):
            if m['kind'] == 'tile_discount' and m['tile_id'] == tile['id']:
                price = price * m['pct'] // 100
        return price

    def closed(self, state, tile_id):
        return any(m['kind'] == 'road_closed' and m['tile_id'] == tile_id for m in state.get('modifiers', []))

    def rent(self, state, tile, dice=None):
        value = super().rent(state, tile, dice)
        if not value:
            return 0
        for m in state.get('modifiers', []):
            kind = m['kind']
            if kind == 'road_closed' and m['tile_id'] == tile['id']:
                return 0
            if kind == 'utility_off' and tile['kind'] == 'utility':
                return 0
            if ((kind == 'group_rent' and tile['group'] == m['group'])
                    or (kind == 'railroad_rent' and tile['kind'] == 'railroad')
                    or (kind == 'tile_rent' and m['tile_id'] == tile['id'])):
                value = value * m['pct'] // 100
        return value

    def full_set(self, state, tile):
        """Owner holds every tile of the group (stations 4, utilities 2)."""
        return bool(tile['owner']) and all(t['owner'] == tile['owner'] for t in self.group(state, tile))

    def _attackable(self, state, tile):
        return (bool(tile['price']) and tile['owner'] is not None and not tile['mortgaged']
                and not self.full_set(state, tile) and not any(g['level'] for g in self.group(state, tile)))

    def _has_item(self, p, kind):
        return any(i['kind'] == kind for i in p['items'])

    def _take_item(self, state, p, kind):
        item = next(i for i in p['items'] if i['kind'] == kind)
        p['items'].remove(item)
        state['_decks'][item['deck']].append(item['card'])

    def loan_limit(self, state, pid):
        rules = state['rules']
        value = sum(t['price'] for t in state['tiles'] if t['owner'] == pid and not t['mortgaged'])
        return min(rules['loan_cap'], value * rules['loan_ratio_pct'] // 100)

    def _next_active(self, state, pid):
        ids = [p['player_id'] for p in state['players']]
        start = ids.index(pid)
        return next((ids[(start + i) % len(ids)] for i in range(1, len(ids))
                     if ids[(start + i) % len(ids)] in self.active(state)), None)

    def _own_window(self, state, pid):
        return (pid == state['turn_player_id'] == state['current_player_id']
                and state['phase'] in ('roll', 'purchase', 'manage'))

    # ------------------------------------------------------------------
    # legal actions

    def side_actions(self, state, pid):
        """Out-of-turn bets: before the current player's first roll this turn."""
        rules = state.get('rules') or {}
        p = next((p for p in state['players'] if p['player_id'] == pid), None)
        if (not rules.get('bets') or p is None or p['bankrupt'] or state['phase'] != 'roll'
                or state.get('rolled_this_turn') or pid == state['current_player_id']
                or pid in state.get('bets', {}) or p['cash'] < rules['bet_stake']
                or state['turn_player_id'] != state['current_player_id']):
            return []
        return [dict(action='bet', choice=c, action_seq=state['action_seq']) for c in ('big', 'small', 'seven')]

    def accepts_out_of_turn_action(self, state, move, player_id):
        return (isinstance(move, dict) and move.get('action') == 'bet'
                and player_id != state.get('turn_player_id'))

    def _choice_actions(self, state, pid):
        choice = state['choice']
        kind = choice['kind']
        if kind == 'triple':
            here = self.player(state, pid)['position']
            return [dict(action='choose_destination', tile_id=i) for i in range(40) if i != here]
        if kind == 'pick_auction':
            return [dict(action='pick_auction', tile_id=i) for i in choice['options']] + [dict(action='skip_choice')]
        if kind == 'gift':
            cash = self.player(state, pid)['cash']
            others = [o for o in self.active(state) if o != pid]
            out = [dict(action='give_gift', to=o, amount=a) for o in others for a in (50, 100, 200) if a <= cash]
            return out or [dict(action='skip_choice')]
        if kind == 'chat':
            if choice['stage'] == 'perform':
                return [dict(action='skip_choice')]
            return [dict(action='vote', accept=True), dict(action='vote', accept=False)]
        if kind in ('nope_fine', 'nope_attack'):
            out = [dict(action='decline_nope')]
            if self._has_item(self.player(state, pid), 'nope'):
                out.insert(0, dict(action='use_nope'))
            return out
        return []

    def legal_actions(self, state, pid):
        if state['phase'] == 'finished' or pid != state['turn_player_id'] or pid not in self.active(state):
            return []
        if state['phase'] == 'setup':
            return [dict(action='choose_edition', edition=e['id'], action_seq=state['action_seq'])
                    for e in editions.edition_menu() if e['available']]
        if state['phase'] == 'choice':
            return [dict(a, action_seq=state['action_seq']) for a in self._choice_actions(state, pid)]
        p = self.player(state, pid)
        rules = state['rules']
        actions = super().legal_actions(state, pid)
        actions = [a for a in actions if a['action'] != 'buy']
        if state['phase'] == 'purchase' and not self._trade_gate(state, pid):
            t = state['tiles'][p['position']]
            if p['cash'] >= self.price_of(state, t):
                actions.insert(0, dict(action='buy', action_seq=state['action_seq']))
        if self._trade_gate(state, pid) or state['phase'] == 'auction':
            return actions
        extra = []
        if p['jailed'] and state['phase'] == 'roll':
            # Gentle jail: the bail option uses the configured amount.
            actions = [a for a in actions if a['action'] != 'pay_bail']
            if p['cash'] >= rules['bail']:
                extra.append(dict(action='pay_bail'))
        if state['phase'] == 'roll' and p['bus'] and not p['jailed'] and not state['rolled_this_turn']:
            extra += [dict(action='use_bus', tile_id=i) for i in self.bus_targets(p['position'])]
        if rules['loans'] and state['phase'] in ('roll', 'purchase', 'manage', 'debt'):
            limit = self.loan_limit(state, pid)
            if p['loan'] is None and limit >= rules['loan_min']:
                for amount in sorted({min(limit, 100), min(limit, 200), min(limit, 500), limit}):
                    if amount >= rules['loan_min']:
                        extra.append(dict(action='take_loan', amount=amount))
            if p['loan'] and state['phase'] != 'debt' and p['cash'] >= p['loan']['balance']:
                extra.append(dict(action='repay_loan', amount=p['loan']['balance']))
        if self._own_window(state, pid) and rules['items'] and state['phase'] in ('roll', 'manage'):
            if self._acquire_ok(state, pid):
                extra.append(dict(action='use_item', item='acquire'))
        return actions + [dict(a, action_seq=state['action_seq']) for a in extra]

    @staticmethod
    def bus_targets(position):
        end = (position // 10 + 1) * 10
        return [i % 40 for i in range(position + 1, end + 1)]

    def _acquire_ok(self, state, pid):
        window = state.get('acquire_window')
        p = self.player(state, pid)
        if not window or not self._has_item(p, 'acquire') or p['position'] != window['tile_id']:
            return False
        tile = state['tiles'][window['tile_id']]
        return (tile['owner'] == window['owner'] and tile['owner'] in self.active(state) and tile['owner'] != pid
                and self._attackable(state, tile) and p['cash'] >= tile['price'] * 2)

    def item_options(self, state, pid):
        p = next((p for p in state['players'] if p['player_id'] == pid), None)
        if not p or not state.get('rules', {}).get('items') or self._trade_gate(state, pid):
            return {}
        out = {}
        if self._has_item(p, 'swap') and self._own_window(state, pid) and state['phase'] in ('roll', 'manage'):
            mine = [t['id'] for t in state['tiles'] if t['owner'] == pid and self._attackable(state, t)]
            theirs = [t['id'] for t in state['tiles'] if t['owner'] not in (None, pid)
                      and t['owner'] in self.active(state) and self._attackable(state, t)]
            if mine and theirs:
                out['swap'] = dict(mine=mine, theirs=theirs)
        if self._acquire_ok(state, pid):
            t = state['tiles'][state['acquire_window']['tile_id']]
            out['acquire'] = dict(tile_id=t['id'], price=t['price'] * 2)
        return out

    # ------------------------------------------------------------------
    # validation

    def validate_action(self, state, move, actor):
        pid = actor['player_id']
        if not isinstance(move, dict) or not self._integer(move.get('action_seq')) or move['action_seq'] != state['action_seq']:
            raise ValueError('action_seq 已变化或缺失，请刷新局面后重试')
        action = move.get('action')
        if action == 'bet':
            if not any(move == a for a in self.side_actions(state, pid)):
                raise ValueError('现在不能押注（只有非当前玩家、在本回合掷骰前、每人一次、现金够50才可以）')
            return
        if pid != state['turn_player_id'] or pid not in self.active(state) or state['phase'] == 'finished':
            raise ValueError('当前没有行动权')
        rules = state.get('rules') or {}
        p = self.player(state, pid)
        if state['phase'] in ('setup', 'choice') and action not in ('perform', 'give_gift'):
            if not any(move == a and all(type(move[k]) is type(a[k]) for k in a) for a in self.legal_actions(state, pid)):
                raise ValueError('该动作不在当前合法行动中')
            return
        if action == 'perform':
            choice = state.get('choice') or {}
            if state['phase'] != 'choice' or choice.get('kind') != 'chat' or choice.get('stage') != 'perform':
                raise ValueError('现在不是表演聊天卡的时候')
            if set(move) != {'action', 'action_seq', 'text'}:
                raise ValueError('perform 只需要 text')
            clean_text(move['text'], rules['chat_text_max'])
            return
        if action == 'give_gift':
            choice = state.get('choice') or {}
            if state['phase'] != 'choice' or choice.get('kind') != 'gift':
                raise ValueError('现在不是发红包的时候')
            if (set(move) != {'action', 'action_seq', 'to', 'amount'} or not self._integer(move['amount'])
                    or not 50 <= move['amount'] <= min(200, p['cash'])):
                raise ValueError('红包金额必须是 50~200 之间、且不超过现金的整数')
            if move['to'] == pid or move['to'] not in self.active(state):
                raise ValueError('红包要发给另一位存活玩家')
            return
        if action == 'take_loan':
            if (not rules.get('loans') or set(move) != {'action', 'action_seq', 'amount'}
                    or state['phase'] not in ('roll', 'purchase', 'manage', 'debt') or self._trade_gate(state, pid)):
                raise ValueError('现在不能借款')
            if p['loan'] is not None:
                raise ValueError('同时只能有一笔银行贷款')
            if not self._integer(move['amount']) or not rules['loan_min'] <= move['amount'] <= self.loan_limit(state, pid):
                raise ValueError(f"借款额度为 {rules['loan_min']}~{self.loan_limit(state, pid)}")
            return
        if action == 'repay_loan':
            if (set(move) != {'action', 'action_seq', 'amount'} or p['loan'] is None
                    or state['phase'] not in ('roll', 'purchase', 'manage') or self._trade_gate(state, pid)):
                raise ValueError('现在不能还款')
            if not self._integer(move['amount']) or not 1 <= move['amount'] <= min(p['cash'], p['loan']['balance']):
                raise ValueError('还款金额必须为正整数，且不超过现金与欠款')
            return
        if action == 'name_tile':
            if (not rules.get('naming') or set(move) != {'action', 'action_seq', 'tile_id', 'name', 'motto'}
                    or not self._own_window(state, pid) or self._trade_gate(state, pid)):
                raise ValueError('只能在自己回合给自己的地产起名')
            if not self._integer(move['tile_id'], 39) or state['tiles'][move['tile_id']]['owner'] != pid:
                raise ValueError('只能给自己名下的地产起名')
            clean_text(move['name'], rules['name_max'])
            clean_text(move['motto'], rules['motto_max'], allow_empty=True)
            return
        if action == 'use_item' and move.get('item') == 'swap':
            if set(move) != {'action', 'action_seq', 'item', 'tile_id', 'target_tile_id'}:
                raise ValueError('强行交易需要 tile_id 和 target_tile_id')
            options = self.item_options(state, pid).get('swap')
            if (not options or self._trade_gate(state, pid) or move['tile_id'] not in options['mine']
                    or move['target_tile_id'] not in options['theirs']
                    or not self._integer(move['tile_id'], 39) or not self._integer(move['target_tile_id'], 39)):
                raise ValueError('强行交易：只能用你一块可选的地换对手一块可选的地（不能成套、不能有房、不能抵押）')
            return
        if action == 'bid' and state['phase'] == 'auction':
            if (set(move) != {'action', 'action_seq', 'amount'} or not self._integer(move.get('amount'))
                    or not state['auction']['bid'] < move['amount'] <= p['cash']):
                raise ValueError('出价必须高于现价且不能超过现金')
            return
        if action == 'propose_trade':
            return super().validate_action(state, move, actor)
        if not any(move == a and all(type(move[k]) is type(a[k]) for k in a) for a in self.legal_actions(state, pid)):
            raise ValueError('该动作不在当前合法行动中')
        if action == 'respond_trade' and move['accept']:
            self._validate_trade(state, self._trade_for(state, pid))

    # ------------------------------------------------------------------
    # movement and landing

    def _next_turn(self, state):
        old_turn = state['turn_number']
        super()._next_turn(state)
        if state['turn_number'] != old_turn:
            state.update(speed=None, rolled_this_turn=False, bets={}, acquire_window=None, _queue=[])
            expired = [m for m in state['modifiers'] if m['until_turn'] <= state['turn_number']]
            if expired:
                state['modifiers'] = [m for m in state['modifiers'] if m['until_turn'] > state['turn_number']]
                self._log(state, 'expire', '本轮效果结束：' + '；'.join(m['text'] for m in expired))

    def _jail(self, state, p):
        rules = state['rules']
        if rules.get('single_cell_jail'):
            for other in state['players']:
                if other is not p and other['jailed']:
                    other.update(jailed=False, jail_turns=0)
                    self.note(state, f"{other['name']}被挤出牢房，改为探监。")
                    self._log(state, 'jail_out', f"{other['name']}被新来的人挤出牢房，改为探监（自动出狱）",
                              player=other['player_id'], tiles=[10])
        from_pos = p['position']
        super()._jail(state, p)
        state['_queue'] = [q for q in state['_queue'] if q.get('player_id') != p['player_id']]
        self._log(state, 'jail', f"{p['name']}入狱", player=p['player_id'], tiles=[10],
                  move=dict(frm=from_pos, to=10, steps=0))

    def _pass_go(self, state, p):
        rules = state['rules']
        salary = rules['salary']
        text = f"{p['name']}经过起点，领取{salary}"
        loan = p['loan']
        if loan:
            interest = math.ceil(loan['balance'] * rules['loan_interest_pct'] / 100)
            paid = min(interest, salary)
            salary -= paid
            loan['balance'] += interest - paid
            loan['laps_left'] -= 1
            text += f"（扣贷款利息{paid}，实得{salary}）"
        p['cash'] += salary
        p['passed_go'] = True
        self.note(state, text + '。')
        self._log(state, 'salary', text, player=p['player_id'], tiles=[0])
        seizing = state.get('_seizure') and state['_seizure']['player_id'] == p['player_id']
        if loan and loan['laps_left'] <= 0 and not seizing:
            self._loan_due(state, p)

    def _move_to(self, state, p, dest, salary=True, special=None, walk=None, entry=None):
        start = p['position']
        steps = walk if walk is not None else 0
        move = dict(frm=start, to=dest, steps=steps)
        if entry is not None:
            # The roll/bus/Mr. Monopoly line already names the destination.
            entry.update(move=move, tiles=[dest])
        else:
            self._log(state, 'move', f"{p['name']} → {state['tiles'][dest]['name']}", player=p['player_id'],
                      tiles=[dest], move=move)
        if salary and dest < start:
            self._pass_go(state, p)
        p['position'] = dest
        self._land(state, p, special)

    def _land(self, state, p, special=None):
        state['phase'] = 'manage'
        t = state['tiles'][p['position']]
        pid = p['player_id']
        self.note(state, f"停在{t['name']}。")
        if t['price']:
            if self.closed(state, t['id']):
                self.note(state, '此地修路封闭中，不收租。')
            elif t['owner'] is None:
                state['phase'] = 'purchase'
            elif t['owner'] != pid and not t['mortgaged']:
                rent = self.rent(state, t)
                if special == 'railroad':
                    rent *= 2
                elif special == 'utility':
                    dice = [self.rng.randint(1, 6), self.rng.randint(1, 6)]
                    state['event_dice'] = dice
                    rent = sum(dice) * 10
                    if any(m['kind'] == 'utility_off' for m in state['modifiers']):
                        rent = 0
                    self.note(state, f'设施事件骰点{dice[0]}+{dice[1]}。')
                if rent:
                    label = f"「{t['title']}」" if t.get('title') else ''
                    self._charge(state, pid, rent, t['owner'], t['name'] + '租金')
                    owner = self.player(state, t['owner'])
                    text = f"{p['name']}向{owner['name']}交{t['name']}{label}租金 {rent}"
                    entry = self._log(state, 'rent', text, player=pid, tiles=[t['id']], amount=rent, owner=t['owner'])
                    if t.get('motto'):
                        entry['motto'] = t['motto']
                    if state['rules']['items']:
                        state['acquire_window'] = dict(tile_id=t['id'], owner=t['owner'])
        elif t['kind'] == 'tax':
            self._charge(state, pid, 200 if t['id'] == 4 else 100, reason=t['name'])
            self._log(state, 'tax', f"{p['name']}缴纳{t['name']} {200 if t['id'] == 4 else 100}", player=pid, tiles=[t['id']])
        elif t['kind'] == 'go_to_jail':
            self._jail(state, p)
        elif t['kind'] in ('chance', 'chest', 'event'):
            self._event(state, p, t['kind'])
        elif t['kind'] == 'auction_house' and state['rules']['parking_auction']:
            options = [x['id'] for x in state['tiles'] if x['price'] and x['owner'] is None]
            if options:
                state['_queue'].append(dict(kind='pick_auction', player_id=pid))
            else:
                self.note(state, '已经没有无主地产可以拍卖。')

    def _detour(self, state, dest):
        # A closed road pushes a dice landing to the following tile.
        guard = 0
        while self.closed(state, dest) and guard < 40:
            dest = (dest + 1) % 40
            guard += 1
        return dest

    def _settle_bets(self, state, total):
        bets = state.get('bets') or {}
        results = []
        payout = state['rules']['bet_payout']
        hit = 'big' if total >= 8 else 'small' if total <= 6 else 'seven'
        for pid, choice in bets.items():
            if pid not in self.active(state):
                continue
            won = choice == hit
            if won:
                self.player(state, pid)['cash'] += payout[choice]
            results.append(dict(player_id=pid, choice=choice, won=won, payout=payout[choice] if won else 0))
        state['last_bets'] = results
        state['bets'] = {}
        if results:
            label = {'big': '大', 'small': '小', 'seven': '7'}
            text = f"押注开奖：白骰和 {total}（{label[hit]}）。" + '；'.join(
                f"{self.player(state, r['player_id'])['name']}押{label[r['choice']]}{'中，拿回' + str(r['payout']) if r['won'] else '未中'}"
                for r in results)
            self._log(state, 'bet', text)
            self.note(state, text)

    def _refund_bets(self, state):
        stake = state['rules']['bet_stake']
        for pid in state.get('bets', {}):
            if pid in self.active(state):
                self.player(state, pid)['cash'] += stake
        if state.get('bets'):
            self._log(state, 'bet', '当前玩家坐巴士没有掷骰，押金全部退回')
        state['bets'] = {}

    def _roll(self, state, p):
        rules = state['rules']
        dice = [self.rng.randint(1, 6), self.rng.randint(1, 6)]
        state['dice'] = dice
        state.pop('event_dice', None)
        speed = None
        if rules['speed_die'] and p['passed_go'] and not p['jailed']:
            speed = SPEED_FACES[self.rng.randint(0, 5)]
        state['speed'] = speed
        if not state['rolled_this_turn']:
            state['rolled_this_turn'] = True
            self._settle_bets(state, sum(dice))
        double = dice[0] == dice[1]
        roll_text = f"{p['name']} 掷出 {dice[0]}+{dice[1]}" + (f"，速度骰「{SPEED_LABELS[speed]}」" if speed else '')
        self.note(state, roll_text + '。')
        state['extra_roll'] = False
        pid = p['player_id']
        if p['jailed']:
            p['jail_turns'] += 1
            if double:
                p.update(jailed=False, jail_turns=0)
                roll_text += '，双骰出狱'
            elif p['jail_turns'] < rules['jail_max_turns']:
                state['phase'] = 'manage'
                self.note(state, '未掷出双骰，继续在押。')
                self._log(state, 'roll', roll_text + '，未出狱', player=pid, dice=dice, tiles=[10])
                return
            else:
                state['_resume'] = {'kind': 'jail_move', 'player_id': pid, 'steps': sum(dice)}
                self._charge(state, pid, rules['bail'], reason='强制出狱罚款')
                state['phase'] = 'manage'
                self._log(state, 'roll', roll_text + f"，在押第二回合：强制交{rules['bail']}出狱", player=pid, dice=dice)
                return
            steps = sum(dice)
        else:
            state['doubles'] = state['doubles'] + 1 if double else 0
            if state['doubles'] == 3:
                self._log(state, 'roll', roll_text + '，连续三次双骰', player=pid, dice=dice)
                self._jail(state, p)
                return
            if speed in (1, 2, 3) and dice[0] == dice[1] == speed:
                state.update(extra_roll=False, doubles=0)
                self._log(state, 'roll', roll_text + '，三同！可以任选落点', player=pid, dice=dice, speed=speed)
                state['_queue'].append(dict(kind='triple', player_id=pid))
                state['phase'] = 'manage'
                return
            state['extra_roll'] = double
            steps = sum(dice) + (speed if isinstance(speed, int) else 0)
            if speed == 'bus':
                self._draw_bus(state, p)
        dest = self._detour(state, (p['position'] + steps) % 40)
        target = state['tiles'][dest]['name']
        entry = self._log(state, 'roll', f"{roll_text} → 走到 {target}", player=pid, dice=dice, speed=speed)
        self._move_to(state, p, dest, walk=(dest - p['position']) % 40, entry=entry)
        if speed == 'mr' and not p['jailed']:
            state['_queue'].append(dict(kind='mr', player_id=pid))

    def _draw_bus(self, state, p):
        pool = state['_bus_pool']
        if not pool:
            self._log(state, 'bus', '巴士票已经发完了', player=p['player_id'])
            return
        ticket = pool.pop(0)
        if ticket == 'expire':
            held = sum(x['bus'] for x in state['players'])
            for x in state['players']:
                x['bus'] = 0
            self._log(state, 'bus', f"{p['name']}抽到「所有已发巴士票作废」，作废 {held} 张", player=p['player_id'])
            self.note(state, '所有已发巴士票作废。')
        else:
            p['bus'] += 1
            self._log(state, 'bus', f"{p['name']}领到一张巴士票", player=p['player_id'])
            self.note(state, '领到一张巴士票。')

    def _mr_move(self, state, p):
        pos = p['position']
        ring = [(pos + n) % 40 for n in range(1, 40)]
        dest = next((i for i in ring if state['tiles'][i]['price'] and state['tiles'][i]['owner'] is None), None)
        if dest is None:
            dest = next((i for i in ring if state['tiles'][i]['owner'] not in (None, p['player_id'])
                         and not state['tiles'][i]['mortgaged']), None)
        if dest is None:
            self._log(state, 'mr', '大富翁先生：已经没有可去的地产', player=p['player_id'])
            return
        entry = self._log(state, 'mr', f"大富翁先生带{p['name']}前往 {state['tiles'][dest]['name']}", player=p['player_id'], tiles=[dest])
        self._move_to(state, p, dest, walk=(dest - pos) % 40, entry=entry)

    # ------------------------------------------------------------------
    # cards and events

    def _event(self, state, p, deck_name):
        deck = state['_decks'][deck_name]
        if not deck:
            self.note(state, '牌堆暂时空了。')
            return
        index = deck.pop(0)
        kind, text, value = self.cards(state, deck_name)[index]
        seq = state['action_seq'] + 1
        if not state.get('last_card_events') or state['last_card_events'][-1]['action_seq'] != seq:
            state['last_card_events'] = []
        events = state['last_card_events']
        title = text if kind != 'chat' else f'聊天卡·{text}'
        event = dict(event_id=f'{seq}:{len(events) + 1}', action_seq=seq, player_id=p['player_id'],
                     deck=deck_name, text=title if kind != 'item' else '道具卡（内容保密）', summary='')
        events.append(event)
        state['last_event'] = event['text']
        self.note(state, event['text'] + '。')
        pid = p['player_id']
        deck_label = {'chance': '机会', 'chest': '公益', 'event': '事件'}[deck_name]
        if kind == 'item':
            if len(p['items']) >= state['rules']['hand_limit']:
                deck.append(index)
                event['summary'] = '手牌已满，道具卡放回牌堆底'
            else:
                p['items'].append(dict(kind=value, deck=deck_name, card=index))
                event['summary'] = '收入手牌'
            self._log(state, 'card', f"{p['name']}抽到{deck_label}：道具卡（{event['summary']}）", player=pid, deck=deck_name)
            return
        if kind == 'jail_card':
            p['jail_cards'].append(deck_name)
            event['summary'] = '出狱卡已收入手中'
            self._log(state, 'card', f"{p['name']}抽到{deck_label}：{text}", player=pid, deck=deck_name)
            return
        deck.append(index)
        self._log(state, 'card', f"{p['name']}抽到{deck_label}：{title}", player=pid, deck=deck_name)
        if kind in FINE_KINDS:
            amount = self._fine_amount(state, p, kind, value)
            event['summary'] = f'应付 {amount}'
            if amount and self._has_item(p, 'nope'):
                state['_queue'].insert(0, dict(kind='nope_fine', player_id=pid, amount=amount, reason=title))
                event['summary'] += '（可打出「不行」）'
            else:
                self._charge(state, pid, amount, reason=title)
            return
        handler = getattr(self, f'_ev_{kind}', None)
        if handler is not None:
            event['summary'] = handler(state, p, value, title) or ''
            return
        self._classic_event(state, p, kind, value, text, event)

    def _fine_amount(self, state, p, kind, value):
        pid = p['player_id']
        if kind == 'pay':
            return value
        if kind in ('repairs', 'leak'):
            return sum((value[1] if t['level'] == 5 else value[0] * t['level']) for t in state['tiles'] if t['owner'] == pid)
        if kind == 'agency_fee':
            prices = [t['price'] for t in state['tiles'] if t['owner'] == pid]
            return max(prices) * value // 100 if prices else 0
        return 0

    def _classic_event(self, state, p, kind, value, text, event):
        pid = p['player_id']
        if kind == 'gain':
            p['cash'] += value
            event['summary'] = f'现金 +{value}'
        elif kind in ('pay_each', 'collect_each'):
            event['summary'] = f'向每位其他玩家支付 {value}' if kind == 'pay_each' else f'每位其他玩家付你 {value}'
            for other in self.active(state):
                if other != pid:
                    self._charge(state, pid if kind == 'pay_each' else other, value,
                                 other if kind == 'pay_each' else pid, text)
        elif kind == 'jail':
            self._jail(state, p)
            event['summary'] = '进入监狱，不领取起点奖励'
        elif kind == 'advance':
            event['summary'] = f"前往{state['tiles'][value]['name']}"
            self._move_to(state, p, value)
        elif kind == 'back':
            dest = (p['position'] - value) % 40
            event['summary'] = f"退后{value}格，抵达{state['tiles'][dest]['name']}"
            self._move_to(state, p, dest, salary=False, walk=-value)
        elif kind in ('utility', 'railroad'):
            dest = next((p['position'] + n) % 40 for n in range(1, 41)
                        if state['tiles'][(p['position'] + n) % 40]['kind'] == kind)
            event['summary'] = f"前往{state['tiles'][dest]['name']}"
            self._move_to(state, p, dest, special=kind)

    def _until(self, state):
        return state['turn_number'] + len(self.active(state))

    def _modifier(self, state, kind, text, **extra):
        state['modifiers'].append(dict(kind=kind, text=text, until_turn=self._until(state), **extra))
        self._log(state, 'modifier', text, tiles=extra.get('tiles', []))

    def _ev_group_rent(self, state, p, pct, title):
        group = PROPERTY_GROUPS[self.rng.randint(0, len(PROPERTY_GROUPS) - 1)]
        verb = '翻倍' if pct > 100 else '减半'
        ids = [t['id'] for t in state['tiles'] if t['group'] == group]
        self._modifier(state, 'group_rent', f'{GROUP_NAMES[group]}本轮租金{verb}', group=group, pct=pct, tiles=ids)
        return f'{GROUP_NAMES[group]}租金{verb}'

    def _ev_railroad_rent(self, state, p, pct, title):
        self._modifier(state, 'railroad_rent', '车站本轮租金翻倍', pct=pct,
                       tiles=[t['id'] for t in state['tiles'] if t['kind'] == 'railroad'])
        return '车站租金翻倍'

    def _ev_utility_off(self, state, p, value, title):
        self._modifier(state, 'utility_off', '设施本轮不收租', tiles=[t['id'] for t in state['tiles'] if t['kind'] == 'utility'])
        return '设施本轮不收租'

    def _ev_shift_all(self, state, p, value, title):
        for x in state['players']:
            if x['bankrupt'] or x['jailed']:
                continue
            start = x['position']
            x['position'] = (start + 1) % 40
            self._log(state, 'move', f"{x['name']}挪到{state['tiles'][x['position']]['name']}", player=x['player_id'],
                      tiles=[x['position']], move=dict(frm=start, to=x['position'], steps=1))
        state['acquire_window'] = None
        return '所有人前进一格'

    def _ev_swap_two(self, state, p, value, title):
        pool = [x for x in state['players'] if not x['bankrupt'] and not x['jailed']]
        if len(pool) < 2:
            return '人数不足，无事发生'
        a = pool[self.rng.randint(0, len(pool) - 1)]
        b = [x for x in pool if x is not a][self.rng.randint(0, len(pool) - 2)]
        pa, pb = a['position'], b['position']
        a['position'], b['position'] = pb, pa
        for x, frm, to in ((a, pa, pb), (b, pb, pa)):
            self._log(state, 'move', f"{x['name']}换到{state['tiles'][to]['name']}", player=x['player_id'],
                      tiles=[to], move=dict(frm=frm, to=to, steps=0))
        state['acquire_window'] = None
        return f"{a['name']} ⇄ {b['name']}"

    def _rich_poor(self, state):
        alive = [self.player(state, pid) for pid in self.active(state)]
        rich = max(alive, key=lambda x: x['cash'])
        poor = min(alive, key=lambda x: x['cash'])
        return rich, poor

    def _ev_welfare(self, state, p, value, title):
        rich, poor = self._rich_poor(state)
        if rich is poor or rich['cash'] == poor['cash']:
            return '大家一样多，无事发生'
        self._charge(state, rich['player_id'], value, poor['player_id'], title)
        return f"{rich['name']} → {poor['name']} {value}"

    def _ev_festival(self, state, p, value, title):
        for pid in self.active(state):
            self.player(state, pid)['cash'] += value
        return f'每人 +{value}'

    def _ev_property_tax(self, state, p, value, title):
        for pid in self.active(state):
            count = sum(t['owner'] == pid for t in state['tiles'])
            self._charge(state, pid, count * value, reason=title)
        return f'每块地产 {value}'

    def _ev_checkup(self, state, p, value, title):
        for pid in self.active(state):
            self._charge(state, pid, value, reason=title)
        return f'每人 -{value}'

    def _ev_pass_left(self, state, p, value, title):
        for pid in self.active(state):
            nxt = self._next_active(state, pid)
            if nxt:
                self._charge(state, pid, value, nxt, title)
        return f'每人给下家 {value}'

    def _ev_flash_auction(self, state, p, value, title):
        options = [t['id'] for t in state['tiles'] if t['price'] and t['owner'] is None]
        if not options:
            return '没有无主地产'
        tile_id = options[self.rng.randint(0, len(options) - 1)]
        state['_auction_queue'].append(tile_id)
        self._log(state, 'auction', f"快闪拍卖：{state['tiles'][tile_id]['name']}", tiles=[tile_id])
        return f"拍卖{state['tiles'][tile_id]['name']}"

    def _ev_jail_break(self, state, p, value, title):
        freed = [x['name'] for x in state['players'] if x['jailed']]
        for x in state['players']:
            x.update(jailed=False, jail_turns=0)
        return '、'.join(freed) + '出狱' if freed else '牢里没人'

    def _ev_cake(self, state, p, value, title):
        _, poor = self._rich_poor(state)
        for pid in self.active(state):
            if pid != poor['player_id']:
                self._charge(state, pid, value, poor['player_id'], title)
        return f"大家给{poor['name']}各 {value}"

    def _ev_tile_rent_up(self, state, p, pct, title):
        owned = [t for t in state['tiles'] if t['owner'] is not None]
        if not owned:
            return '还没有人买地，无事发生'
        t = owned[self.rng.randint(0, len(owned) - 1)]
        self._modifier(state, 'tile_rent', f"{t['name']}本轮租金 ×1.5", tile_id=t['id'], pct=pct, tiles=[t['id']])
        return f"{t['name']}租金 ×1.5"

    def _ev_tile_discount(self, state, p, pct, title):
        free = [t for t in state['tiles'] if t['price'] and t['owner'] is None]
        if not free:
            return '没有无主地产'
        t = free[self.rng.randint(0, len(free) - 1)]
        self._modifier(state, 'tile_discount', f"{t['name']}本轮七折出售", tile_id=t['id'], pct=pct, tiles=[t['id']])
        return f"{t['name']}七折"

    def _ev_road_closed(self, state, p, value, title):
        streets = [t for t in state['tiles'] if t['kind'] == 'property' and t['id'] != p['position']
                   and not self.closed(state, t['id'])]
        t = streets[self.rng.randint(0, len(streets) - 1)]
        self._modifier(state, 'road_closed', f"{t['name']}修路封闭", tile_id=t['id'], tiles=[t['id']])
        return f"{t['name']}封闭"

    def _ev_appraisal(self, state, p, value, title):
        count = sum(t['owner'] == p['player_id'] and not t['mortgaged'] for t in state['tiles'])
        p['cash'] += count * value
        return f'现金 +{count * value}'

    def _ev_next_railroad(self, state, p, value, title):
        pos = p['position']
        dest = next((pos + n) % 40 for n in range(1, 41) if state['tiles'][(pos + n) % 40]['kind'] == 'railroad')
        self._move_to(state, p, dest, walk=(dest - pos) % 40)
        return f"前往{state['tiles'][dest]['name']}"

    def _ev_back(self, state, p, value, title):
        dest = (p['position'] - value) % 40
        self._move_to(state, p, dest, salary=False, walk=-value)
        return f"退到{state['tiles'][dest]['name']}"

    def _ev_forward(self, state, p, value, title):
        dest = (p['position'] + value) % 40
        self._move_to(state, p, dest, walk=value)
        return f"前进到{state['tiles'][dest]['name']}"

    def _ev_gift(self, state, p, value, title):
        state['_queue'].append(dict(kind='gift', player_id=p['player_id']))
        return '选一个人发红包'

    def _ev_milk_tea(self, state, p, value, title):
        for pid in self.active(state):
            if pid != p['player_id']:
                self._charge(state, p['player_id'], value, pid, title)
        return f'付每位其他玩家 {value}'

    def _ev_wallet(self, state, p, value, title):
        p['cash'] += value
        return f'现金 +{value}'

    def _ev_chat(self, state, p, spec, title):
        nxt = self._next_active(state, p['player_id'])
        prompt = spec['prompt'].format(next=self.player(state, nxt)['name'] if nxt else '下家')
        state['_queue'].append(dict(kind='chat', player_id=p['player_id'], card=title.replace('聊天卡·', ''),
                                    prompt=prompt, mode=spec['mode'], amount=spec['amount'], reward=spec['reward']))
        return prompt

    # ------------------------------------------------------------------
    # loans

    def _loan_due(self, state, p):
        loan = p['loan']
        pay = min(p['cash'], loan['balance'])
        p['cash'] -= pay
        loan['balance'] -= pay
        text = f"{p['name']}的银行贷款到期，从现金扣还 {pay}"
        if loan['balance'] <= 0:
            p['loan'] = None
            self._log(state, 'loan', text + '，已还清', player=p['player_id'])
            self._cancel_seizure(state, p)
            return
        self._log(state, 'loan', text + f"，还差 {loan['balance']}，银行开始拍卖其地产抵债", player=p['player_id'])
        state['_seizure'] = dict(player_id=p['player_id'], tile_id=None)
        self._next_seizure(state, p)

    def _next_seizure(self, state, p):
        loan = p['loan']
        owned = [t for t in state['tiles'] if t['owner'] == p['player_id']]
        if not owned:
            remaining = loan['balance']
            p['loan'] = None
            state['_seizure'] = None
            self._charge(state, p['player_id'], remaining, reason='贷款到期未还清')
            self._log(state, 'loan', f"{p['name']}地产已拍完，剩余贷款 {remaining} 转为欠银行的债", player=p['player_id'])
            return
        clean = [t for t in owned if not any(g['level'] for g in self.group(state, t))]
        if not clean:
            cheapest = min(owned, key=lambda t: t['price'])
            proceeds = 0
            for g in self.group(state, cheapest):
                proceeds += g['level'] * g['build_cost'] // 2
                g['level'] = 0
            self._loan_credit(state, p, proceeds, f"拆售{GROUP_NAMES.get(cheapest['group'], '该组')}建筑")
            if p['loan'] is None:
                return
            clean = [t for t in owned if not any(g['level'] for g in self.group(state, t))]
        tile = min(clean, key=lambda t: (t['price'], t['id']))
        tile.update(owner=None, mortgaged=False, title='', motto='')
        state['_seizure']['tile_id'] = tile['id']
        state['_auction_queue'].append(tile['id'])

    def _cancel_seizure(self, state, p):
        """The loan was cleared mid-seizure: return a not-yet-auctioned lot."""
        seizure = state.get('_seizure')
        if not seizure or seizure['player_id'] != p['player_id']:
            return
        tile_id = seizure.get('tile_id')
        if tile_id is not None and tile_id in state['_auction_queue'] and (state['auction'] or {}).get('tile_id') != tile_id:
            state['_auction_queue'].remove(tile_id)
            state['tiles'][tile_id]['owner'] = p['player_id']
            self._log(state, 'loan', f"贷款已还清，{state['tiles'][tile_id]['name']}退还给{p['name']}", tiles=[tile_id])
            state['_seizure'] = None
        elif tile_id is None:
            state['_seizure'] = None

    def _loan_credit(self, state, p, amount, reason):
        loan = p['loan']
        if loan is None:
            p['cash'] += amount
            self._log(state, 'loan', f"{reason}得 {amount}，贷款已还清，全部还给{p['name']}", player=p['player_id'])
            return
        used = min(amount, loan['balance'])
        loan['balance'] -= used
        p['cash'] += amount - used
        text = f"{reason}得 {amount}，抵扣贷款 {used}"
        if amount > used:
            text += f"，多出 {amount - used} 还给{p['name']}"
        if loan['balance'] <= 0:
            p['loan'] = None
            state['_seizure'] = None
            text += '；贷款已还清'
        self._log(state, 'loan', text, player=p['player_id'])

    # ------------------------------------------------------------------
    # auctions (seizure-aware)

    def _start_auction(self, state):
        super()._start_auction(state)
        seizure = state.get('_seizure')
        auction = state['auction']
        if seizure and seizure.get('tile_id') == auction['tile_id']:
            auction['eligible'] = [x for x in auction['eligible'] if x != seizure['player_id']]
            auction['seizure_of'] = seizure['player_id']
            if state['turn_player_id'] not in auction['eligible']:
                if not auction['eligible']:
                    self._finish_auction(state)
                    return
                state['turn_player_id'] = auction['eligible'][0]
        self._log(state, 'auction', f"开始拍卖 {state['tiles'][auction['tile_id']]['name']}", tiles=[auction['tile_id']])

    def _finish_auction(self, state):
        auction = state['auction']
        tile = state['tiles'][auction['tile_id']]
        seizure = state.get('_seizure')
        seized = bool(seizure and seizure.get('tile_id') == tile['id'])
        if auction['highest_bidder']:
            self._log(state, 'auction', f"{self.player(state, auction['highest_bidder'])['name']}以 {auction['bid']} 拍得 {tile['name']}",
                      tiles=[tile['id']], player=auction['highest_bidder'])
        else:
            self._log(state, 'auction', f"{tile['name']}流拍", tiles=[tile['id']])
        bid, winner = auction['bid'], auction['highest_bidder']
        if seized:
            borrower = self.player(state, seizure['player_id'])
            seizure['tile_id'] = None
            if winner and not borrower['bankrupt']:
                self._loan_credit(state, borrower, bid, f"拍卖{tile['name']}")
            if borrower['loan'] is None:
                state['_seizure'] = None
            elif not borrower['bankrupt']:
                self._next_seizure(state, borrower)
        super()._finish_auction(state)
        if state['phase'] == 'purchase':
            # A lot sold while its lander was deciding (seizure/flash auction).
            here = state['tiles'][self.player(state, state['current_player_id'])['position']]
            if here['owner'] is not None:
                state['phase'] = 'manage'

    # ------------------------------------------------------------------
    # bankruptcy

    def _eliminate(self, state, pid, creditor):
        p = self.player(state, pid)
        if p['bankrupt']:
            return
        for t in state['tiles']:
            if t['owner'] == pid:
                p['cash'] += t['level'] * t['build_cost'] // 2
                t['level'] = 0
                t.update(title='', motto='')
        loan = p['loan']
        if loan:
            pay = min(p['cash'], loan['balance'])
            p['cash'] -= pay
            loan['balance'] -= pay
            if loan['balance'] > 0:
                creditor = None  # bank debt first: unpaid loan sends all estates to bank auction
            p['loan'] = None
        if state.get('_seizure') and state['_seizure']['player_id'] == pid:
            state['_seizure'] = None
        for item in p['items']:
            state['_decks'][item['deck']].append(item['card'])
        p['items'] = []
        p['bus'] = 0
        state['bets'] = {k: v for k, v in state.get('bets', {}).items() if k != pid}
        state['_queue'] = [q for q in state['_queue'] if q.get('player_id') != pid]
        super()._eliminate(state, pid, creditor)

    def _terminal(self, state):
        result = super()._terminal(state)
        if result:
            state.update(choice=None, _queue=[], _choice_return=None, bets={}, acquire_window=None, _seizure=None)
        return result

    # ------------------------------------------------------------------
    # queued decisions

    def _stable(self, state):
        return (state['phase'] in ('roll', 'manage') and state['auction'] is None and state['debt'] is None
                and not state['_charges'] and state['choice'] is None and not state['_auction_queue'])

    def _advance_queue(self, state):
        guard = 0
        while state['_queue'] and self._stable(state) and guard < 20:
            guard += 1
            item = state['_queue'].pop(0)
            pid = item['player_id']
            if pid not in self.active(state):
                continue
            p = self.player(state, pid)
            if item['kind'] == 'mr':
                self._mr_move(state, p)
                self._drain_charges(state)
                continue
            if item['kind'] == 'pick_auction':
                item['options'] = [t['id'] for t in state['tiles'] if t['price'] and t['owner'] is None]
                if not item['options']:
                    continue
            if item['kind'] == 'gift':
                item['max'] = min(200, p['cash'])
            if item['kind'] == 'chat':
                item['stage'] = 'perform'
            state['_choice_return'] = dict(phase=state['phase'], turn_player_id=state['turn_player_id'])
            state.update(choice=item, phase='choice', turn_player_id=pid)

    def _end_choice(self, state):
        back = state['_choice_return'] or dict(phase='manage', turn_player_id=state['current_player_id'])
        state.update(choice=None, phase=back['phase'], turn_player_id=back['turn_player_id'], _choice_return=None)
        if state['turn_player_id'] not in self.active(state):
            state['turn_player_id'] = state['current_player_id']
        if state['current_player_id'] not in self.active(state):
            self._next_turn(state)
            return
        if state['_auction_queue'] and state['auction'] is None:
            self._start_auction(state)
        elif state['_charges']:
            self._drain_charges(state)

    def _resolve_choice(self, state, pid, move):
        choice = state['choice']
        p = self.player(state, pid)
        action = move['action']
        kind = choice['kind']
        if kind == 'triple':
            dest = move['tile_id']
            entry = self._log(state, 'triple', f"{p['name']}用三同选择前往 {state['tiles'][dest]['name']}", player=pid, tiles=[dest])
            self._end_choice(state)
            self._move_to(state, p, dest, walk=(dest - p['position']) % 40, entry=entry)
            self._drain_charges(state)
            return
        if kind == 'pick_auction':
            if action == 'pick_auction':
                self._log(state, 'auction', f"{p['name']}在拍卖行挑了 {state['tiles'][move['tile_id']]['name']}", player=pid,
                          tiles=[move['tile_id']])
                state['_auction_queue'].append(move['tile_id'])
            else:
                self._log(state, 'auction', f"{p['name']}在拍卖行没有开拍", player=pid)
            self._end_choice(state)
            return
        if kind == 'gift':
            if action == 'give_gift':
                to = self.player(state, move['to'])
                p['cash'] -= move['amount']
                to['cash'] += move['amount']
                self._log(state, 'gift', f"{p['name']}给{to['name']}发了 {move['amount']} 的红包", player=pid, amount=move['amount'])
                self.note(state, f"{p['name']}给{to['name']}发红包{move['amount']}。")
            else:
                self._log(state, 'gift', f"{p['name']}囊中羞涩，红包下次再发", player=pid)
            self._end_choice(state)
            return
        if kind == 'chat':
            if choice['stage'] == 'perform':
                if action == 'skip_choice':
                    self._log(state, 'chat', f"{p['name']}放弃了聊天卡「{choice['card']}」", player=pid)
                    self._end_choice(state)
                    return
                text = clean_text(move['text'], state['rules']['chat_text_max'])
                voters = [v for v in self._seat_order_from(state, pid) if v != pid]
                choice.update(stage='vote', text=text, voters=voters, votes={})
                self._log(state, 'chat', f"{p['name']}（{choice['card']}）：{text}", player=pid)
                self.note(state, f"{p['name']}完成聊天卡，等待大家投票。")
                if not voters:
                    self._settle_chat(state)
                    return
                state['turn_player_id'] = voters[0]
                return
            choice['votes'][pid] = bool(move['accept'])
            pending = [v for v in choice['voters'] if v not in choice['votes'] and v in self.active(state)]
            if pending:
                state['turn_player_id'] = pending[0]
                return
            self._settle_chat(state)
            return
        if kind in ('nope_fine', 'nope_attack'):
            used = action == 'use_nope'
            if used:
                self._take_item(state, p, 'nope')
            if kind == 'nope_fine':
                if used:
                    self._log(state, 'nope', f"{p['name']}打出「不行」，免掉了 {choice['amount']}（{choice['reason']}）", player=pid)
                else:
                    self._charge(state, pid, choice['amount'], reason=choice['reason'])
                self._end_choice(state)
                return
            attack = choice['attack']
            attacker = self.player(state, attack['attacker'])
            label = ITEM_LABELS[attack['item']]
            self._end_choice(state)
            if used:
                self._log(state, 'nope', f"{p['name']}打出「不行」，挡下了{attacker['name']}的{label}卡", player=pid,
                          tiles=[x for x in (attack.get('tile_id'), attack.get('target_tile_id')) if x is not None])
            else:
                self._execute_attack(state, attack)
            return

    def _seat_order_from(self, state, pid):
        ids = [p['player_id'] for p in state['players']]
        start = ids.index(pid)
        return [ids[(start + i) % len(ids)] for i in range(len(ids)) if ids[(start + i) % len(ids)] in self.active(state)]

    def _settle_chat(self, state):
        choice = state['choice']
        drawer = self.player(state, choice['player_id'])
        votes = {k: v for k, v in choice['votes'].items() if k in self.active(state)}
        yes = [k for k, v in votes.items() if v]
        voters = [v for v in choice['voters'] if v in self.active(state)]
        amount, mode = choice['amount'], choice['mode']
        if mode == 'per_yes':
            for voter in yes:
                self._charge(state, voter, amount, drawer['player_id'], f"聊天卡「{choice['card']}」")
            text = f"「{choice['card']}」获得 {len(yes)}/{len(voters)} 张赞成票" + (f"，每票付 {amount}" if yes else '')
        else:
            passed = (len(yes) * 2 > len(voters)) if mode == 'majority' else bool(yes)
            if passed:
                drawer['cash'] += amount
            text = f"「{choice['card']}」获得 {len(yes)}/{len(voters)} 张赞成票，" + (f"银行奖励 {amount}" if passed else '未通过')
        self._log(state, 'vote', text, player=drawer['player_id'])
        self.note(state, text + '。')
        self._end_choice(state)

    # ------------------------------------------------------------------
    # items

    def _use_item(self, state, p, move):
        item = move['item']
        pid = p['player_id']
        self._take_item(state, p, item)
        if item == 'acquire':
            tile = state['tiles'][state['acquire_window']['tile_id']]
            attack = dict(attacker=pid, item='acquire', tile_id=tile['id'], target=tile['owner'], price=tile['price'] * 2)
            text = f"{p['name']}对{self.player(state, tile['owner'])['name']}使用收购卡：想以 {tile['price'] * 2} 买下 {tile['name']}"
            state['acquire_window'] = None
        else:
            mine, theirs = state['tiles'][move['tile_id']], state['tiles'][move['target_tile_id']]
            attack = dict(attacker=pid, item='swap', tile_id=mine['id'], target_tile_id=theirs['id'], target=theirs['owner'])
            text = f"{p['name']}对{self.player(state, theirs['owner'])['name']}使用强行交易卡：用 {mine['name']} 换 {theirs['name']}"
        self._log(state, 'item', text, player=pid, tiles=[x for x in (attack.get('tile_id'), attack.get('target_tile_id')) if x is not None])
        self.note(state, text + '。')
        # The target always gets a confirmation window, so hands stay private.
        state['_choice_return'] = dict(phase=state['phase'], turn_player_id=state['turn_player_id'])
        state.update(phase='choice', turn_player_id=attack['target'],
                     choice=dict(kind='nope_attack', player_id=attack['target'], attack=attack, text=text))

    def _execute_attack(self, state, attack):
        attacker = self.player(state, attack['attacker'])
        if attack['item'] == 'acquire':
            tile = state['tiles'][attack['tile_id']]
            if tile['owner'] != attack['target'] or not self._attackable(state, tile) or attacker['cash'] < attack['price']:
                self._log(state, 'item', '收购条件已失效，收购取消')
                return
            owner = self.player(state, tile['owner'])
            attacker['cash'] -= attack['price']
            owner['cash'] += attack['price']
            tile['owner'] = attacker['player_id']
            self._log(state, 'item', f"收购成功：{attacker['name']}付 {attack['price']} 买下 {tile['name']}", tiles=[tile['id']],
                      player=attacker['player_id'])
            return
        mine, theirs = state['tiles'][attack['tile_id']], state['tiles'][attack['target_tile_id']]
        if (mine['owner'] != attacker['player_id'] or theirs['owner'] != attack['target']
                or not self._attackable(state, mine) or not self._attackable(state, theirs)):
            self._log(state, 'item', '强行交易条件已失效，交易取消')
            return
        mine['owner'], theirs['owner'] = theirs['owner'], mine['owner']
        self._log(state, 'item', f"强行交易成功：{mine['name']} ⇄ {theirs['name']}", tiles=[mine['id'], theirs['id']],
                  player=attacker['player_id'])

    # ------------------------------------------------------------------
    # apply

    def apply_action(self, state, move, actor):
        self.validate_action(state, move, actor)
        previous_turn = state['turn_number']
        state = deepcopy(state)
        self._migrate_trades(state)
        before = self._delta_basis(state)
        owners = {t['id']: t['owner'] for t in state['tiles']}
        state['last_action_note'] = ''
        pid = actor['player_id']
        p = self.player(state, pid)
        action = move['action']
        rules = state.get('rules') or {}
        if action == 'bet':
            p['cash'] -= rules['bet_stake']
            state['bets'][pid] = move['choice']
            label = {'big': '大（≥8）', 'small': '小（≤6）', 'seven': '7'}[move['choice']]
            self._log(state, 'bet', f"{p['name']}押了「{label}」，押金 {rules['bet_stake']}", player=pid)
            self.note(state, f"{p['name']}押注{label}。")
        elif action == 'choose_edition':
            self._apply_edition(state, move['edition'])
            for x in state['players']:
                x.update(items=[], bus=0, loan=None, passed_go=False)
            state.update(edition=move['edition'], phase='roll')
            self._log(state, 'setup', f"{p['name']}选择了「{state['edition_name']}」版本，开局！", player=pid)
            self.note(state, f"版本：{state['edition_name']}。")
        elif action == 'roll':
            self._roll(state, p)
            self._drain_charges(state)
        elif action == 'use_bus':
            p['bus'] -= 1
            state['rolled_this_turn'] = True
            self._refund_bets(state)
            state.update(extra_roll=False, doubles=0, dice=[], speed=None)
            dest = move['tile_id']
            entry = self._log(state, 'bus', f"{p['name']}坐巴士前往 {state['tiles'][dest]['name']}", player=pid, tiles=[dest])
            self._move_to(state, p, dest, walk=(dest - p['position']) % 40, entry=entry)
            self._drain_charges(state)
        elif action == 'end_turn':
            self._next_turn(state)
        elif action == 'buy':
            t = state['tiles'][p['position']]
            price = self.price_of(state, t)
            p['cash'] -= price
            t['owner'] = pid
            state['phase'] = 'manage'
            self.note(state, f"{p['name']}以{price}买下{t['name']}。")
            self._log(state, 'buy', f"{p['name']}以 {price} 买下 {t['name']}", player=pid, tiles=[t['id']])
        elif action == 'auction':
            state['phase'] = 'manage'
            state['_auction_queue'].append(p['position'])
            self._start_auction(state)
        elif action in ('bid', 'pass_bid'):
            if action == 'bid':
                self._log(state, 'bid', f"{p['name']}出价 {move['amount']}", player=pid, tiles=[state['auction']['tile_id']])
            self._auction_step(state, pid, move)
        elif action == 'propose_trade':
            trade = {k: deepcopy(move[k]) for k in ('to', 'give_cash', 'take_cash', 'give_tiles', 'take_tiles')}
            trade['from'] = pid
            state['trades'].append(trade)
            state.setdefault('_trade_meta', {})[move['to']] = dict(
                assets=[dict(id=i, mortgaged=state['tiles'][i]['mortgaged']) for i in move['give_tiles'] + move['take_tiles']],
                proposed_turn=state['turn_number'])
            state['trades_this_turn'] += 1
            self.note(state, '已提出交易，等待接收方在自己的正常回合回应。')
            self._log(state, 'trade', f"{p['name']}向{self.player(state, move['to'])['name']}提出交易", player=pid)
        elif action == 'respond_trade':
            trade = self._trade_for(state, pid)
            if move['accept']:
                a, b = self.player(state, trade['from']), self.player(state, trade['to'])
                delta = trade['give_cash'] - trade['take_cash']
                a['cash'] -= delta
                b['cash'] += delta
                for ids, owner in ((trade['give_tiles'], trade['to']), (trade['take_tiles'], trade['from'])):
                    for i in ids:
                        state['tiles'][i]['owner'] = owner
            else:
                state.setdefault('_trade_rejections', []).append(dict(deepcopy(trade), remaining=4))
            self.note(state, '交易已同时交割。' if move['accept'] else '交易已拒绝。')
            self._log(state, 'trade', ('交易完成：' if move['accept'] else '交易被拒绝：')
                      + f"{self.player(state, trade['from'])['name']} ⇄ {self.player(state, trade['to'])['name']}",
                      tiles=trade['give_tiles'] + trade['take_tiles'])
            self._clear_trade(state, trade)
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
            label = {'build': '建造一级', 'sell_building': '出售一级建筑', 'mortgage': '已抵押', 'redeem': '已赎回'}[action]
            self.note(state, f"{t['name']}：{label}。")
            self._log(state, action, f"{p['name']}·{t['name']}：{label}", player=pid, tiles=[t['id']])
            if state['phase'] == 'debt':
                self._drain_charges(state)
        elif action in ('pay_bail', 'use_jail_card'):
            if action == 'pay_bail':
                p['cash'] -= rules['bail']
            else:
                state['_decks'][p['jail_cards'].pop(0)].append(0)
            p.update(jailed=False, jail_turns=0)
            self.note(state, '已出狱，可以掷骰。')
            self._log(state, 'jail_out', f"{p['name']}{'交 ' + str(rules['bail']) if action == 'pay_bail' else '用出狱卡'}出狱",
                      player=pid, tiles=[10])
        elif action == 'bankrupt':
            self._eliminate(state, pid, state['debt']['creditor'])
            self._log(state, 'bankrupt', f"{p['name']}破产离场", player=pid)
            if not self._terminal(state):
                self._drain_charges(state)
        elif action == 'take_loan':
            p['cash'] += move['amount']
            p['loan'] = dict(principal=move['amount'], balance=move['amount'], laps_left=rules['loan_laps'])
            self._log(state, 'loan', f"{p['name']}向银行借款 {move['amount']}（{rules['loan_laps']} 圈内还清）", player=pid)
            self.note(state, f"借款{move['amount']}。")
            if state['phase'] == 'debt':
                self._drain_charges(state)
        elif action == 'repay_loan':
            p['cash'] -= move['amount']
            p['loan']['balance'] -= move['amount']
            text = f"{p['name']}还款 {move['amount']}"
            if p['loan']['balance'] <= 0:
                p['loan'] = None
                text += '，贷款已还清'
                self._cancel_seizure(state, p)
            self._log(state, 'loan', text, player=pid)
            self.note(state, text + '。')
        elif action == 'name_tile':
            t = state['tiles'][move['tile_id']]
            t['title'] = clean_text(move['name'], rules['name_max'])
            t['motto'] = clean_text(move['motto'], rules['motto_max'], allow_empty=True)
            self._log(state, 'name', f"{p['name']}给 {t['name']} 挂牌「{t['title']}」" + (f"：{t['motto']}" if t['motto'] else ''),
                      player=pid, tiles=[t['id']])
        elif action == 'use_item':
            self._use_item(state, p, move)
        elif state['phase'] == 'choice':
            self._resolve_choice(state, pid, move)
        # Ownership changes always take any custom sign down.
        for t in state['tiles']:
            if t['owner'] != owners[t['id']] and (t['title'] or t['motto']):
                t.update(title='', motto='')
        if state['phase'] != 'finished':
            if state['_charges'] and state['phase'] in ('roll', 'purchase', 'manage'):
                self._drain_charges(state)
            if state['_auction_queue'] and state['auction'] is None and state['phase'] in ('roll', 'purchase', 'manage'):
                self._start_auction(state)
            self._advance_queue(state)
        self._cancel_invalid_trade(state)
        self._sync_trade_alias(state)
        state['action_seq'] += 1
        result = self._terminal(state)
        delta = self._delta(before, self._delta_basis(state))
        return MoveResult(state=state, next_player_id=state['turn_player_id'], result=result,
                          turn_completed=result is not None or state['turn_number'] != previous_turn,
                          participant_activity={x['player_id']: 'eliminated' for x in state['players'] if x['bankrupt']},
                          note=state['last_action_note'],
                          public_event={'monopoly_plus_delta': delta} if delta else None)

    def apply_resignation(self, state, resigned_player_id, participants):
        pid = resigned_player_id
        choice = state.get('choice')
        if choice:
            if choice['kind'] == 'chat' and choice.get('stage') == 'vote' and choice['player_id'] != pid:
                choice['votes'][pid] = False
                choice['voters'] = [v for v in choice['voters'] if v != pid]
            elif choice['player_id'] == pid or (choice['kind'] == 'nope_attack' and choice['attack']['attacker'] == pid):
                state.update(choice=None, phase=(state['_choice_return'] or {}).get('phase', 'manage'),
                             turn_player_id=state['current_player_id'], _choice_return=None)
        super().apply_resignation(state, pid, participants)
        choice = state.get('choice')
        if choice and choice['kind'] == 'chat' and choice.get('stage') == 'vote' and state['phase'] == 'choice':
            pending = [v for v in choice['voters'] if v not in choice['votes'] and v in self.active(state)]
            if pending:
                state['turn_player_id'] = pending[0]
            elif not self._terminal(deepcopy(state)):
                self._settle_chat(state)
        if state['phase'] != 'finished' and state['turn_player_id'] not in self.active(state):
            state['turn_player_id'] = state['current_player_id']

    # ------------------------------------------------------------------
    # projections

    def _delta_basis(self, state):
        players = [[x['player_id'], x['cash'], x['position'], x['bankrupt'], x['jailed'], x['jail_turns'],
                    len(x.get('items', [])), x.get('bus', 0), (x.get('loan') or {}).get('balance'),
                    (x.get('loan') or {}).get('laps_left'), x.get('passed_go', False)] for x in state['players']]
        tiles = [[t['id'], t['owner'], t['level'], t['mortgaged'], t.get('title', ''), t.get('motto', '')]
                 for t in state['tiles'] if t['price']]
        basis = {k: deepcopy(state.get(k)) for k in (
            'phase', 'edition', 'turn_player_id', 'current_player_id', 'turn_number', 'dice', 'speed', 'extra_roll',
            'doubles', 'auction', 'debt', 'choice', 'bets', 'last_bets', 'modifiers')}
        basis['trades'] = deepcopy(self.pending_trades(state))
        basis['players'] = players
        basis['tiles'] = tiles
        basis['log_seq'] = state['log'][-1]['seq'] if state.get('log') else 0
        basis['log'] = deepcopy(state.get('log', []))
        basis['supply'] = self.supply(state)
        return basis

    @staticmethod
    def _delta(before, after):
        delta = {}
        for key, value in after.items():
            if key in ('players', 'tiles', 'log', 'log_seq'):
                continue
            if before.get(key) != value:
                delta[key] = value
        rows_before = {r[0]: r for r in before['players']}
        rows = [r for r in after['players'] if rows_before.get(r[0]) != r]
        if rows:
            delta['p'] = rows
        tiles_before = {r[0]: r for r in before['tiles']}
        rows = [r for r in after['tiles'] if tiles_before.get(r[0]) != r]
        if rows:
            delta['t'] = rows
        new_log = [e['text'] for e in after['log'] if e['seq'] > before['log_seq']]
        if new_log:
            delta['log'] = new_log
        if 'turn_player_id' in delta:
            delta['next'] = delta.pop('turn_player_id')
        if 'choice' in delta and delta['choice']:
            delta['choice'] = {k: v for k, v in delta['choice'].items() if k != 'options'}
        return delta

    def public_state(self, state, participants):
        public = super().public_state(state, participants)
        for player in public['players']:
            player['items'] = len(player.get('items', []))
        # Item actions would reveal a private hand; only the holder sees them.
        public['legal_actions'] = [a for a in public['legal_actions'] if a['action'] not in ('use_item', 'use_nope')]
        public['editions'] = editions.edition_menu()
        for t in public['tiles']:
            if t['price']:
                t['sale_price'] = self.price_of(state, t)
                t['closed'] = self.closed(state, t['id'])
        public['bet_window'] = bool(state['phase'] == 'roll' and not state.get('rolled_this_turn')
                                    and state.get('rules', {}).get('bets'))
        return public

    def private_state(self, state, viewer, participants):
        private = super().private_state(state, viewer, participants)
        pid = viewer['player_id']
        p = next((p for p in state['players'] if p['player_id'] == pid), None)
        private.pop('decision_context', None)
        private['guide'] = ('复制 legal_actions 并携带 revision。不是自己回合时看 side_actions 押注。'
                            '道具卡只有你自己看得到；item_options 列出现在能用的道具。')
        private['side_actions'] = self.side_actions(state, pid)
        if p:
            private['items'] = [dict(kind=i['kind'], name=ITEM_LABELS[i['kind']]) for i in p['items']]
            private['item_options'] = self.item_options(state, pid)
            if state.get('rules', {}).get('loans'):
                private['loan_limit'] = self.loan_limit(state, pid)
        return private

    def terminal_public_state(self, state, participants):
        public = self.public_state(state, participants)
        public.update(phase='finished', turn_player_id=None, legal_actions=[])
        return public

    def participant_summary(self, state, participant, participants):
        p = next((p for p in state['players'] if p['player_id'] == participant['player_id']), None)
        return {'现金': p['cash'], '地产': p.get('property_count', 0)} if p else {}

    # ---- MCP: compact bootstrap/turn projections; full map via full_state ----

    DELTA_FORMAT = ('monopoly_plus_delta 只给本动作改变的键：p=[player_id,cash,position,bankrupt,jailed,jail_turns,'
                    'items张数,bus票数,loan_balance,loan_laps_left,passed_go] 按 player_id 覆盖；'
                    't=[id,owner,level,mortgaged,title,motto] 按 id 覆盖；log=本动作新增的事件文字；'
                    'next=turn_player_id；其余键整体替换（null 清空）。完整地图与租金表用 state(full_state=true) 查询。')

    @staticmethod
    def _tile_code(t):
        if not t['price']:
            return f"{t['id']} {t['name']} {t['kind']}"
        rents = '/'.join(str(r) for r in t['rents'])
        extra = f" 房{t['build_cost']}" if t['kind'] == 'property' else ''
        return f"{t['id']} {t['name']} {t['kind']} g{t['group']} ¥{t['price']} 租{rents}{extra}"

    def mcp_bootstrap_state(self, public_state, viewer, participants):
        keep = ('phase', 'edition', 'edition_name', 'current_player_id', 'turn_player_id', 'turn_number', 'action_seq',
                'dice', 'speed', 'extra_roll', 'doubles', 'auction', 'debt', 'trades', 'choice', 'bets', 'modifiers', 'editions')
        board = {k: deepcopy(public_state.get(k)) for k in keep}
        board['players'] = [[x['player_id'], x['cash'], x['position'], x['bankrupt'], x['jailed'], x['jail_turns'],
                             x.get('items', 0), x.get('bus', 0), (x.get('loan') or {}).get('balance'),
                             (x.get('loan') or {}).get('laps_left'), x.get('passed_go', False)] for x in public_state['players']]
        board['owned'] = [[t['id'], t['owner'], t['level'], t['mortgaged'], t.get('title', ''), t.get('motto', '')]
                          for t in public_state['tiles'] if t['owner'] is not None]
        board['map'] = [self._tile_code(t) for t in public_state['tiles']]
        board['recent'] = [e['text'] for e in public_state.get('log', [])[-6:]]
        board['delta_format'] = self.DELTA_FORMAT
        return board

    def mcp_snapshot_state(self, public_state, viewer, participants):
        snapshot = super(Monopoly, self).mcp_snapshot_state(public_state, viewer, participants)
        snapshot['delta_format'] = self.DELTA_FORMAT
        return snapshot

    def mcp_private_state(self, private_state, viewer, participants):
        private = deepcopy(private_state)
        private.pop('guide', None)
        options = private.pop('trade_options', {})
        if options.get('partners'):
            private['trade_options'] = {'partners': options['partners']}
        actions = private.get('legal_actions') or []
        dests = [a['tile_id'] for a in actions if a['action'] == 'choose_destination']
        if len(dests) > 8:
            private['legal_actions'] = [a for a in actions if a['action'] != 'choose_destination'] + [
                dict(action='choose_destination', tile_id=dests[0], action_seq=actions[0]['action_seq'])]
            private['choose_destination_any_of'] = dests
        # Group repetitive tile/amount examples; the move shape is in move_format.
        actions = private.get('legal_actions') or []
        grouped = {}
        for a in actions:
            if a['action'] in ('build', 'sell_building', 'mortgage', 'redeem', 'use_bus'):
                grouped.setdefault(a['action'], []).append(a['tile_id'])
        if grouped:
            private['legal_actions'] = [a for a in actions if a['action'] not in grouped]
            private['tile_actions'] = grouped
        if any(a['action'] == 'take_loan' for a in actions):
            private['legal_actions'] = [a for a in private['legal_actions'] if a['action'] != 'take_loan']
            private['take_loan'] = f"amount {editions.CLASSIC_PLUS_RULES['loan_min']}..{private.get('loan_limit')}"
        private.pop('loan_limit', None)
        if not private.get('side_actions'):
            private.pop('side_actions', None)
        if not private.get('item_options'):
            private.pop('item_options', None)
        return private

    def mcp_turn_private_state(self, private, public):
        """Each turn: only what the decision needs, never the whole map."""
        result = deepcopy(private)
        result['here'] = {k: public.get(k) for k in ('phase', 'choice', 'auction', 'debt') if public.get(k)}
        return result

    def mcp_lifecycle_state(self, public):
        return {k: public.get(k) for k in ('phase', 'turn_player_id', 'current_player_id', 'players', 'owned', 'choice', 'auction')
                if k in public}

    # ------------------------------------------------------------------
    # NPC (local policy; also used for timeout assistance)

    def npc_legal_actions(self, state, actor, participants):
        pid = actor['player_id']
        if state['phase'] in ('setup', 'choice'):
            actions = self.legal_actions(state, pid)
            choice = state.get('choice') or {}
            if (actor.get('participant_kind') == 'system_npc' and choice.get('kind') == 'chat'
                    and choice.get('stage') == 'perform'):
                actions.append(dict(action='perform', text=NPC_CHAT_LINES.get(choice['card'], '大家好，我来献丑啦！'),
                                    action_seq=state['action_seq']))
            return actions
        actions = super().npc_legal_actions(state, actor, participants)
        if actor.get('participant_kind') == 'system_npc':
            options = self.item_options(state, pid).get('swap')
            if options:
                for mine in options['mine']:
                    for theirs in options['theirs']:
                        target = state['tiles'][theirs]
                        if (sum(t['owner'] == pid for t in self.group(state, target)) >= 1
                                and not any(t['owner'] == pid for t in self.group(state, state['tiles'][mine]) if t['id'] != mine)):
                            actions.append(dict(action='use_item', item='swap', tile_id=mine, target_tile_id=theirs,
                                                action_seq=state['action_seq']))
        return actions

    def choose_local_npc_action(self, state, actor, participants):
        actions = self.npc_legal_actions(state, actor, participants)
        pid = actor['player_id']
        npc = actor.get('participant_kind') == 'system_npc'
        p = self.player(state, pid)
        find = lambda name: next((a for a in actions if a['action'] == name), None)
        if state['phase'] == 'setup':
            return actions[0]
        if state['phase'] == 'choice':
            kind = state['choice']['kind']
            if kind == 'triple':
                good = [a for a in actions if state['tiles'][a['tile_id']]['price']
                        and state['tiles'][a['tile_id']]['owner'] is None
                        and self.price_of(state, state['tiles'][a['tile_id']]) <= p['cash'] - 100]
                return good[0] if good else actions[0]
            if kind == 'pick_auction':
                picks = [a for a in actions if a['action'] == 'pick_auction']
                mine = [a for a in picks if any(t['owner'] == pid for t in self.group(state, state['tiles'][a['tile_id']]))]
                return (mine or picks or actions)[0] if npc else find('skip_choice') or actions[0]
            if kind == 'gift':
                gifts = [a for a in actions if a['action'] == 'give_gift' and a['amount'] == 50]
                if gifts:
                    poorest = min(gifts, key=lambda a: self.player(state, a['to'])['cash'])
                    return poorest
                return actions[0]
            if kind == 'chat':
                if state['choice']['stage'] == 'perform':
                    return find('perform') or find('skip_choice')
                # Timeout assistance never approves on a real player's behalf.
                return next(a for a in actions if a['accept'] is (npc and len(state['choice'].get('text', '')) >= 8))
            if kind == 'nope_fine':
                return find('use_nope') if npc and state['choice']['amount'] >= 50 and find('use_nope') else find('decline_nope')
            if kind == 'nope_attack':
                return find('use_nope') or find('decline_nope')
        if state['phase'] == 'debt' and p['loan'] is None:
            debt = state['debt']['amount'] - p['cash']
            loans = [a for a in actions if a['action'] == 'take_loan' and a['amount'] >= debt]
            if loans and npc:
                return loans[0]
        if state['phase'] in ('roll', 'manage'):
            acquire = find('use_item') if npc else None
            if acquire and acquire.get('item') == 'acquire' and p['cash'] - state['tiles'][p['position']]['price'] * 2 >= 300:
                return acquire
            if acquire and acquire.get('item') == 'swap':
                return acquire
            repay = find('repay_loan')
            if repay and npc and p['cash'] - repay['amount'] >= 400:
                return repay
        if state['phase'] == 'roll' and npc:
            bus = [a for a in actions if a['action'] == 'use_bus' and state['tiles'][a['tile_id']]['price']
                   and state['tiles'][a['tile_id']]['owner'] is None]
            if bus:
                return bus[-1]
        filtered = [a for a in actions if a['action'] not in ('take_loan', 'repay_loan', 'use_bus', 'use_item')]
        if p['jailed'] and state['phase'] == 'roll' and p['jail_turns'] >= 1:
            pay = next((a for a in filtered if a['action'] == 'pay_bail'), None)
            if pay:
                return pay
        return self._base_choice(state, actor, filtered or actions)

    def _base_choice(self, state, actor, actions):
        # Same decisions as the classic local policy over the filtered list.
        p = self.player(state, actor['player_id'])
        find = lambda name: next((a for a in actions if a['action'] == name), None)
        if find('respond_trade'):
            accept = False
            if actor.get('participant_kind') == 'system_npc':
                t = self._trade_for(state, actor['player_id'])
                value = lambda ids: sum(state['tiles'][i]['price'] - (state['tiles'][i]['redemption_cost'] if state['tiles'][i]['mortgaged'] else 0) for i in ids)
                accept = value(t['give_tiles']) + t['give_cash'] >= value(t['take_tiles']) + t['take_cash']
            return next((a for a in actions if a.get('accept') == accept), find('respond_trade'))
        if state['phase'] == 'debt':
            return find('sell_building') or find('mortgage') or find('bankrupt')
        if state['phase'] == 'auction':
            tile = state['tiles'][state['auction']['tile_id']]
            affordable = [a for a in actions if a['action'] == 'bid' and a['amount'] <= min(tile['price'], p['cash'] - 150)]
            return affordable[-1] if affordable else find('pass_bid')
        if state['phase'] == 'purchase':
            tile = state['tiles'][p['position']]
            return find('buy') if find('buy') and p['cash'] - self.price_of(state, tile) >= 100 else find('auction')
        build = next((a for a in actions if a['action'] == 'build'
                      and p['cash'] - state['tiles'][a['tile_id']]['build_cost'] >= 180), None)
        redeem = next((a for a in actions if a['action'] == 'redeem' and p['cash'] - state['tiles'][a['tile_id']]['redemption_cost'] >= 250), None)
        return build or redeem or find('propose_trade') or find('use_jail_card') or find('roll') or find('end_turn') or actions[0]

    def npc_action_spec(self, state, actor):
        return None

    def validate_npc_action(self, state, move, actor):
        self.validate_action(state, move, actor)

    def format_action(self, state, move, actor):
        labels = {'roll': '掷骰', 'buy': '买地', 'auction': '开启拍卖', 'end_turn': '结束回合',
                  'build': '建房', 'sell_building': '卖房', 'mortgage': '抵押', 'redeem': '赎回',
                  'bid': '出价', 'pass_bid': '退出拍卖', 'propose_trade': '提出交易', 'respond_trade': '回应交易',
                  'bankrupt': '破产', 'pay_bail': '交保释金', 'use_jail_card': '使用出狱卡',
                  'choose_edition': '选择版本', 'bet': '押注', 'use_bus': '坐巴士', 'take_loan': '借款',
                  'repay_loan': '还款', 'name_tile': '挂地名牌', 'use_item': '使用道具卡', 'use_nope': '打出「不行」',
                  'decline_nope': '不使用「不行」', 'choose_destination': '三同选落点', 'pick_auction': '拍卖行开拍',
                  'skip_choice': '跳过', 'give_gift': '发红包', 'perform': '完成聊天卡', 'vote': '投票'}
        return labels.get(move.get('action'), '大富翁·改行动') if isinstance(move, dict) else '大富翁·改行动'
