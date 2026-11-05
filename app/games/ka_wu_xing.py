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
    display_name = '襄阳卡五星'
    min_players = max_players = recommended_players = 3
    allowed_player_counts = (3,)
    rules_text = (
        '【襄阳卡五星 · 固定房规 v1】三人84张：筒、条各1–9及中发白各四张，无万、风、花。'
        '庄14张先打，闲家13张；任意对子作将，四面子一对或七对（四张可作两对）。'
        '不能吃，可碰、直杠、补杠、暗杠；摸牌墙头，杠补墙尾。单手结束，不连续轮庄。\n'
        '【倍数】平胡×1、碰碰胡×2、清一色×4、七对×4、龙七对×8、双龙及以上七对×16；'
        '七对族择一，不叠碰碰胡或四归一。非七对的非杠四张：含明碰为明四归一×2，否则暗四归一×4，'
        '多组只取最高一项。大三元×8/小三元×4互斥；手抓一（四副露剩单张成对）×4。'
        '胡进5筒/5条且存在以手中4、6组成456的合法拆分才是卡五星×2；已有456胡别张不计。'
        '杠上开花、抢杠胡、海底自摸各×2；不同项相乘，牌型封顶×8，双龙及以上七对封顶×16，'
        '之后仅赢家亮倒再×2（最高×32）。未实现杠上炮、买马、漂、癞子。'
        '自摸平胡可胡；点炮须最终至少×2；抢杠×2可胡。\n'
        '【亮倒】在本人出牌时选择“亮倒并打…”：弃牌后必须听牌，公开固定听口及余手牌。'
        '此后只能胡或打刚摸的牌，禁止碰和任何杠；只公开固定手牌，不公开新摸暗牌。'
        '荒庄：每位亮倒者向每位未亮倒者付1基础分；全亮/全未亮均不赔，不另查叫、不包赔点炮。\n'
        '【响应】胡优先于碰杠，同级按距出牌者近者先响应，单和制，不支持一炮双响。'
        '过合法胡（包括自摸不胡）后，至本人下次摸牌前不能接炮或抢杠。最后一张弃牌只可胡。'
        '补杠先检查抢杠，未完成不收杠分；暗杠不可抢。\n'
        '【积分】直杠来源付2，补杠另两家各付1，暗杠另两家各付2；杠分单独记账，荒庄保留。'
        '自摸另两家各付最终倍数，接炮/抢杠仅来源付最终倍数；认输付另两家各1，保留杠分。'
        '0底注仅记录局分；非0局分乘底注，终局一次结算，真实输家最多扣当前可用钱包，'
        '实际可付总额按赢家应得比例分配、整数余数按座次，不透支；NPC只记逻辑分。'
        '仅站内娱乐积分，无真实钱。'
    )
    mcp_move_format = Mahjong.move_format + ' 亮倒使用服务端 liang: 开头 action_id；公开听口见 board_state.revealed，倍数非国标番数。'

    @staticmethod
    def build_wall():
        return [{'id': f'{t}-{i}', 'code': t} for t in CODES for i in range(1, 5)]

    @staticmethod
    def _empty_state(order):
        state = Mahjong._empty_state(order)
        state.update(rules_version='xiangyang-84-v1', revealed={}, passed_hu=[],
                     kong_scores={p: 0 for p in order}, kong_ledger=[])
        return state

    def initialize_for_first_player(self, participants, first_player_id):
        if not 1 <= len(participants) <= 3:
            raise ValueError('卡五星固定三人，等待房可先创建一至两个席位')
        return super().initialize_for_first_player(participants, first_player_id)

    def _chi_actions(self, *args):
        return []

    def _discard_response_queue(self, state, source_player_id, tile):
        return [entry for entry in super()._discard_response_queue(state, source_player_id, tile)
                if entry['priority'] == 'hu' or entry['player_id'] not in state['revealed']]

    @staticmethod
    def _draw(state, player_id, *, replacement):
        ok = Mahjong._draw(state, player_id, replacement=replacement)
        if ok and player_id in state.get('passed_hu', []):
            state['passed_hu'].remove(player_id)
        return ok

    def _fan_evaluation(self, state, player_id, win_tile, *, source_kind):
        if source_kind != 'self_draw' and player_id in state.get('passed_hu', []):
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

    def legal_actions_for(self, state, player_id):
        actions = super().legal_actions_for(state, player_id)
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
            listening = waits(codes, state['melds'][player_id])
            if listening:
                declarations.append({**a, 'action_id': f"liang:{a['tile_id']}",
                                     'declare_waits': listening, 'label': '亮倒并' + a['label'],
                                     'public_label': '亮倒并' + a['label']})
        return actions + declarations

    def _before_discard(self, state, player_id, action):
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
                amount = 1 if kind == 'added_gang' else 2
                for q in payers:
                    after['kong_scores'][q] -= amount
                    after['kong_scores'][p] += amount
                after['kong_ledger'].append(dict(player_id=p, kind=kind, payers=payers, units=amount))
        if applied.result is not None:
            applied.result['score_units'] = self.score_units(after, applied.result)
            after['game_result'] = deepcopy(applied.result)
        return applied

    def _finish_win(self, state, winner_id, action, source_player_id):
        result = super()._finish_win(state, winner_id, action, source_player_id)
        result.pop('minimum_fan', None)
        result.update(multiplier=action['multiplier'], pattern_cap=action['pattern_cap'],
                      uncapped_multiplier=action['uncapped_multiplier'])
        return result

    def score_units(self, state, result):
        order = state['participant_order']
        scores = dict(state['kong_scores'])
        def pay(source, target, amount):
            scores[source] -= amount
            scores[target] += amount
        if result.get('win_type') == 'resignation_liability':
            for p in order:
                if p != result['resigned_player_id']:
                    pay(result['resigned_player_id'], p, 1)
        elif result.get('draw'):
            for p in state['revealed']:
                for q in order:
                    if q not in state['revealed']:
                        pay(p, q, 1)
        else:
            winner = result['winner_player_id']
            payers = [p for p in order if p != winner] if result['win_type'] == 'self_draw' else [result['source_player_id']]
            for p in payers:
                pay(p, winner, result['multiplier'])
        return scores

    def settlement_deltas(self, state, result, participants, stake):
        if {p['player_id'] for p in participants} != set(state['participant_order']):
            raise ValueError('卡五星结算席位不一致')
        return {p: v * stake for p, v in self.score_units(state, result).items()}

    def result_for_resignation(self, state, resigned_player_id, participants):
        others = [p['player_id'] for p in participants if p['player_id'] != resigned_player_id]
        if len(others) != 2 or resigned_player_id not in state['participant_order']:
            raise ValueError('卡五星认输需三名有效参与者')
        result = dict(draw=False, reason='resignation_liability', win_type='resignation_liability',
                      resigned_player_id=resigned_player_id, winner_player_id=others[0], winning_player_ids=others,
                      result_text='认输者赔两家各1基础分，保留杠分')
        result['score_units'] = self.score_units(state, result)
        return result

    def _project_public_state(self, state, participants, *, terminal):
        public = super()._project_public_state(state, participants, terminal=terminal)
        public.update(variant=self.game_type, round_label='襄阳卡五星',
                      revealed=deepcopy(state['revealed']), kong_scores=dict(state['kong_scores']),
                      kong_ledger=deepcopy(state['kong_ledger']))
        return public

    def _public_delta(self, before_state, after_state, participants):
        delta = super()._public_delta(before_state, after_state, participants)
        for key in ('revealed', 'kong_scores', 'kong_ledger'):
            if before_state[key] != after_state[key]:
                delta[key] = deepcopy(after_state[key])
        return delta

    def _shanten(self, state, player_id):
        hand = [t['code'] for t in state['hands'].get(player_id, [])]
        melds = state['melds'].get(player_id, [])
        if len(hand) == 13 - 3 * len(melds):
            return hand_shanten(hand), 'current'
        if len(hand) == 14 - 3 * len(melds):
            return min(hand_shanten(hand[:i] + hand[i+1:]) for i in range(len(hand))), 'after_best_discard'
        return None, 'unavailable'

    def npc_compact_rules(self, *args):
        return '三人84张卡五星，不能吃，只选服务端legal_actions；平胡自摸可胡，接炮至少×2。'

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
                return (4, hand_shanten(remaining), affinity)
            return (5, 0)
        best = min(actions, key=rank)
        return {'action': 'act', 'action_id': best['action_id']}
