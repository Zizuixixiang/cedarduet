"""Three-seat variant using Mahjong's durable action/response/projection protocol.

Reuse PyMahjongGB's pure regular/seven-pairs shanten (including quad pairs).
A cached decomposition supplies variant scoring and the actual winning-tile gap;
never use the MCR fan calculator or its aggregate special-hand shanten.
"""
from collections import Counter
from copy import deepcopy
from functools import lru_cache
from math import prod

from MahjongGB import RegularShanten, SevenPairsShanten

from .mahjong import Mahjong
from .base import MoveResult

CODES = tuple(f'{s}{n}' for s, ranks in [('B', 9), ('T', 9), ('J', 3)] for n in range(1, ranks + 1))


@lru_cache(maxsize=32768)
def _groups(codes):
    if not codes:
        return ((),)
    counts = Counter(codes)
    first = codes[0]
    choices = []
    if counts[first] >= 3:
        choices.append((first,) * 3)
    if first[0] in 'BT' and int(first[1]) <= 7:
        seq = tuple(f'{first[0]}{int(first[1]) + i}' for i in range(3))
        if all(counts[t] for t in seq):
            choices.append(seq)
    out = []
    for group in choices:
        remaining = list(codes)
        for tile in group:
            remaining.remove(tile)
        out.extend((group,) + tail for tail in _groups(tuple(remaining)))
    return tuple(out)


@lru_cache(maxsize=32768)
def structures(codes, meld_count=0):
    """Return all (pair, concealed groups), with physical count validation."""
    if len(codes) != 14 - 3 * meld_count or any(t not in CODES for t in codes):
        return ()
    counts = Counter(codes)
    if max(counts.values(), default=0) > 4:
        return ()
    out = []
    for pair, count in counts.items():
        if count >= 2:
            rest = list(codes)
            rest.remove(pair)
            rest.remove(pair)
            out.extend((pair, groups) for groups in _groups(tuple(rest)))
    return tuple(out)


def seven_pairs(codes):
    counts = Counter(codes)
    return len(codes) == 14 and all(t in CODES and n in (2, 4) for t, n in counts.items())


def winning(codes, meld_count=0):
    codes = tuple(sorted(codes))
    return bool(structures(codes, meld_count) or (meld_count == 0 and seven_pairs(codes)))


def hand_shanten(codes):
    regular = RegularShanten(tuple(codes))[0]
    return min(regular, SevenPairsShanten(tuple(codes))[0]) if len(codes) == 13 else regular


def waits(codes, melds=()):
    if len(codes) != 13 - 3 * len(melds):
        return []
    candidates = set()
    value, useful = RegularShanten(tuple(codes))
    if value == 0:
        candidates.update(useful)
    if not melds:
        value, useful = SevenPairsShanten(tuple(codes))
        if value == 0:
            candidates.update(useful)
    owned = Counter(codes)
    owned.update(t['code'] for m in melds for t in m['tiles'])
    return [t for t in CODES if t in candidates and owned[t] < 4 and winning([*codes, t], len(melds))]


class KaWuXing(Mahjong):
    game_type = 'ka_wu_xing'
    mcp_event_key = 'mahjong_delta'

    @staticmethod
    def mcp_event(event, previous):
        # Already an action delta; legacy read helpers need no extra transform.
        return event

    display_name = '襄阳卡五星'
    min_players = max_players = recommended_players = 3
    allowed_player_counts = (3,)
    rules_text = (
        '【襄阳卡五星 · 基础规则】\n三人84张：筒、条各1–9及中发白各四张，无万、风、花。'
        '庄14张先打，闲家13张；任意对子作将，四面子一对或七对（四张可作两对）。'
        '不能吃，可碰、直杠、补杠、暗杠；摸牌墙头，杠补墙尾。仅打一手，打完结束。\n\n'
        '【倍数】\n平胡×1、碰碰胡×2、清一色×4、七对×4、龙七对×8、双龙及以上七对×16；'
        '七对族择一，不叠碰碰胡或四归一。非七对的非杠四张：含明碰为明四归一×2，否则暗四归一×4，'
        '多组只取最高一项。大三元×8/小三元×4互斥；手抓一（四副露剩单张成对）×4。'
        '胡进5筒/5条且存在以手中4、6组成456的合法拆分才是卡五星×2；已有456胡别张不计。'
        '杠上开花、抢杠胡、海底自摸各×2；不同项相乘，牌型封顶×8，双龙及以上七对封顶×16，'
        '之后仅赢家亮倒再×2（最高×32）。可选杠上炮×2、买马、漂、红中赖子。'
        '自摸平胡可胡；点炮须最终至少×2；抢杠×2可胡。\n\n'
        '【亮倒】\n在本人出牌时选择“亮倒并打…”：弃牌后必须听牌，公开固定听口及余手牌。'
        '此后只能胡或打刚摸的牌，禁止碰和任何杠；只公开固定手牌，不公开新摸暗牌。'
        '荒庄：每位亮倒者向每位未亮倒者付1基础分；全亮/全未亮均不赔，不另查叫。\n\n'
        '【响应】\n胡优先于碰杠，同级按距出牌者近者先响应，弃牌单和/一炮双响按本房设置；抢补杠单和。'
        '过合法胡（包括自摸不胡）后，至本人下次摸牌前不能接炮或抢杠。最后一张弃牌只可胡。'
        '补杠先检查抢杠，未完成不收杠分；暗杠不可抢。\n\n'
        '【积分】\n直杠来源付2，补杠另两家各付1，暗杠另两家各付2；杠分单独记账，荒庄保留。'
        '自摸另两家各付最终倍数，接炮/抢杠仅来源付最终倍数；认输付另两家各1，保留杠分。'
        '0底注仅记录局分；非0局分乘底注，终局一次结算，真实输家最多扣当前可用钱包，'
        '实际可付总额按赢家应得比例分配、整数余数按座次，不透支；NPC只记逻辑分。'
        '仅站内娱乐积分，无真实钱。'
    )
    mcp_move_format = Mahjong.move_format + ' 亮倒使用服务端 liang: 开头 action_id；公开听口见 board_state.revealed，倍数非国标番数。'

    OPTION_DEFAULTS = dict(double_hu=False, gang_discard_double=False,
                           liang_liability=False, consecutive_kong_double=False,
                           horse_count=0, piao=0, joker='none')

    @classmethod
    def normalize_options(cls, options=None, stake=0):
        if options is None:
            options = {}
        if not isinstance(options, dict):
            raise ValueError('卡五星 game_options 必须为对象')
        options = dict(options)
        if 'rounds' in options:
            rounds = options.pop('rounds')
            if type(rounds) is not int or rounds != 1:
                raise ValueError('卡五星仅支持单手，rounds 只能为整数 1')
        if set(options) - set(cls.OPTION_DEFAULTS):
            raise ValueError('未知卡五星房规字段')
        normalized = {**cls.OPTION_DEFAULTS, **options}
        for key in ('double_hu', 'gang_discard_double', 'liang_liability', 'consecutive_kong_double'):
            if type(normalized[key]) is not bool:
                raise ValueError(f'{key} 必须为布尔值')
        for key, allowed in [('horse_count', (0, 1)), ('piao', (0, 1, 3, 5))]:
            if type(normalized[key]) is not int or normalized[key] not in allowed:
                raise ValueError(f'{key} 必须为 {allowed} 中的整数')
        if type(normalized['joker']) is not str or normalized['joker'] not in ('none', 'red'):
            raise ValueError('joker 必须为 none 或 red')
        return normalized

    @classmethod
    def saved_options(cls, state):
        # Read old ended matches without revalidating their retired round count.
        # Creation always calls normalize_options directly. Never mutate saves.
        options = dict(state.get('options') or {})
        options.pop('rounds', None)
        return cls.normalize_options(options)

    def configure(self, state, options=None):
        state = deepcopy(state)
        state['options'] = self.normalize_options(options)
        state['rules_version'] = 'xiangyang-84-v2'
        return state

    def room_rules(self, state):
        o = self.saved_options(state)
        enabled = [label for key, label in [('double_hu', '一炮双响'),
                   ('gang_discard_double', '杠上炮×2'), ('liang_liability', '亮倒包赔两份'),
                   ('consecutive_kong_double', '连续杠分翻倍')] if o[key]]
        summary = '、'.join(enabled) or '单和、普通杠分、不包赔'
        return (f"【本房】{summary}；买马{o['horse_count']}张；固定漂{o['piao']}；"
                f"{'红中万能' if o['joker'] == 'red' else '红中普通字牌'}；单手结束。\n"
                + self._option_rule_details(o) + '\n' + self.rules_text)

    @staticmethod
    def _option_rule_details(o):
        # Shared explanations for room projections and the NPC-only summary.
        details = []
        if o['liang_liability']:
            details.append('赢家亮倒接炮/抢杠时，来源付两份胡分和漂分。')
        if o['horse_count']:
            details.append('自摸取剩余墙尾一马，筒条点数/中发白10，两家各另付，空墙不买。')
        if o['piao']:
            details.append(f"每个胡牌付款份额另付漂{o['piao']}，不乘胡牌倍数。")
        if o['consecutive_kong_double']:
            details.append('同人未弃牌前连续完成杠按1/2/4倍计本次杠分，抢杠未成不收费。')
        if o['horse_count'] or o['piao'] or o['consecutive_kong_double']:
            details.append('马/漂/杠分不参与胡牌封顶。')
        if o['joker'] == 'red':
            details.append('红中仅手里可万能；弃出可按普通红中接炮，禁止碰杠红中。有红中七对只普通七对，所有胡型不计四归一；实5配自然4、6才卡五星；三元不能替代发白。亮倒听口含自摸红中，接炮另按自然红中校验；亮倒锁听且禁碰杠。')
        return ''.join(details)

    @staticmethod
    def build_wall():
        return [{'id': f'{t}-{i}', 'code': t} for t in CODES for i in range(1, 5)]

    @staticmethod
    def _empty_state(order):
        state = Mahjong._empty_state(order)
        state.update(rules_version='xiangyang-84-v1', revealed={}, passed_hu=[],
                     kong_scores={p: 0 for p in order}, kong_ledger=[], horse_tiles=[],
                     kong_chain=None, kong_discard=None)
        return state

    def initialize_for_first_player(self, participants, first_player_id):
        if not 1 <= len(participants) <= 3:
            raise ValueError('卡五星固定三人，等待房可先创建一至两个席位')
        return super().initialize_for_first_player(participants, first_player_id)

    def _chi_actions(self, *args):
        return []

    def _discard_response_queue(self, state, source_player_id, tile):
        wild_discard = self.saved_options(state)['joker'] == 'red' and tile['code'] == 'J1'
        return [entry for entry in super()._discard_response_queue(state, source_player_id, tile)
                if entry['priority'] == 'hu' or (not wild_discard and entry['player_id'] not in state['revealed'])]

    @staticmethod
    def _draw(state, player_id, *, replacement):
        ok = Mahjong._draw(state, player_id, replacement=replacement)
        if ok and not replacement:
            state['kong_chain'] = None
            state['kong_discard'] = None
        if ok and player_id in state.get('passed_hu', []):
            state['passed_hu'].remove(player_id)
        return ok

    def _fan_evaluation(self, state, player_id, win_tile, *, source_kind):
        if source_kind != 'self_draw' and player_id in state.get('passed_hu', []):
            return None
        fixed_waits = state.get('revealed', {}).get(player_id, {}).get('waits')
        if fixed_waits is not None and win_tile['code'] not in fixed_waits:
            return None
        hand = list(state['hands'][player_id])
        if source_kind == 'self_draw':
            hand = [t for t in hand if t['id'] != win_tile['id']]
            if len(hand) != len(state['hands'][player_id]) - 1:
                return None
        codes = tuple(sorted([t['code'] for t in hand] + [win_tile['code']]))
        melds = state['melds'][player_id]
        all_codes = [*codes, *(t['code'] for m in melds for t in m['tiles'])]
        if any(n > 4 for n in Counter(all_codes).values()):
            return None
        if self.saved_options(state)['joker'] == 'red':
            return self._joker_evaluation(state, player_id, codes, melds, win_tile, source_kind)
        decompositions = structures(codes, len(melds))
        pairs = not melds and seven_pairs(codes)
        if not decompositions and not pairs:
            return None
        common = []
        if len({t[0] for t in all_codes}) == 1 and all_codes[0][0] in 'BT':
            common.append(('清一色', 4))
        draw = state.get('draw_context') or {}
        if source_kind == 'rob_kong':
            common.append(('抢杠胡', 2))
        if source_kind == 'discard' and self.saved_options(state)['gang_discard_double'] and state.get('kong_discard'):
            common.append(('杠上炮', 2))
        if source_kind == 'self_draw':
            if draw.get('replacement'):
                common.append(('杠上开花', 2))
            if draw.get('wall_last'):
                common.append(('海底自摸', 2))
        candidates = []
        if pairs:
            quads = sum(n == 4 for n in Counter(codes).values())
            candidates.append((common + [(('七对', '龙七对', '双龙七对')[min(quads, 2)], (4, 8, 16)[min(quads, 2)])], 16 if quads >= 2 else 8))
        for pair, groups in decompositions:
            fans = list(common)
            if all(len(set(g)) == 1 for g in groups):
                fans.append(('碰碰胡', 2))
            triples = {g[0] for g in groups if len(set(g)) == 1} | {m['engine_tile'] for m in melds}
            dragons = len(triples & {'J1', 'J2', 'J3'})
            if dragons == 3:
                fans.append(('大三元', 8))
            elif dragons == 2 and pair in ('J1', 'J2', 'J3'):
                fans.append(('小三元', 4))
            if len(melds) == 4:
                fans.append(('手抓一', 4))
            quad_fans = []
            for t, n in Counter(all_codes).items():
                if n == 4 and not any(m['kind'].endswith('gang') and m['engine_tile'] == t for m in melds):
                    quad_fans.append(('明四归一', 2) if any(m['engine_tile'] == t for m in melds) else ('暗四归一', 4))
            if quad_fans:
                fans.append(max(quad_fans, key=lambda x: x[1]))
            code = win_tile['code']
            if code in ('B5', 'T5') and (code[0]+'4', code, code[0]+'6') in groups:
                # This decomposition assigns the actual winning 5 to the gap.
                fans.append(('卡五星', 2))
            candidates.append((fans, 8))
        fans, cap = max(candidates, key=lambda item: min(item[1], prod(v for _, v in item[0])))
        raw = prod(v for _, v in fans)
        multiplier = min(cap, raw)
        if not fans:
            fans = [('平胡', 1)]
        if player_id in state['revealed']:
            fans = [*fans, ('亮倒', 2)]
            multiplier *= 2
        return dict(total_fan=multiplier, multiplier=multiplier, uncapped_multiplier=raw,
                    pattern_cap=cap, fans=[dict(name=n, points=v, count=1, fan=v, multiplier=v) for n, v in fans],
                    meets_minimum=source_kind == 'self_draw' or multiplier >= 2)

    def _hu_action(self, *args):
        action = super()._hu_action(*args)
        if action:
            action['label'] = f"胡（×{action['multiplier']}）"
        return action

    def _waits(self, state, codes, melds):
        if self.saved_options(state)['joker'] == 'red':
            from .kwx_joker import waits as joker_waits
            return joker_waits(codes, melds)
        return waits(codes, melds)

    def _joker_evaluation(self, state, player_id, codes, melds, tile, source_kind):
        from .kwx_joker import evaluations
        candidates = evaluations(codes, melds, tile['code'], win_is_wild=source_kind == 'self_draw')
        if not candidates:
            return None
        extras = []
        if source_kind == 'rob_kong':
            extras.append(('抢杠胡', 2))
        if source_kind == 'discard' and self.saved_options(state)['gang_discard_double'] and state.get('kong_discard'):
            extras.append(('杠上炮', 2))
        if source_kind == 'self_draw':
            draw = state.get('draw_context') or {}
            if draw.get('replacement'):
                extras.append(('杠上开花', 2))
            if draw.get('wall_last'):
                extras.append(('海底自摸', 2))
        candidate = max(candidates, key=lambda c: min(c['cap'], prod(v for _, v in c['fans'] + extras)))
        fans = candidate['fans'] + extras
        raw = prod(v for _, v in fans)
        multiplier = min(candidate['cap'], raw)
        if not fans:
            fans = [('平胡', 1)]
        if player_id in state['revealed']:
            fans = [*fans, ('亮倒', 2)]
            multiplier *= 2
        return dict(total_fan=multiplier, multiplier=multiplier, uncapped_multiplier=raw,
                    pattern_cap=candidate['cap'], fans=[dict(name=n, points=v, count=1, fan=v, multiplier=v) for n, v in fans],
                    meets_minimum=source_kind == 'self_draw' or multiplier >= 2)

    def legal_actions_for(self, state, player_id):
        if state.get('phase') == 'round_end':
            return []
        actions = super().legal_actions_for(state, player_id)
        if self.saved_options(state)['joker'] == 'red':
            actions = [a for a in actions if not (
                a['kind'] == 'concealed_gang' and a.get('code') == 'J1') and not (
                a['kind'] == 'added_gang' and state['melds'][player_id][a['meld_index']]['engine_tile'] == 'J1')]
        if state.get('phase') != 'discard':
            return actions
        if player_id in state['revealed']:
            return [a for a in actions if a['kind'] == 'hu' or
                    (a['kind'] == 'discard' and a['tile_id'] == state.get('drawn_tile_id'))]
        declarations = []
        for a in actions:
            if a['kind'] != 'discard':
                continue
            codes = [t['code'] for t in state['hands'][player_id] if t['id'] != a['tile_id']]
            listening = self._waits(state, codes, state['melds'][player_id])
            if listening:
                declarations.append({**a, 'action_id': f"liang:{a['tile_id']}",
                                     'declare_waits': listening, 'label': '亮倒并' + a['label'],
                                     'public_label': '亮倒并' + a['label']})
        return actions + declarations

    def _before_discard(self, state, player_id, action):
        state['kong_discard'] = player_id if (state.get('draw_context') or {}).get('replacement') else None
        state['kong_chain'] = None
        if action.get('declare_waits'):
            state['revealed'][player_id] = {
                'waits': list(action['declare_waits']),
                'hand': [self._public_tile(t) for t in state['hands'][player_id]],
            }

    def apply_action(self, state, move, actor):
        player_id = str(actor['player_id'])
        action = self._resolve_action(state, player_id, move)
        prepared = deepcopy(state)
        if action['kind'] != 'hu' and any(a['kind'] == 'hu' for a in self.legal_actions_for(state, player_id)):
            if player_id not in prepared['passed_hu']:
                prepared['passed_hu'].append(player_id)
        window = prepared.get('response_window') or {}
        if (self.saved_options(state)['double_hu'] and window.get('source_kind') == 'discard'
                and (action['kind'] == 'hu' or window.get('accepted_wins'))):
            applied = self._double_response(prepared, action, player_id)
        else:
            applied = super().apply_action(prepared, move, actor)
        after = applied.state
        # Compare only completed meld transitions. Pending/robbed added kongs
        # never enter this ledger; pass/reconnect cannot charge a second time.
        for p, melds in after['melds'].items():
            for i, meld in enumerate(melds):
                old = state['melds'][p][i]['kind'] if i < len(state['melds'][p]) else None
                kind = meld['kind']
                if kind == old or kind not in ('ming_gang', 'added_gang', 'concealed_gang'):
                    continue
                payers = [meld['source_player_id']] if kind == 'ming_gang' else [q for q in after['participant_order'] if q != p]
                chain = after.get('kong_chain') or {}
                count = chain.get('count', 0) + 1 if chain.get('player_id') == p else 1
                after['kong_chain'] = dict(player_id=p, count=count)
                factor = 2 ** (count - 1) if self.saved_options(after)['consecutive_kong_double'] else 1
                amount = (1 if kind == 'added_gang' else 2) * factor
                for q in payers:
                    after['kong_scores'][q] -= amount
                    after['kong_scores'][p] += amount
                after['kong_ledger'].append(dict(player_id=p, kind=kind, payers=payers, units=amount,
                                                 chain_count=count, chain_multiplier=factor))
        if applied.result is not None:
            applied.result['score_breakdown'] = self._score_breakdown(after, applied.result)
            applied.result['score_units'] = self.score_units(after, applied.result)
            self._complete_hand(applied)
        return applied

    def _double_response(self, state, action, player_id):
        """Persist accepted wins until all higher-priority win offers resolve.

        The discard stays in its owner's river exactly once. Win records only
        reference it, and accepted winners never reopen lower-priority claims.
        """
        window = state['response_window']
        if action['kind'] == 'hu':
            window.setdefault('accepted_wins', []).append(dict(player_id=player_id, action=deepcopy(action)))
        elif action['kind'] != 'pass':
            raise ValueError('确认胡牌后只允许其他玩家胡或过')
        self._record_action(state, player_id, action)
        window.setdefault('decisions', []).append(dict(player_id=player_id, priority='hu', decision=action['kind']))
        remaining = [entry for entry in window['queue'][window['index'] + 1:] if entry['priority'] == 'hu']
        # Keep consumed indices stable so stale pass action IDs are invalid.
        window['queue'] = window['queue'][:window['index'] + 1] + remaining
        window['index'] += 1
        if remaining:
            state['turn_player_id'] = remaining[0]['player_id']
            return MoveResult(state=state, next_player_id=state['turn_player_id'], turn_completed=False,
                              note=action['public_label'])
        accepted = window['accepted_wins']
        source = window['source_player_id']
        result = self._finish_win(state, accepted[0]['player_id'], accepted[0]['action'], source)
        result['winner_results'] = [self._winner_record(item['player_id'], item['action'], source) for item in accepted]
        result['winner_player_ids'] = [item['player_id'] for item in accepted]
        result['winning_player_ids'] = list(result['winner_player_ids'])
        result['double_hu'] = len(accepted) == 2
        return MoveResult(state=state, result=result, turn_completed=True, note=action['public_label'])

    def _winner_record(self, winner_id, action, source):
        return dict(winner_player_id=winner_id, source_player_id=source, win_type=action['source_kind'],
                    winning_tile=self._public_tile(action['tile']), multiplier=action['multiplier'],
                    total_fan=action['total_fan'], fans=deepcopy(action['fans']),
                    pattern_cap=action['pattern_cap'], uncapped_multiplier=action['uncapped_multiplier'])

    def _finish_win(self, state, winner_id, action, source_player_id):
        result = super()._finish_win(state, winner_id, action, source_player_id)
        result.pop('minimum_fan', None)
        result.update(multiplier=action['multiplier'], pattern_cap=action['pattern_cap'],
                      uncapped_multiplier=action['uncapped_multiplier'])
        result['winner_results'] = [self._winner_record(winner_id, action, source_player_id)]
        if action['source_kind'] == 'self_draw' and self.saved_options(state)['horse_count'] and state['wall']:
            # Remove one physical tile only after the win is committed.
            state.setdefault('horse_tiles', []).append(state['wall'].pop())
        result['horse_tiles'] = [self._public_tile(t) for t in state.get('horse_tiles', [])]
        return result

    def _score_breakdown(self, state, result):
        order = state['participant_order']
        components = {key: dict.fromkeys(order, 0) for key in ('hu', 'kong', 'horse', 'piao', 'draw')}
        components['kong'] = dict(state['kong_scores'])
        def pay(kind, source, target, amount):
            components[kind][source] -= amount
            components[kind][target] += amount
        if result.get('win_type') == 'resignation_liability':
            for p in order:
                if p != result['resigned_player_id']:
                    pay('hu', result['resigned_player_id'], p, 1)
        elif result.get('draw'):
            for p in state['revealed']:
                for q in order:
                    if q not in state['revealed']:
                        pay('draw', p, q, 1)
        else:
            options = self.saved_options(state)
            for win in result.get('winner_results', [result]):
                winner = win['winner_player_id']
                self_draw = win['win_type'] == 'self_draw'
                payers = [p for p in order if p != winner] if self_draw else [win['source_player_id']]
                shares = 2 if not self_draw and options['liang_liability'] and winner in state['revealed'] else 1
                horse = sum(10 if t['code'][0] == 'J' else int(t['code'][1]) for t in state.get('horse_tiles', [])) if self_draw else 0
                for p in payers:
                    pay('hu', p, winner, win['multiplier'] * shares)
                    pay('piao', p, winner, options['piao'] * shares)
                    pay('horse', p, winner, horse)
        return components

    def score_units(self, state, result):
        if 'score_units' in result:
            return dict(result['score_units'])
        components = self._score_breakdown(state, result)
        return {p: sum(scores[p] for scores in components.values()) for p in state['participant_order']}

    def _complete_hand(self, applied):
        state, result = applied.state, applied.result
        result['terminal_hands'] = {p: [self._public_tile(t) for t in h] for p, h in state['hands'].items()}
        state['game_result'] = deepcopy(result)

    def settlement_deltas(self, state, result, participants, stake):
        if {p['player_id'] for p in participants} != set(state['participant_order']):
            raise ValueError('卡五星结算席位不一致')
        return {p: v * stake for p, v in self.score_units(state, result).items()}

    def apply_resignation(self, state, resigned_player_id, participants):
        # A recorded win cannot be replaced by the cheaper ordinary forfeit.
        # Framework calls this within its transaction, so rejecting also rolls
        # back the tentative inactive/left participant flag.
        if (state.get('response_window') or {}).get('accepted_wins'):
            raise ValueError('已有玩家确认胡牌，请等待本次胡/过响应完成后再认输或离开')
        result = self.result_for_resignation(state, resigned_player_id, participants)
        state['game_result'] = result
        state['phase'] = state['flow']['phase'] = 'finished'
        state['turn_player_id'] = None
        state['response_window'] = None

    def result_for_archive(self, state):
        """Keep accepted wins and earned kongs when a pending double win expires."""
        order = state['participant_order']
        window = state.get('response_window') or {}
        accepted = window.get('accepted_wins', [])
        result = dict(draw=True, reason='stale_archive', win_type='interrupted_hand')
        components = {k: dict.fromkeys(order, 0) for k in ('hu', 'kong', 'horse', 'piao', 'draw')}
        components['kong'] = dict(state['kong_scores'])
        if accepted:
            wins = [self._winner_record(item['player_id'], item['action'], window['source_player_id']) for item in accepted]
            result.update(wins[0], draw=False, winner_results=wins,
                          winner_player_ids=[w['winner_player_id'] for w in wins])
            components = self._score_breakdown(state, result)
        current = {p: sum(c[p] for c in components.values()) for p in order}
        result.update(score_units=current, score_breakdown=components,
                      result_text='超时归档：保留已记杠分及已确认胡牌；未确认胡牌按过，不另收荒庄或认输分。')
        return result

    def result_for_resignation(self, state, resigned_player_id, participants):
        others = [p['player_id'] for p in participants if p['player_id'] != resigned_player_id]
        if len(others) != 2 or resigned_player_id not in state['participant_order']:
            raise ValueError('卡五星认输需三名有效参与者')
        result = dict(draw=False, reason='resignation_liability', win_type='resignation_liability',
                      resigned_player_id=resigned_player_id, winner_player_id=others[0], winning_player_ids=others,
                      result_text='认输者赔两家各1基础分，保留杠分')
        result['score_breakdown'] = self._score_breakdown(state, result)
        result['score_units'] = {p: sum(c[p] for c in result['score_breakdown'].values())
                                 for p in state['participant_order']}
        return result

    def _project_public_state(self, state, participants, *, terminal):
        public = super()._project_public_state(state, participants, terminal=terminal)
        public.update(variant=self.game_type, round_label='襄阳卡五星 · 3人',
                      revealed=deepcopy(state['revealed']), kong_scores=dict(state['kong_scores']),
                      kong_ledger=deepcopy(state['kong_ledger']), options=self.saved_options(state))
        # Read-only historical matches retain their original totals/reviews.
        if terminal and state.get('options', {}).get('rounds', 1) > 1:
            for key in ('round_number', 'round_result', 'round_results', 'cumulative_scores'):
                if key in state:
                    public[key] = deepcopy(state[key])
            public['rounds'] = state['options']['rounds']
        # A shared winning discard is referenced by both hands for replay only;
        # authoritative physical ownership remains with the source river.
        if 'terminal_hands' in public:
            result = state.get('round_result') or state.get('game_result') or {}
            for win in result.get('winner_results', []):
                tile = win['winning_tile']
                hand = public['terminal_hands'][win['winner_player_id']]
                if win['win_type'] != 'self_draw' and all(t['id'] != tile['id'] for t in hand):
                    hand.append(deepcopy(tile))
        return public

    def _public_delta(self, before_state, after_state, participants):
        delta = super()._public_delta(before_state, after_state, participants)
        public = self.public_state(after_state, participants)
        for key in ('revealed', 'kong_scores', 'kong_ledger'):
            if before_state.get(key) != after_state.get(key):
                delta[key] = deepcopy(public.get(key))
        for key in ('round_result', 'game_result'):
            if isinstance(delta.get(key), dict):
                delta[key].pop('terminal_hands', None)
                delta[key].pop('round_results', None)
        return delta

    def mcp_snapshot_state(self, public_state, viewer, participants):
        snapshot = super().mcp_snapshot_state(public_state, viewer, participants)
        # Historical ended matches: full_state restores complete reviews.
        keys = ('round_number', 'draw', 'winner_player_ids', 'score_units', 'next_dealer_player_id')
        if 'round_results' in snapshot:
            snapshot['round_results'] = [{k: deepcopy(r[k]) for k in keys if k in r}
                                         for r in snapshot['round_results']]
        for key in ('round_result', 'game_result'):
            if isinstance(snapshot.get(key), dict):
                snapshot[key].pop('terminal_hands', None)
                snapshot[key].pop('round_results', None)
        return snapshot

    mcp_bootstrap_state = mcp_snapshot_state

    def _shanten(self, state, player_id):
        hand = [t['code'] for t in state['hands'].get(player_id, [])]
        melds = state['melds'].get(player_id, [])
        if self.saved_options(state)['joker'] == 'red':
            # MCR shanten treats red dragons as ordinary tiles. Do not present
            # that misleading distance in a wildcard room.
            return None, 'joker_use_legal_actions'
        if len(hand) == 13 - 3 * len(melds):
            return hand_shanten(hand), 'current'
        if len(hand) == 14 - 3 * len(melds):
            return min(hand_shanten(hand[:i] + hand[i+1:]) for i in range(len(hand))), 'after_best_discard'
        return None, 'unavailable'

    def npc_compact_rules(self, state, actor=None, participants=None):
        o = self.saved_options(state)
        # Spell out disabled options too; never send the full base rules on
        # every NPC decision/speech request or infer settings from defaults.
        options = [
            '弃牌一炮双响' if o['double_hu'] else '弃牌单响（单和）',
            '杠上炮开（×2，计入牌型封顶）' if o['gang_discard_double'] else '杠上炮关（不额外翻倍）',
            '亮倒包赔开' if o['liang_liability'] else '亮倒包赔关（接炮/抢杠来源付一份）',
            '连续杠分翻倍开' if o['consecutive_kong_double'] else '连续杠分翻倍关（每杠按普通杠分）',
            f"买马{o['horse_count']}张", f"固定漂{o['piao']}",
            '红中万能' if o['joker'] == 'red' else '红中普通字牌',
        ]
        return (
            '三人84张卡五星，单手结束，不能吃，只选服务端legal_actions；平胡自摸可胡，接炮至少×2。'
            '【本房】' + '；'.join(options) + '。抢补杠始终单和。'
            + self._option_rule_details(o)
            + '牌型封顶×8（双龙及以上七对×16），之后赢家亮倒再×2。'
            '亮倒须打后听牌，锁听且只能胡或打新摸牌、禁碰杠；荒庄每位亮倒者向每位未亮倒者付1。'
            '自摸两家各付，接炮/抢杠来源付；直杠来源付2，补杠两家各1，暗杠两家各2，荒庄保留杠分。'
        )

    def choose_local_npc_action(self, state, actor, participants):
        actions = self.legal_actions_for(state, str(actor['player_id']))
        if not actions:
            return None
        def rank(a):
            kind = a['kind']
            if kind == 'hu': return (0, 0)
            if a.get('declare_waits'): return (1, -len(a['declare_waits']))
            if kind.endswith('gang'): return (2, 0)
            if kind == 'peng': return (3, 0)
            if kind == 'discard':
                hand = Counter(t['code'] for t in state['hands'][str(actor['player_id'])])
                t = next(t['code'] for t in state['hands'][str(actor['player_id'])] if t['id'] == a['tile_id'])
                # Only own tiles: preserve pairs and nearby suited runs.
                affinity = (hand[t]-1)*3
                if t[0] in 'BT':
                    affinity += sum(hand[f'{t[0]}{int(t[1])+d}'] for d in (-2,-1,1,2))
                remaining = [tile['code'] for tile in state['hands'][str(actor['player_id'])] if tile['id'] != a['tile_id']]
                if self.saved_options(state)['joker'] == 'red':
                    # Preserve wildcards; availability remains authoritative
                    # even when this deliberately simple heuristic is imperfect.
                    return (4, 100 if t == 'J1' else 0, affinity)
                return (4, hand_shanten(remaining), affinity)
            return (5, 0)
        best = min(actions, key=rank)
        return {'action': 'act', 'action_id': best['action_id']}
