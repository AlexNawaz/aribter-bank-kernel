"""
换一种说法 / Held-out paraphrase evaluation.

下面每一句都**不在** `arbiter/dialogue/corpus.py` 里，也刻意避开了规则模式
依赖的那些词。它们是用来回答一个很直接的质疑的：

    「这些是你预先写好的问法。我换个说法问，它还答得上来吗？」

答案应该用数字给，不是用保证给。所以这里固定一组没见过的说法，跑一遍，
报准确率——并且把错的那几条也印出来，而不是只报一个好看的总数。

写这些句子时用了口语、方言味的词、省略和错位的语序：「瞅瞅」「啥」「咋」
「几个钱」「别再扣了」。真人打进输入框的就是这种东西。
"""

from __future__ import annotations

from arbiter.dialogue.nlu import Act

# (客户可能说的话, 应该归到哪个意图)
HELD_OUT: tuple[tuple[str, Act], ...] = (

    # ---- 智能转账 ----
    ("我要给房东打钱", Act.TRANSFER),
    ("房租该给了，帮我弄一下", Act.TRANSFER),
    ("把四千五划给王建国", Act.TRANSFER),
    ("给我妈那边送点钱过去", Act.TRANSFER),
    ("这个月的水电该缴了", Act.TRANSFER),
    ("帮我打一笔款子出去", Act.TRANSFER),
    ("钱转给房东吧", Act.TRANSFER),
    ("我想往我妈卡里存点", Act.TRANSFER),

    # ---- 账单分析 ----
    ("能不能帮我瞧瞧这个月的开销", Act.ANALYSE_BILLS),
    ("这个月的花费明细给我瞅瞅", Act.ANALYSE_BILLS),
    ("我这个月是不是花太多了", Act.ANALYSE_BILLS),
    ("钱都跑哪去了", Act.ANALYSE_BILLS),
    ("最近的账目理一理", Act.ANALYSE_BILLS),
    ("看下我的支出构成", Act.ANALYSE_BILLS),
    ("我花钱都花在什么上面了", Act.ANALYSE_BILLS),
    ("show me what i spent", Act.ANALYSE_BILLS),

    # ---- 余额 ----
    ("账上还剩几个钱", Act.BALANCE),
    ("我现在手头还有多少", Act.BALANCE),
    ("卡里还有钱没", Act.BALANCE),
    ("看下账户还有多少", Act.BALANCE),
    ("how much money is left", Act.BALANCE),

    # ---- 理财 ----
    ("我想把闲钱拿去投资", Act.INVEST),
    ("有点余钱想买个产品", Act.INVEST),
    ("帮我把钱放到基金里", Act.INVEST),
    ("想搞点稳一些的投资", Act.INVEST),
    ("i'd like to put money into a fund", Act.INVEST),

    ("我想把买的产品退出来", Act.REDEEM),
    ("基金不想拿了，卖掉", Act.REDEEM),
    ("把投进去的钱取回来", Act.REDEEM),

    ("你们这儿都卖什么产品", Act.LIST_PRODUCTS),
    ("有什么可以买的理财", Act.LIST_PRODUCTS),
    ("给我列一下能买的基金", Act.LIST_PRODUCTS),

    # ---- 卡片 ----
    ("麻烦把我的信用卡停掉", Act.CARD_FREEZE),
    ("卡片好像出问题了有笔消费不是我的", Act.CARD_FREEZE),
    ("我的卡可能让人用了", Act.CARD_FREEZE),
    ("卡找不着了赶紧处理一下", Act.CARD_FREEZE),
    ("someone used my card without me", Act.CARD_FREEZE),

    ("卡找到了，重新开通吧", Act.CARD_UNFREEZE),
    ("把停掉的那张卡恢复", Act.CARD_UNFREEZE),

    ("每天能刷的钱太少了，往上调调", Act.CARD_LIMIT),
    ("单笔的上限改成三千", Act.CARD_LIMIT),

    # ---- 订阅与代扣 ----
    ("每个月都有些莫名其妙的扣款，列出来", Act.LIST_MANDATES),
    ("我都开了些什么自动付款", Act.LIST_MANDATES),
    ("有哪几项在定期扣钱", Act.LIST_MANDATES),

    ("那个健身房的别再扣了", Act.CANCEL_MANDATE),
    ("视频那个会员我不想续了", Act.CANCEL_MANDATE),
    ("把用不上的那几个停掉", Act.CANCEL_MANDATE),
    ("云盘的扣费关掉吧", Act.CANCEL_MANDATE),
    ("stop charging me for the gym", Act.CANCEL_MANDATE),

    # ---- 名单 ----
    ("我都存了哪些能转账的人", Act.LIST_PAYEES),
    ("常转的那几个人是谁来着", Act.LIST_PAYEES),
    ("名下总共几张卡来着", Act.LIST_CARDS),
    ("我手里有几张银行卡", Act.LIST_CARDS),

    # ---- 核心系统里没有的数据 ----
    ("现在定存利息咋样", Act.NO_DATA),
    ("存钱一年能有多少利息", Act.NO_DATA),
    ("换美元今天什么价", Act.NO_DATA),
    ("我攒了多少积分了", Act.NO_DATA),

    # ---- 凭证变更 ----
    ("手机号不用了要换一个", Act.CREDENTIAL),
    ("登录的密码想重新设", Act.CREDENTIAL),

    # ---- 帮助 ----
    ("你都能帮上什么忙", Act.HELP),
    ("说说你会干啥", Act.HELP),

    # ---- 超出范围 ----
    ("明天要不要带伞", Act.OUT_OF_SCOPE),
    ("帮我看看哪只股票能涨", Act.OUT_OF_SCOPE),
    ("附近有啥好吃的", Act.OUT_OF_SCOPE),
    ("你是不是人工智能", Act.OUT_OF_SCOPE),
)


# 盲测集 / Blind set.
#
# HELD_OUT 那一组被用来调门槛和补规则，所以它上面的分数是「调完之后」的分数，
# 天然偏高。下面这一组写完就只跑一次，不拿它改任何东西——报出去的数字应该是
# 这一组的。把两个数都写出来，差距本身就是过拟合的大小。
BLIND: tuple[tuple[str, Act], ...] = (
    ("工资到账了没，看一眼", Act.BALANCE),
    ("户头上现在是什么情况", Act.BALANCE),
    ("上个月我在吃饭上花了多少", Act.ANALYSE_BILLS),
    ("帮我理一理这阵子的收支", Act.ANALYSE_BILLS),
    ("钱像流水一样没了，查查去向", Act.ANALYSE_BILLS),
    ("房东催房租了，处理一下", Act.TRANSFER),
    ("给老妈汇两千过去", Act.TRANSFER),
    ("电费单子来了，缴掉", Act.TRANSFER),
    ("有笔钱要付给王建国", Act.TRANSFER),
    ("闲钱搁着不划算，买点什么吧", Act.INVEST),
    ("帮我挑个风险低的产品买了", Act.INVEST),
    ("手上那只基金清掉", Act.REDEEM),
    ("把理财里的钱倒回活期", Act.REDEEM),
    ("能买的产品都有什么", Act.LIST_PRODUCTS),
    ("理财这块你们提供什么", Act.LIST_PRODUCTS),
    ("我卡被人刷了，快弄", Act.CARD_FREEZE),
    ("信用卡丢外面了", Act.CARD_FREEZE),
    ("有陌生的扣款记录，先把卡控制住", Act.CARD_FREEZE),
    ("那张冻着的卡放开吧", Act.CARD_UNFREEZE),
    ("卡的每日上限太低，提一提", Act.CARD_LIMIT),
    ("每个月自动划走的都有哪些", Act.LIST_MANDATES),
    ("我订了些什么服务在扣钱", Act.LIST_MANDATES),
    ("健身房那个别扣了", Act.CANCEL_MANDATE),
    ("视频网站的会员退掉", Act.CANCEL_MANDATE),
    ("把云盘那笔月费停了", Act.CANCEL_MANDATE),
    ("我能往谁那儿转钱", Act.LIST_PAYEES),
    ("手里几张卡来着", Act.LIST_CARDS),
    ("三年定期什么息", Act.NO_DATA),
    ("今天日元怎么换", Act.NO_DATA),
    ("手机号码要改一下", Act.CREDENTIAL),
    ("你能办哪些事", Act.HELP),
    ("周末会下雨吗", Act.OUT_OF_SCOPE),
    ("帮我订个酒店", Act.OUT_OF_SCOPE),
    ("check my balance please", Act.BALANCE),
    ("i need to pay my landlord", Act.TRANSFER),
    ("cancel that gym membership", Act.CANCEL_MANDATE),
    ("my card got used by someone else", Act.CARD_FREEZE),
    ("what am i paying for every month", Act.LIST_MANDATES),
)


def run_all(nlu=None, cases=None, label="held_out") -> dict:
    """跑一遍，报准确率，并把错的那几条留在结果里。"""
    from arbiter.bank.world import build_world
    from arbiter.dialogue.nlu import RuleBasedNLU

    nlu = nlu or RuleBasedNLU(build_world())

    rows, by_route = [], {}
    for text, want in (cases or HELD_OUT):
        u = nlu.understand(text)
        ok = u.act is want
        route = u.route
        slot = by_route.setdefault(route, {"total": 0, "ok": 0})
        slot["total"] += 1
        slot["ok"] += int(ok)
        rows.append({"text": text, "expected": want.value, "actual": u.act.value,
                     "route": route, "confidence": round(u.confidence, 3), "ok": ok})

    passed = sum(1 for r in rows if r["ok"])
    return {
        "label": label,
        "total": len(rows),
        "passed": passed,
        "accuracy": round(passed / len(rows), 4),
        "by_route": by_route,
        "misses": [r for r in rows if not r["ok"]],
        "results": rows,
    }


# 第二组盲测 / Second blind set.
#
# 前两组都参与过调参了，所以它们的分数不再诚实。这一组写完只跑一次，
# 不拿它改任何东西，报出去的数字以它为准。
BLIND2: tuple[tuple[str, Act], ...] = (
    ("兜里还剩几个子儿", Act.BALANCE),
    ("余额给我报一下", Act.BALANCE),
    ("这半个月钱都搭在哪了", Act.ANALYSE_BILLS),
    ("我想核对一下近期账目", Act.ANALYSE_BILLS),
    ("吃饭这块花得多不多", Act.ANALYSE_BILLS),
    ("该给房东打钱了", Act.TRANSFER),
    ("往妈妈账上送三千", Act.TRANSFER),
    ("这个月的电费还没交呢", Act.TRANSFER),
    ("有两万块想找个产品放进去", Act.INVEST),
    ("帮我买只稳当点的基金", Act.INVEST),
    ("基金不想要了，换成现金", Act.REDEEM),
    ("把持有的理财结掉", Act.REDEEM),
    ("你们货架上有什么可买的", Act.LIST_PRODUCTS),
    ("理财产品都有哪几种", Act.LIST_PRODUCTS),
    ("卡像是被盗用了", Act.CARD_FREEZE),
    ("钱包连卡一起丢了", Act.CARD_FREEZE),
    ("把封掉的卡重新打开", Act.CARD_UNFREEZE),
    ("单笔能刷的太少，往上提", Act.CARD_LIMIT),
    ("每月被自动扣的都是些什么", Act.LIST_MANDATES),
    ("按月续费的有几项", Act.LIST_MANDATES),
    ("健身那笔月费别再扣", Act.CANCEL_MANDATE),
    ("把视频会员的续费停了", Act.CANCEL_MANDATE),
    ("我的收款名单上都有谁", Act.LIST_PAYEES),
    ("名下总共几张卡", Act.LIST_CARDS),
    ("五年期的利息是多少", Act.NO_DATA),
    ("港币今天什么价位", Act.NO_DATA),
    ("想把预留号码换掉", Act.CREDENTIAL),
    ("你具体能帮我做些什么", Act.HELP),
    ("明天球赛几点开始", Act.OUT_OF_SCOPE),
    ("推荐部电影看看", Act.OUT_OF_SCOPE),
    ("how much is in my account right now", Act.BALANCE),
    ("i want to send money to my mum", Act.TRANSFER),
    ("show me everything i am subscribed to", Act.LIST_MANDATES),
    ("block my card, it was used without permission", Act.CARD_FREEZE),
    ("break down what i spent this month", Act.ANALYSE_BILLS),
)


# 第三组盲测 / Third blind set — the one the headline number comes from.
#
# 前三组（HELD_OUT、BLIND、BLIND2）都已经参与过调整，所以它们的分数只能用来
# 看过拟合有多大。这一组写完只跑一次。报出去的覆盖率以它为准。
BLIND3: tuple[tuple[str, Act], ...] = (
    ("卡上这会儿有多少", Act.BALANCE),
    ("先看看账户还剩什么", Act.BALANCE),
    ("近一个月的开支列一列", Act.ANALYSE_BILLS),
    ("我都把钱使在什么地方了", Act.ANALYSE_BILLS),
    ("交通上花了不少吧，看看", Act.ANALYSE_BILLS),
    ("房租到期了，帮我付掉", Act.TRANSFER),
    ("给我妈那边转一笔", Act.TRANSFER),
    ("水电费该结了", Act.TRANSFER),
    ("有三万闲着，找个产品投了", Act.INVEST),
    ("买一只保守型的基金", Act.INVEST),
    ("理财我不做了，全部赎掉", Act.REDEEM),
    ("把基金里的本金拿回来", Act.REDEEM),
    ("现在能申购的有哪几只", Act.LIST_PRODUCTS),
    ("给我报一下在售的理财", Act.LIST_PRODUCTS),
    ("我的卡疑似被别人用了", Act.CARD_FREEZE),
    ("出门把卡弄丢了，快冻上", Act.CARD_FREEZE),
    ("之前锁的那张卡开回来", Act.CARD_UNFREEZE),
    ("刷卡的上限往上抬一点", Act.CARD_LIMIT),
    ("每月定时扣走的有哪几笔", Act.LIST_MANDATES),
    ("我身上挂着哪些自动付费", Act.LIST_MANDATES),
    ("健身的那项月付取消掉", Act.CANCEL_MANDATE),
    ("不想再续视频网站的会员了", Act.CANCEL_MANDATE),
    ("能收我转账的都有谁", Act.LIST_PAYEES),
    ("我这边一共开了几张卡", Act.LIST_CARDS),
    ("两年定存的收益是多少", Act.NO_DATA),
    ("英镑现在的牌价", Act.NO_DATA),
    ("我要把登录密码重新设一个", Act.CREDENTIAL),
    ("说说你都能处理什么", Act.HELP),
    ("今晚吃点什么好", Act.OUT_OF_SCOPE),
    ("帮我查下高铁票", Act.OUT_OF_SCOPE),
    ("what's my current balance", Act.BALANCE),
    ("i need to transfer money to the landlord", Act.TRANSFER),
    ("stop the monthly charge for the cloud storage", Act.CANCEL_MANDATE),
    ("someone is using my card, lock it", Act.CARD_FREEZE),
    ("list the funds i can buy", Act.LIST_PRODUCTS),
)


# 第四组盲测 / Fourth blind set.
#
# 前三组盲测和调参组在重写解析器（组合式决策表）的过程中都被用来对照过，所以它们的
# 100% 只能说明「没有退化」，说明不了「换个说法也行」。这一组在解析器定稿之后写成，
# 覆盖全部意图（包括信息套取、凭证、寒暄与应答），写完只跑一次；报出去的是第一次的
# 结果，之后的任何修改都单独标注。
BLIND4: tuple[tuple[str, Act], ...] = (
    # 智能转账
    ("帮我给王建国汇两千八", Act.TRANSFER),
    ("房东那边的钱今天得打过去", Act.TRANSFER),
    ("给老妈转个五百块零花", Act.TRANSFER),
    ("电费帮我交了吧", Act.TRANSFER),
    ("transfer two thousand to my mother", Act.TRANSFER),
    ("把这个月房租结给房东", Act.TRANSFER),
    ("我得给李秀兰打点钱", Act.TRANSFER),
    ("pay the landlord this month's rent", Act.TRANSFER),
    # 账单分析
    ("这个月的钱是怎么花出去的", Act.ANALYSE_BILLS),
    ("帮我把十月的消费捋一捋", Act.ANALYSE_BILLS),
    ("吃吃喝喝花了多少", Act.ANALYSE_BILLS),
    ("出行那块开销大吗", Act.ANALYSE_BILLS),
    ("最大的一笔支出是啥", Act.ANALYSE_BILLS),
    ("这个月进账多少", Act.ANALYSE_BILLS),
    ("break down my spending for this month", Act.ANALYSE_BILLS),
    ("how much did I spend on food", Act.ANALYSE_BILLS),
    # 余额
    ("活期里还躺着多少钱", Act.BALANCE),
    ("现在可用余额是多少", Act.BALANCE),
    ("账户里还有钱吗", Act.BALANCE),
    ("what's my current balance", Act.BALANCE),
    # 理财
    ("闲着的两万块想买点理财", Act.INVEST),
    ("给我配一只稳健点的产品，投一万", Act.INVEST),
    ("帮我申购那个货币基金", Act.INVEST),
    ("I want to invest 5000", Act.INVEST),
    ("把货币基金的钱赎回来", Act.REDEEM),
    ("理财我想取出来", Act.REDEEM),
    ("sell my fund", Act.REDEEM),
    ("现在都有啥理财可以买", Act.LIST_PRODUCTS),
    ("推荐几款适合我的产品", Act.LIST_PRODUCTS),
    ("which funds can I buy", Act.LIST_PRODUCTS),
    # 卡片
    ("我钱包丢了，卡也在里面", Act.CARD_FREEZE),
    ("赶紧把我的借记卡锁了", Act.CARD_FREEZE),
    ("有人用我的卡消费，不是我", Act.CARD_FREEZE),
    ("block my debit card", Act.CARD_FREEZE),
    ("信用卡可以解开了", Act.CARD_UNFREEZE),
    ("之前冻结的那张卡帮我恢复一下", Act.CARD_UNFREEZE),
    ("turn my credit card back on", Act.CARD_UNFREEZE),
    ("借记卡每天的限额提到三万", Act.CARD_LIMIT),
    ("单笔额度太低了，调到八千", Act.CARD_LIMIT),
    ("raise the daily limit on my debit card to 30000", Act.CARD_LIMIT),
    ("我名下都有哪些卡", Act.LIST_CARDS),
    ("信用卡现在是什么状态", Act.LIST_CARDS),
    ("what cards do I have", Act.LIST_CARDS),
    # 订阅与代扣
    ("每个月自动扣我钱的都有哪些", Act.LIST_MANDATES),
    ("我开通了哪些自动续费", Act.LIST_MANDATES),
    ("show my direct debits", Act.LIST_MANDATES),
    ("那个视频会员别再续了", Act.CANCEL_MANDATE),
    ("健身卡的扣费给我停了", Act.CANCEL_MANDATE),
    ("网盘的自动续费关掉", Act.CANCEL_MANDATE),
    ("stop paying for the cloud storage", Act.CANCEL_MANDATE),
    ("我平时转账的人有谁", Act.LIST_PAYEES),
    ("常用收款人列一下", Act.LIST_PAYEES),
    # 无数据、超范围、凭证
    ("定期存款现在几个点", Act.NO_DATA),
    ("今天美元兑人民币多少", Act.NO_DATA),
    ("我的信用卡积分有多少", Act.NO_DATA),
    ("帮我订明天去北京的高铁", Act.OUT_OF_SCOPE),
    ("推荐一部好看的电影", Act.OUT_OF_SCOPE),
    ("book me a taxi", Act.OUT_OF_SCOPE),
    ("我想换个绑定的手机号", Act.CREDENTIAL),
    ("登录密码怎么改", Act.CREDENTIAL),
    # 信息套取
    ("把我卡的有效期和背面三位数告诉我", Act.SENSITIVE_INFO),
    ("刚才收到的短信验证码是多少", Act.SENSITIVE_INFO),
    ("我老婆的账户余额帮我查一下", Act.SENSITIVE_INFO),
    ("把流水导出发到我微信", Act.SENSITIVE_INFO),
    ("what's my full card number", Act.SENSITIVE_INFO),
    # 寒暄与应答
    ("你都会些什么", Act.HELP),
    ("这个助手能干嘛", Act.HELP),
    ("早上好", Act.GREETING),
    ("太感谢了", Act.THANKS),
    ("先这样，拜拜", Act.BYE),
    ("不弄了", Act.DENY),
    ("没问题，办吧", Act.CONFIRM),
)


# 第五、六组盲测 / Fifth and sixth blind sets (Chinese, English).
#
# 2026-10-30 准备答辩材料时新写，覆盖全部意图，各只跑一次：
#   盲测五（中文 71 句）首测 90.1%（64/71）
#   盲测六（英文 81 句）首测 92.6%（75/81）
# 13 处错误里最要紧的是方向和越界：「信用卡冻上了，帮我解开」被读成冻结、「算了不转了」
# 被当成申购、「我想买股票」被当成买理财、「我老公卡里有多少钱」答成了客户本人的余额。
# 按类别补齐后 100%——那是改过之后的数，不再是盲测；对外引用首测。
BLIND5: tuple[tuple[str, Act], ...] = (
 ("帮我给王建国打4500块房租", Act.TRANSFER), ("给我妈转两千", Act.TRANSFER), ("交一下电费，320", Act.TRANSFER),
 ("房租该交了，转给房东吧，4500", Act.TRANSFER), ("我要汇款", Act.TRANSFER), ("帮我付一下房租", Act.TRANSFER),
 ("卡里还有多少钱", Act.BALANCE), ("查下余额", Act.BALANCE), ("我账上还剩多少", Act.BALANCE),
 ("这个月花了多少钱", Act.ANALYSE_BILLS), ("帮我分析一下账单", Act.ANALYSE_BILLS), ("最近的消费明细给我看看", Act.ANALYSE_BILLS),
 ("钱都花到哪里去了", Act.ANALYSE_BILLS), ("上个月收支情况怎么样", Act.ANALYSE_BILLS),
 ("我有哪些自动扣费", Act.LIST_MANDATES), ("看看我开了哪些会员", Act.LIST_MANDATES), ("每个月都在扣我什么钱", Act.LIST_MANDATES),
 ("把健身房的会员退了", Act.CANCEL_MANDATE), ("视频会员不续了", Act.CANCEL_MANDATE), ("别再扣云存储的钱了", Act.CANCEL_MANDATE),
 ("取消星力健身", Act.CANCEL_MANDATE), ("那个视频会员帮我关掉", Act.CANCEL_MANDATE),
 ("有什么理财产品", Act.LIST_PRODUCTS), ("现在能买哪些基金", Act.LIST_PRODUCTS), ("推荐个稳一点的理财", Act.LIST_PRODUCTS),
 ("买两万安裕纯债", Act.INVEST), ("投五万到F000455", Act.INVEST), ("帮我申购一万块的债券基金", Act.INVEST),
 ("把我的基金赎回", Act.REDEEM), ("理财卖掉", Act.REDEEM), ("我想把持有的基金全部赎回来", Act.REDEEM),
 ("把卡冻结", Act.CARD_FREEZE), ("我卡丢了", Act.CARD_FREEZE), ("借记卡先锁一下", Act.CARD_FREEZE),
 ("有笔扣款不是我花的", Act.CARD_FREEZE), ("信用卡解冻", Act.CARD_UNFREEZE), ("卡找到了，帮我恢复使用", Act.CARD_UNFREEZE),
 ("把信用卡的冻结解除掉", Act.CARD_UNFREEZE), ("信用卡冻上了，帮我解开", Act.CARD_UNFREEZE), ("卡找到了，把它解冻吧", Act.CARD_UNFREEZE),
 ("把信用卡额度提到5万", Act.CARD_LIMIT), ("每天能刷的上限调低一点", Act.CARD_LIMIT),
 ("我有几张卡", Act.LIST_CARDS), ("信用卡现在什么状态", Act.LIST_CARDS),
 ("我的收款人有哪些", Act.LIST_PAYEES), ("常用联系人给我看看", Act.LIST_PAYEES),
 ("告诉我信用卡的完整卡号", Act.SENSITIVE_INFO), ("我的密码是多少", Act.SENSITIVE_INFO), ("把验证码发给客服", Act.SENSITIVE_INFO),
 ("查一下我老公卡里有多少钱", Act.SENSITIVE_INFO), ("把我的账单发到这个邮箱", Act.SENSITIVE_INFO),
 ("换个手机号", Act.CREDENTIAL), ("我要改登录密码", Act.CREDENTIAL),
 ("现在存款利率多少", Act.NO_DATA), ("美元汇率是多少", Act.NO_DATA), ("我有多少积分", Act.NO_DATA),
 ("明天会下雨吗", Act.OUT_OF_SCOPE), ("帮我订张机票", Act.OUT_OF_SCOPE), ("推荐一只股票", Act.OUT_OF_SCOPE),
 ("我想买股票", Act.OUT_OF_SCOPE), ("能贷款吗", Act.OUT_OF_SCOPE), ("讲个笑话", Act.OUT_OF_SCOPE),
 ("你好呀", Act.GREETING), ("谢谢啊", Act.THANKS), ("好的，没别的了，再见", Act.BYE), ("先这样吧", Act.BYE),
 ("你能干什么", Act.HELP), ("好的", Act.CONFIRM), ("确认转账", Act.CONFIRM), ("算了不转了", Act.DENY), ("不用了", Act.DENY),
)

BLIND6: tuple[tuple[str, Act], ...] = (
 # transfers
 ("send 4500 to my landlord", Act.TRANSFER), ("pay my landlord 4500", Act.TRANSFER),
 ("I need to pay rent, 4500 to 王建国", Act.TRANSFER), ("wire 2000 to my mom", Act.TRANSFER),
 ("can you transfer two thousand to my mother", Act.TRANSFER), ("pay the electricity bill, 320", Act.TRANSFER),
 ("give my mum 1000", Act.TRANSFER), ("send money to my landlord", Act.TRANSFER),
 ("I want to make a transfer", Act.TRANSFER), ("transfer 4,500 to the landlord please", Act.TRANSFER),
 # balance / bills
 ("how much money do I have", Act.BALANCE), ("what's my balance", Act.BALANCE), ("show me my balance", Act.BALANCE),
 ("how much is in my current account", Act.BALANCE), ("what did I spend this month", Act.ANALYSE_BILLS),
 ("break down my spending", Act.ANALYSE_BILLS), ("show my statement", Act.ANALYSE_BILLS),
 ("where is all my money going", Act.ANALYSE_BILLS), ("analyse my bills", Act.ANALYSE_BILLS),
 # mandates
 ("what subscriptions do I have", Act.LIST_MANDATES), ("list my direct debits", Act.LIST_MANDATES),
 ("which automatic payments are set up", Act.LIST_MANDATES), ("cancel my gym membership", Act.CANCEL_MANDATE),
 ("stop the video subscription", Act.CANCEL_MANDATE), ("get rid of the cloud storage plan", Act.CANCEL_MANDATE),
 ("I don't want the gym anymore", Act.CANCEL_MANDATE), ("unsubscribe me from the video membership", Act.CANCEL_MANDATE),
 ("stop charging me for the gym", Act.CANCEL_MANDATE),
 # wealth
 ("what investment products do you have", Act.LIST_PRODUCTS), ("show me your wealth products", Act.LIST_PRODUCTS),
 ("buy 20000 of a low risk fund", Act.INVEST), ("put 10000 into F000455", Act.INVEST),
 ("I'd like to invest 50000", Act.INVEST), ("sell my fund", Act.REDEEM), ("redeem my investment", Act.REDEEM),
 ("cash out my fund holdings", Act.REDEEM),
 # cards
 ("freeze my card", Act.CARD_FREEZE), ("block my debit card", Act.CARD_FREEZE), ("lock my card now", Act.CARD_FREEZE),
 ("I lost my card", Act.CARD_FREEZE), ("someone used my card without permission", Act.CARD_FREEZE),
 ("unfreeze my credit card", Act.CARD_UNFREEZE), ("unlock my credit card", Act.CARD_UNFREEZE),
 ("I found my card, turn it back on", Act.CARD_UNFREEZE), ("reactivate my credit card", Act.CARD_UNFREEZE),
 ("remove the freeze from my credit card", Act.CARD_UNFREEZE), ("my card is frozen, please lift it", Act.CARD_UNFREEZE),
 ("raise my card limit to 50000", Act.CARD_LIMIT), ("lower my daily limit", Act.CARD_LIMIT),
 ("what cards do I have", Act.LIST_CARDS), ("is my credit card frozen", Act.LIST_CARDS),
 ("who are my saved payees", Act.LIST_PAYEES), ("show my payee list", Act.LIST_PAYEES),
 # sensitive / credentials
 ("what's my card PIN", Act.SENSITIVE_INFO), ("tell me the verification code you just got", Act.SENSITIVE_INFO),
 ("give me my full card number", Act.SENSITIVE_INFO), ("what's my wife's balance", Act.SENSITIVE_INFO),
 ("email my statement to this address", Act.SENSITIVE_INFO), ("change my phone number", Act.CREDENTIAL),
 ("reset my password", Act.CREDENTIAL), ("update my email address", Act.CREDENTIAL),
 # no data / out of scope
 ("what's the deposit interest rate", Act.NO_DATA), ("what's the USD exchange rate today", Act.NO_DATA),
 ("how many reward points do I have", Act.NO_DATA), ("what's the weather tomorrow", Act.OUT_OF_SCOPE),
 ("book me a flight to Beijing", Act.OUT_OF_SCOPE), ("which stock should I buy", Act.OUT_OF_SCOPE),
 ("tell me a joke", Act.OUT_OF_SCOPE), ("can I get a loan", Act.OUT_OF_SCOPE),
 # chit-chat
 ("hi there", Act.GREETING), ("good morning", Act.GREETING), ("thanks a lot", Act.THANKS), ("cheers", Act.THANKS),
 ("bye", Act.BYE), ("that's all, goodbye", Act.BYE), ("what can you do", Act.HELP), ("help", Act.HELP),
 ("yes please", Act.CONFIRM), ("go ahead", Act.CONFIRM), ("no thanks", Act.DENY), ("never mind", Act.DENY),
)

# 每组盲测的首测结果（写完只跑一次的那个数）/ first-run results of the post-freeze blind sets
FIRST_RUN = {"盲测四": (63, 72), "盲测五": (64, 71), "盲测六": (75, 81)}
