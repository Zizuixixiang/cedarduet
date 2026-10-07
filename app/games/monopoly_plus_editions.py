"""大富翁·改：版本配置。

每个版本是一份纯数据：地图覆盖（哪些格子改名/改用途）、牌堆、规则开关和数值。
引擎只读这份配置，因此以后加新版本 = 在 EDITIONS 里加一项（换地图、换卡池、
关掉某个机制），不必改引擎分支。``available=False`` 的版本只在开局选择页
灰显“敬请期待”，服务端拒绝选择它们。
"""
from __future__ import annotations

from copy import deepcopy

# 经典机会/公益牌原文与数值沿用原版大富翁（app/games/monopoly.py），索引 0 必须是
# 出狱卡：原版引擎在使用/破产时按 ``append(0)`` 把出狱卡放回牌堆。
from .monopoly import _CARDS as _CLASSIC

ITEM_LABELS = {'swap': '强行交易', 'acquire': '收购', 'nope': '不行'}
ITEM_HELP = {
    'swap': '自己回合掷骰前或落地后使用：用你的一块地强制换对手的一块地。成套的、盖了房的、抵押中的地都不能选。',
    'acquire': '本回合踩到对手地产并付完租后使用：按标价 2 倍强制买下这块地。对方那块必须无建筑、不在对方的成套组里。',
    'nope': '被强行交易、被收购、或抽到针对你的罚款事件时打出，抵消这一次。',
}

# (kind, 文案, 数值)。item 卡只能抽到；抽到后收入手牌（别人只看到张数）。
CHANCE_PLUS = list(_CLASSIC['chance']) + [
    ('item', '道具卡', 'swap'),
    ('item', '道具卡', 'acquire'),
    ('item', '道具卡', 'nope'),
]
CHEST_PLUS = list(_CLASSIC['chest']) + [
    ('item', '道具卡', 'swap'),
    ('item', '道具卡', 'nope'),
    ('item', '道具卡', 'nope'),
]

# 聊天卡：mode=per_yes 每张赞成票由投票者付 amount 给你；majority 过半赞成银行付；
# any 至少一票赞成银行付。prompt 里的 {next} 替换为下家名字。
EVENTS_PLUS = [
    # —— 全桌 ——
    ('group_rent', '旺季：随机一组街区本轮租金翻倍', 200),
    ('group_rent', '淡季：随机一组街区本轮租金减半', 50),
    ('railroad_rent', '春运：所有车站本轮租金翻倍', 200),
    ('utility_off', '停电检修：两处设施本轮不收租', 0),
    ('shift_all', '集体散步：所有人（在押者除外）向前挪一格，不结算落地', 1),
    ('swap_two', '乾坤大挪移：随机两位玩家交换位置，不结算落地', 0),
    ('welfare', '济贫：现金最多的人给现金最少的人 100', 100),
    ('festival', '城市庆典：每人从银行领 20', 20),
    ('property_tax', '物业税：每人按名下地产数，每块付 10 给银行', 10),
    ('checkup', '集体体检：每人付 25 给银行', 25),
    ('pass_left', '传红包：每人给下家 30', 30),
    ('flash_auction', '快闪拍卖：随机一块无主地产立即公开拍卖', 0),
    ('jail_break', '大赦：牢里的人立即出狱，不用交钱', 0),
    ('cake', '蛋糕分享：其他每人给现金最少的人 10', 10),
    # —— 地产 ——
    ('tile_rent_up', '网红打卡：随机一块有主地产本轮租金 ×1.5', 150),
    ('tile_discount', '开发商促销：随机一块无主地产本轮售价七折', 70),
    ('road_closed', '修路：随机一块街区本轮封闭——掷骰停在那里会顺延一格，封闭期间不收租', 0),
    ('appraisal', '地产评估：你名下每块未抵押地产，银行给你 10', 10),
    ('leak', '水管漏水：你每栋房屋付 15、每座旅馆付 50', (15, 50)),
    ('agency_fee', '中介费：付你最贵那块地产标价的 10% 给银行', 10),
    # —— 个人 ——
    ('next_railroad', '顺风车：前进到下一个车站（照常结算）', 0),
    ('back', '走错路：后退 2 格', 2),
    ('forward', '晨跑：前进 3 格', 3),
    # —— 温馨 ——
    ('gift', '红包：给你最喜欢的人发一个红包（自选 50~200）', 0),
    ('milk_tea', '请客：你请全桌喝奶茶，付每位其他玩家 15', 15),
    ('wallet', '拾金不昧：把捡到的钱包交还失主，银行奖励你 30', 30),
    # —— 聊天卡（需要说话，桌上其他人一键投票，超时按不通过） ——
    ('chat', '讲故事', dict(prompt='给下家「{next}」讲一个三句话的故事', mode='per_yes', amount=50,
                          reward='每张赞成票由投票者付你 50')),
    ('chat', '夸夸卡', dict(prompt='用一句话夸场上的每一个人', mode='majority', amount=80,
                          reward='过半数赞成，银行奖励你 80')),
    ('chat', '藏头诗', dict(prompt='用“大富翁”三个字写一首藏头诗', mode='per_yes', amount=30,
                          reward='每张赞成票由投票者付你 30')),
    ('chat', '推销员', dict(prompt='用一句话向全桌推销你名下的一块地（没有地就推销你自己）', mode='majority', amount=60,
                          reward='过半数赞成，银行奖励你 60')),
    ('chat', '真心话', dict(prompt='说一件今天让你开心的小事', mode='any', amount=40,
                          reward='至少一人赞成，银行奖励你 40')),
    ('chat', '天气预报', dict(prompt='用播音腔播报一段“大富翁城”明天的天气', mode='majority', amount=50,
                            reward='过半数赞成，银行奖励你 50')),
]

# 罚款类事件：持有“不行”卡时可抵消。
FINE_KINDS = frozenset({'pay', 'repairs', 'leak', 'agency_fee'})

CLASSIC_PLUS_RULES = dict(
    start_cash=1500, salary=200, bail=50,
    jail_max_turns=2,            # 第 2 个在押回合强制交罚款出狱
    single_cell_jail=True,       # 牢里同时只关一人
    speed_die=True, bus_tickets=16, bus_expire_tickets=3,
    parking_auction=True,
    items=True, hand_limit=3,
    events=True,
    loans=True, loan_ratio_pct=50, loan_cap=1000, loan_min=50, loan_interest_pct=10, loan_laps=3,
    bets=True, bet_stake=50, bet_payout=dict(big=100, small=100, seven=200),
    naming=True, name_max=8, motto_max=30,
    chat_text_max=150,
)

EDITIONS = {
    'classic_plus': dict(
        name='经典·改', available=True,
        tagline='经典 40 格 + 速度骰、道具卡、事件卡、银行借贷、观战押注、地名牌',
        # 地图覆盖：tile_id -> (名字, kind)。其余格子与原版相同。
        tiles={17: ('事件', 'event'), 22: ('事件', 'event'), 20: ('拍卖行', 'auction_house')},
        decks=dict(chance=CHANCE_PLUS, chest=CHEST_PLUS, event=EVENTS_PLUS),
        rules=CLASSIC_PLUS_RULES,
    ),
    'metro': dict(
        name='地铁环线版', available=False,
        tagline='敬请期待：环线换乘、站点地产与换乘卡',
    ),
    'island': dict(
        name='海岛度假版', available=False,
        tagline='敬请期待：潮汐事件、度假村与渡轮',
    ),
}
DEFAULT_EDITION = 'classic_plus'


def edition(key: str) -> dict:
    return EDITIONS[key]


def edition_menu() -> list[dict]:
    """开局选择页需要的公开摘要（不含牌堆）。"""
    return [dict(id=k, name=v['name'], available=v['available'], tagline=v['tagline'])
            for k, v in EDITIONS.items()]


def edition_rules(key: str) -> dict:
    return deepcopy(EDITIONS[key]['rules'])
