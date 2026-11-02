"""
意图理解 / Understanding what the customer said.

把客户随口说的一句话，变成一个或几个「意图 + 槽位」。

**这一层不是安全控制。** 它位于不可信侧，和规划器一样。理解错了最多是问错一个
问题或者提议一个错误的信封——而信封要由客户阅读并签署，之后内核仍然按观测到的
效果裁决。把语义理解当成安全边界，正是本项目要修复的那个错误。

**怎么理解。** 第一版是一张按顺序匹配的正则表，谁排在前面谁赢。实机试用里冒出
来的问题几乎全出在「谁排在前面」上：「取消冻结」被冻结规则抢走，「信用卡信息」
被判成冻结，「我想投资5000块」被转账规则吃掉，「取消星力健身的会员」指向了视频
会员。

这一版对每个分句分别回答四个问题：

    说的是什么东西？   卡、订阅、理财、收款人、账单、余额、利率……
    要对它做什么？     冻结、解冻、取消、买、卖、转、查、改……
    有没有否定？       「取消冻结」是解冻，「不想要了」对订阅是取消、对理财是赎回
    是问句还是祈使句？ 「信用卡信息」是查询，不是冻结

再由一张决策表把四个答案组合成意图。原先的规则表和相似度匹配都还在，但退到
了后面：只有决策表认不出来的句子才轮到它们。

一句话里可以有几件事（「查一下余额，然后给房东转4500」），按分句分别理解；
一个分句里也可以点名几样东西（「取消视频会员和健身房」）。

**默认实现不依赖任何大模型。** 评审要能在没有网络、没有 API key 的环境下复现。
`DialogueModel` 协议留了接口；接入真实大模型后，由于内核不信任这里的任何输出，
理解能力随便升级，安全性质一条都不变。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Protocol

from arbiter.dialogue import lexicon as L
from arbiter.types import RiskLevel, yuan


class Act(str, Enum):
    """对话行为 / What a customer turn (or one clause of it) is doing."""
    TRANSFER = "transfer"                 # 智能转账
    ANALYSE_BILLS = "analyse_bills"       # 账单分析
    INVEST = "invest"                     # 理财操作：申购
    REDEEM = "redeem"                     # 理财操作：赎回
    CARD_FREEZE = "card_freeze"           # 卡片管理：冻结
    CARD_UNFREEZE = "card_unfreeze"       # 卡片管理：解冻
    CARD_LIMIT = "card_limit"             # 卡片管理：额度
    LIST_MANDATES = "list_mandates"       # 订阅与代扣：查看
    CANCEL_MANDATE = "cancel_mandate"     # 订阅与代扣：取消
    BALANCE = "balance"                   # 查询余额
    LIST_PRODUCTS = "list_products"       # 理财产品目录
    LIST_PAYEES = "list_payees"           # 收款人列表
    LIST_CARDS = "list_cards"             # 卡片列表与状态
    NO_DATA = "no_data"                   # 像银行业务，但核心系统里没有这项数据
    SENSITIVE_INFO = "sensitive_info"     # 信息套取：密码、验证码、完整卡号、别人的资料、外发
    CONFIRM = "confirm"                   # 是 / 好 / 确认
    DENY = "deny"                         # 不 / 算了 / 取消
    CORRECTION = "correction"             # 不对，是…
    GREETING = "greeting"
    THANKS = "thanks"
    BYE = "bye"
    HELP = "help"
    CREDENTIAL = "credential"             # 凭证变更：像银行业务，但不在权限内
    OUT_OF_SCOPE = "out_of_scope"         # 超范围兜底
    REFERENCE = "reference"               # 只说了一个名字：卡号、商户、产品、收款人
    AMOUNT_ONLY = "amount_only"           # 只说了一个金额
    UNKNOWN = "unknown"

    @property
    def scenario(self) -> str:
        return _ACT_SCENARIO.get(self, "")


_ACT_SCENARIO = {
    Act.TRANSFER: "智能转账", Act.LIST_PAYEES: "智能转账",
    Act.ANALYSE_BILLS: "账单分析", Act.BALANCE: "账单分析",
    Act.INVEST: "理财操作", Act.REDEEM: "理财操作", Act.LIST_PRODUCTS: "理财操作",
    Act.CARD_FREEZE: "卡片管理", Act.CARD_UNFREEZE: "卡片管理", Act.CARD_LIMIT: "卡片管理",
    Act.LIST_CARDS: "卡片管理",
    Act.LIST_MANDATES: "订阅与代扣管理", Act.CANCEL_MANDATE: "订阅与代扣管理",
}

# 会动钱或动控制的意图 / intents that move money or change controls
ACTION_ACTS = frozenset({
    Act.TRANSFER, Act.INVEST, Act.REDEEM, Act.CANCEL_MANDATE,
    Act.CARD_FREEZE, Act.CARD_UNFREEZE, Act.CARD_LIMIT,
})
READ_ACTS = frozenset({Act.ANALYSE_BILLS, Act.BALANCE, Act.LIST_MANDATES})
CATALOGUE_ACTS = frozenset({Act.LIST_PRODUCTS, Act.LIST_PAYEES, Act.LIST_CARDS})
UTTERANCE_ACTS = frozenset({Act.CONFIRM, Act.DENY, Act.CORRECTION, Act.GREETING,
                            Act.THANKS, Act.BYE, Act.HELP})


# ---------------------------------------------------------------------------
# 中文数字 / Chinese numerals
# ---------------------------------------------------------------------------

_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "俩": 2, "三": 3, "四": 4,
           "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
           "壹": 1, "贰": 2, "叁": 3, "肆": 4, "伍": 5, "陆": 6, "柒": 7, "捌": 8, "玖": 9}
_UNITS = {"十": 10, "拾": 10, "百": 100, "佰": 100, "千": 1000, "仟": 1000}
_BIG = {"万": 10_000, "萬": 10_000, "亿": 100_000_000}


def parse_chinese_number(text: str) -> int | None:
    """
    解析纯中文数字，例如「两千五百」「一万五」。

    「一万五」在口语里是 15000 而不是 10005——尾随的单个数字继承上一个单位的
    下一级。不处理这种省略说法，客户说「转一万五」就会被理解成 10005 元，
    而这正是那种不会报错、只会转错钱的错误。
    """
    text = text.strip()
    if not text or any(ch not in _DIGITS and ch not in _UNITS and ch not in _BIG for ch in text):
        return None

    total = 0          # 已结算到「万」以上的部分
    section = 0        # 当前「万」以内的累计
    current = 0        # 当前数字
    last_unit = 1      # 上一个用到的单位，供「一万五」这类省略用

    for ch in text:
        if ch in _DIGITS:
            current = _DIGITS[ch]
        elif ch in _UNITS:
            unit = _UNITS[ch]
            section += (current if current else 1) * unit
            last_unit = unit
            current = 0
        elif ch in _BIG:
            big = _BIG[ch]
            section = (section + current) * big
            total += section
            section = 0
            current = 0
            last_unit = big
    if current:
        if last_unit >= 10 and section == 0 and total:
            total += current * (last_unit // 10)
        elif last_unit >= 10 and section:
            section += current * (last_unit // 10)
        else:
            section += current
    return total + section or None


_CURRENCY = r"(?:块钱|块|元|圆|人民币|RMB|rmb|yuan|cny)"
_NOT_MONEY = r"(?:月|日|号|点|年|岁|%|％|折|次|个|位|张|倍|分钟|小时|天|周|期|只|项|笔)"
_CN_UNIT = set("十百千万亿拾佰仟萬")

_AMOUNT_RE = re.compile(
    r"(?:(?P<arabic>\d+(?:\.\d+)?)\s*(?P<arabic_unit>万|千|k|K|w|W)?(?P<arabic_tail>[一二两三四五六七八九1-9])?"
    r"|(?P<chinese>[零〇一二两俩三四五六七八九十百千万亿壹贰叁肆伍陆柒捌玖拾佰仟萬]+))"
    rf"\s*(?P<cur>{_CURRENCY})?\s*(?P<tail>{_NOT_MONEY})?"
)


def _clean_digits(text: str) -> str:
    """千分位逗号、全角数字、¥ 符号 / thousands separators, full-width digits, currency signs."""
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"(?<=\d)[,，](?=\d{3}(?!\d))", "", text)
    return text.replace("¥", " ").replace("￥", " ")


def extract_amount(text: str) -> int | None:
    """
    抽取金额，返回整数分 / Extract an amount, in 分.

    这里保守得刻意。一个被误读的数字不会报错，它会转错金额——所以只有当一个数字
    确实像金额时才接受它：

      · 带货币单位（「三块」「500 元」）
      · 或中文数字里含有量级字（「一千」「两万五」）
      · 或阿拉伯数字且后面不跟「月/日/号/点/%」这类明显不是钱的量词

    计算全程用 Decimal，不碰浮点。「4,500」按 4500 读，不是 4；「1万5」是 15000，
    不是 10000。漏掉一种说法只是多问客户一句；读错一个数字是转错一笔钱。
    """
    text = _clean_digits(text)
    for m in _AMOUNT_RE.finditer(text):
        if m.group("tail") and not m.group("cur"):
            continue                       # 「11 月」「2 号」「30%」不是金额

        if m.group("arabic"):
            digits = m.group("arabic")
            if digits.replace(".", "").isdigit() and len(digits.split(".")[0]) >= 10:
                continue                   # 账号、手机号不是金额
            try:
                value = Decimal(digits)
            except InvalidOperation:
                continue
            unit = m.group("arabic_unit")
            tail = m.group("arabic_tail")
            if unit in ("万", "w", "W"):
                value *= 10_000
                if tail:                  # 「1万5」= 15000
                    value += Decimal(_DIGITS.get(tail, tail)) * 1_000
            elif unit in ("千", "k", "K"):
                value *= 1_000
                if tail:                  # 「2千5」= 2500
                    value += Decimal(_DIGITS.get(tail, tail)) * 100
            if value <= 0:
                continue
            return yuan(str(value.quantize(Decimal("0.01"))))

        if m.group("chinese"):
            token = m.group("chinese")
            if not (set(token) & _CN_UNIT) and not m.group("cur"):
                continue                   # 「清一清」里的「一」
            parsed = parse_chinese_number(token)
            if parsed:
                return yuan(parsed)
    return None


_NEGATIVE_AMOUNT = re.compile(r"(?<![A-Za-z\d])(-|－|负)\s*\d")   # CARD-5521 的连字符不算
_ZERO_AMOUNT = re.compile(r"(?<![\d.])0+(?:\.0+)?\s*(?:元|块|块钱)|零元|零块")


def is_english(text: str) -> bool:
    """这一轮有没有中文 / True when the turn carries Latin letters and no CJK."""
    has_cjk = any("一" <= ch <= "鿿" for ch in text)
    has_latin = any("a" <= ch.lower() <= "z" for ch in text)
    return has_latin and not has_cjk


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"[​-‏⁠﻿]", "", text)
    # 「4,500」里的逗号是千分位，不是分句——先拿掉，否则「给房东打4,500」会被切成
    # 「给房东打4」和「500」，转出去的是四块钱。
    text = re.sub(r"(?<=\d)[,，](?=\d{3}(?!\d))", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


_PUNCT = re.compile(r"[\s,.!?;:~、，。！？；：…·'\"“”‘’()（）\[\]【】<>《》—\-_/\\|]+")


def _core(text: str) -> str:
    """整句应答判断用的核心串 / the bare core of a short reply."""
    t = _PUNCT.sub("", text.lower())
    t = re.sub(r"^(那就|那么|那|嗯嗯|嗯|呃|哦|喔|噢|啊)(?=.)", "", t)
    t = t.rstrip(L.PARTICLES) or t
    return t


def _core_en(text: str) -> str:
    """只在整句都是英文字母时才有意义——「4.5k」「ok 5000」不能被剥成「k」「ok」。"""
    t = re.sub(r"[\s,.!?;:~'\"()\-]+", " ", text.lower()).strip()
    return t if re.fullmatch(r"[a-z' ]+", t) else ""


# ---------------------------------------------------------------------------
# 第一版规则表 / Version-one rule tables — kept as a fallback
# ---------------------------------------------------------------------------
#
# 决策表认不出来的句子，再交给这两张表试一次。它们曾经排在最前面、一条正则
# 一锤定音，过宽的条目因此抢走过本不属于它们的句子；退到后面之后，它们只能
# 补位，不能抢位。

LEGACY_PATTERNS: tuple[tuple[Act, str], ...] = (
    (Act.CONFIRM, r"^\s*(确认|确定|是的|是|好的|好|可以|对|行|没错|同意|继续|执行|OK|ok|yes)\s*[。！!.]?\s*$"),
    # 口语里拒绝很少是光秃秃一个字：「不用了」「不要了」「先不用」「暂时不用」。
    # 原先锚定得太死，这些全部落到 UNKNOWN，于是客户想推掉一个建议却推不掉。
    (Act.DENY, r"^\s*(先|暂时|现在)?(取消|算了|不要|不用|不需要|不|停|别|撤销|放弃|no)"
               r"(了|吧|啦|呢)?\s*[。！!.]?\s*$"),

    (Act.CORRECTION, r"(不对|错了|搞错|说错|不是这个|应该是|重新来)"),

    # 变更预留手机号、邮箱、密码：智能体在任何授权下都不做这件事，
    # 所以这里要识别出来并给一个明确的答复，而不是让它掉进通用兜底。
    (Act.CREDENTIAL, r"(改|换|更换|修改|变更|更新|绑定|解绑|重设|重置).{0,8}"
                     r"(手机号|预留号码|预留手机|预留电话|电话号码|号码|邮箱|密码|登录密码|支付密码)"
                     r"|(手机号|预留号码|预留手机|预留电话|电话号码|邮箱|密码)"
                     r".{0,8}(改|换|变更|更新|重设|重置)"),

    # 「有哪些理财产品」不是「我要买理财」。原先这两句话落在同一个意图上，
    # 于是客户问目录，系统反问「打算投多少」——答非所问。查询和申购要分开。
    (Act.LIST_PRODUCTS, r"(咨询|了解|问问|打听).{0,8}(投资|理财|基金|产品)"
                        r"|(投资|理财|基金|产品).{0,6}(方面|相关).{0,4}(信息|情况|内容)"
                        r"|(有哪些|有什么|都有什么|看看|列出|列一下|查查|查询|介绍|推荐)"
                        r".{0,8}(理财|基金|产品|净值)"
                        r"|(理财|基金|产品).{0,6}(有哪些|有什么|列表|目录|清单)"),
    (Act.LIST_PAYEES, r"(有哪些|有谁|都有谁|列出|查看|看看).{0,6}(收款人|联系人|收款方)"
                      r"|(收款人|联系人|收款方).{0,6}(有哪些|有谁|列表|名单)"
                      r"|(能|可以|常|经常).{0,4}转(账)?的(人|对象|谁)"
                      r"|(存|留)了哪些.{0,6}(人|联系|收款)"
                      r"|常转.{0,8}(人|谁)"
                      r"|(往|给|跟|向)谁.{0,8}(转|汇|打|付)"
                      r"|谁.{0,6}(能收|可以收).{0,4}钱"
                      r"|(能|可以).{0,4}收.{0,6}(转账|钱|款).{0,4}(的|都有|有谁)"
                      r"|收款名单|转账名单"),
    (Act.LIST_CARDS, r"(卡|银行卡|信用卡|借记卡|卡片).{0,4}(信息|状态|详情|情况|资料)"
                     r"|(查询|查看|查|看).{0,6}(卡|银行卡|信用卡|借记卡|卡片)"
                     r"|(有几张|有哪些|列出|查看|看看|都有什么).{0,6}(卡|银行卡|信用卡|借记卡)"
                     r"|(我的卡|名下的卡|银行卡).{0,6}(有几张|有哪些|列表|情况|状态)"),

    # 核心系统里没有利率、汇率、积分这些数据。
    #
    # 这一条是幻觉防护在对话层的对应物：没有数据时正确的回答是说没有，而不是
    # 编一个看起来很合理的数字。它必须排在动作模式之前，否则「利率给我说说」
    # 会被转账那条很宽的规则吃掉。
    (Act.NO_DATA, r"(存款利率|贷款利率|利率|利息|年化|收益率|业绩比较基准"
                  r"|汇率|外汇|换(美元|欧元|日元|汇)|美元.{0,4}(价|多少)"
                  r"|积分|里程|信用分|征信)"),

    # 客户说「卡被盗刷了」时并没有说「请冻结」，但他要的就是这个。
    # 把它接到冻结上，是「真实问题解决度」这一项真正考察的东西。
    (Act.CARD_FREEZE, r"(盗刷|被盗|盗用|盗了|丢了|丢失|不见了|泄露|被偷|可疑扣款)"),
    # 「取消冻结」里有「冻结」两个字，原先被冻结规则抢走——客户要求解冻，
    # 系统却提议冻结，方向正好相反。否定词必须先看。
    (Act.CARD_UNFREEZE, r"(取消|解除|撤销|去掉|去除).{0,4}(冻结|锁定|挂失|停用)"
                        r"|(冻结|锁定|挂失).{0,6}(取消|解除|撤销|去掉|打开)"
                        r"|(解冻|解锁|恢复|启用).{0,4}(卡|银行卡|信用卡|借记卡)"
                        r"|把.{0,12}卡.{0,6}(解冻|解锁|打开|启用|恢复|开通)"
                        r"|(卡|卡片).{0,8}(恢复|重新开通|重新启用)"),
    (Act.CARD_FREEZE, r"(?<!取消)(?<!解除)(?<!撤销)(冻结|锁卡|锁住|停用|挂失)"
                      r".{0,4}(卡|银行卡|信用卡|借记卡)?|把.{0,6}卡.{0,6}(冻|锁|停)"),
    (Act.CARD_LIMIT, r"(额度|限额|上限).{0,8}(调|改|提高|降低|设置|变成|成)"
                     r"|(调整|修改|提高|降低|调高|调低|往上调|往下调).{0,8}(额度|限额|上限)"
                     r"|(每天|单笔|每日).{0,6}(能刷|可刷|最多).{0,8}(调|改|少|多)"),

    (Act.CANCEL_MANDATE, r"把(?!.{0,8}卡).{0,10}(的)?\s*(退了|退掉|退订|取消了?|关了|关掉|停了|停掉|清掉)"
                          r"|(退|取消|关|停).{0,3}(个|掉|了)?.{0,4}(订阅|代扣|自动续费|会员|扣费)"
                          r"|(取消|退订|关掉|关闭|停掉|不要了|退了|清理|清掉).{0,8}(订阅|代扣|自动续费|会员|扣费)"
                          r"|(订阅|代扣|自动续费|会员|扣费).{0,8}(清一清|清理|清掉|退掉|取消|关掉|停掉|不要了)"
                          r"|把.{0,12}(订阅|代扣|会员|续费).{0,6}(退|取消|关|停|清|理)"),
    (Act.LIST_MANDATES, r"(有哪些|查看|看看|列出|都有什么|多少).{0,6}(订阅|代扣|自动扣费|自动续费|扣费)"
                         r"|(订阅|代扣|自动扣费|续费).{0,6}(有哪些|列表|情况|几项|几笔)"
                         r"|(有哪些|有几|几项|几笔|多少).{0,8}(续费|扣费|代扣|订阅)"
                         r"|(按月|每月|每个月|定期).{0,8}(扣|续费|划).{0,6}(有哪些|是什么|几项|都有)"
                         r"|(看看|查看|有哪些|列出).{0,8}(可以|能).{0,2}(取消|退订|关掉)"),

    (Act.REDEEM, r"(赎回|卖出|取出|提现).{0,6}(理财|基金|产品)?"
                  r"|把.{0,8}(理财|基金|产品|持仓).{0,6}(赎|卖|取|退)"
                  r"|(理财|基金|产品|持仓).{0,8}(退出来|取回来|拿回来|卖了|卖掉|不想拿)"
                  r"|(投|买).{0,6}(进去|的).{0,4}钱.{0,4}(取|拿|退)"
                  r"|(理财|基金|产品|持仓).{0,10}(赎掉|赎了|不做了|不要了|清掉|结掉|换成现金)"
                  r"|(全部|都|统统|一次性).{0,6}(赎|卖|清)"),
    (Act.INVEST, r"(买|购买|申购|投|投资|配置).{0,8}(理财|基金|产品)|(理财|基金).{0,4}(买|申购|投)"
                  r"|有.{0,4}(什么|哪些).{0,4}(理财|基金|产品)"),

    (Act.BALANCE, r"(余额|还有多少钱|有多少钱|账户.{0,4}多少|我的账户|账户情况|账户概览)"),
    # 「花哪」原先要求这两个字紧挨着，于是「钱都花在哪里」「钱花到哪去了」
    # 全部落空——客户把同一个问题换个说法就问不出来了。动词和疑问词之间
    # 要允许有字。「明细」是柜面和网银里最常见的说法，原先整个词表里没有。
    (Act.ANALYSE_BILLS, r"(账单|流水|明细|花了多少|支出|开销|花销|对账|账目"
                        r"|消费(记录|情况|明细|构成|习惯)|(记录|情况|构成|分析).{0,4}消费"
                        r"|钱.{0,6}(花|用|去|跑).{0,4}(哪|何处)"
                        r"|(花|用|跑).{0,4}(哪|何处).{0,4}(了|去))"),

    (Act.TRANSFER, r"(转账|转钱|打款|汇款|汇钱)|(?<!自动)付款"
                    r"|(转|给|付|汇|划|打|存|送).{0,10}"
                    r"(\d{2,}|[零一二两三四五六七八九十百千万]{1,8}(块|元|钱|万|千))"
                    r"|(转|给|汇|划|打|存|送).{0,6}(钱|款子)"
                    r"|(存|打|转|汇|送).{0,4}(点|些)(钱)?"
                    r"|(?<!自动)(交|缴|付|结).{0,4}(房租|电费|水费|话费|水电)"),

    (Act.HELP, r"(你能做什么|能干什么|会什么|帮助|怎么用|有什么功能|help)"),
    (Act.GREETING, r"^\s*(你好|您好|hi|hello|哈喽|在吗|早上好|下午好|晚上好)\s*[。！!.]?\s*$"),
)

# 明显不属于银行业务的请求 / clearly outside the banking domain
LEGACY_OUT_OF_SCOPE = (
    r"(天气|气温|下雨|下雪|带伞|雨伞|明天.{0,4}穿)",
    r"(机票|酒店|订餐|外卖|打车|快递|高铁|火车票)",
    r"(便利店|餐厅|商场|超市|加油站).{0,8}(在哪|哪里|附近|怎么走)"
    r"|(最近|附近|周边).{0,6}(支行|网点|营业厅|柜台|atm|ATM|便利店|餐厅|商场)"
    r"|(支行|网点|营业厅|atm|ATM).{0,6}(在哪|哪里|地址|怎么走)",
    r"(股票.{0,4}(推荐|预测|涨|跌)|币价|比特币|彩票)",
    r"(你是谁|什么模型|GPT|大模型|你是.{0,4}(AI|人工智能|机器人)|是不是(AI|人工智能|机器人))",
    r"(写.{0,4}(代码|作业|论文)|翻译|讲个笑话|电影|电视剧|小说|音乐|游戏|球赛|比赛几点)",
    r"(贷款.{0,4}审批|开户|销户|改密码|重置密码)",
)


# ---------------------------------------------------------------------------
# 英文层 / English layer
# ---------------------------------------------------------------------------
#
# 中文规则命中失败后才扫这一层，所以它不会改变任何中文输入的理解结果。
#
# 加这一层的原因很实际：评测时用浏览器自动翻译打开页面的人，会照着翻译后的
# 界面用英文提问。那种情况下回一句「没听懂」，看上去像系统坏了，而不是像
# 一个只说中文的系统。理解客户说的话属于不可信侧，多支持一种语言不触及
# 任何安全性质——内核读的是效果，不是文字。

LEGACY_EN_PATTERNS: tuple[tuple[Act, str], ...] = (
    (Act.CONFIRM, r"^\s*(yes|yep|yeah|ok|okay|sure|confirm|confirmed|go ahead|do it|proceed|correct)\s*[.!]?\s*$"),
    (Act.DENY, r"^\s*(no|nope|cancel|stop|never ?mind|forget it|abort)\s*[.!]?\s*$"),
    (Act.CORRECTION, r"(that'?s wrong|i made a mistake|not that one|actually it'?s|start over|let'?s redo)"),

    (Act.CREDENTIAL, r"(change|update|reset|modify|replace).{0,24}"
                     r"(phone|mobile|number|e-?mail|password|passcode|pin)"),

    (Act.NO_DATA, r"(\binterest\b|deposit rate|savings rate|exchange rate|fx rate"
                  r"|annualised|annualized|\byield\b|\bapy\b|\bapr\b"
                  r"|credit score|reward points|air ?miles)"),

    (Act.LIST_PRODUCTS, r"(what|which|list|show|any|recommend).{0,28}"
                        r"(product|fund|wealth|investment|portfolio)s?"
                        r"|(product|fund)s?.{0,16}(available|on offer|do you have)"),
    (Act.LIST_PAYEES, r"(payee|beneficiar|saved recipient|recipient list|address book)"),
    (Act.LIST_CARDS, r"(what|which|list|show|how many).{0,20}cards?"
                     r"|my cards?.{0,16}(status|list)"),

    (Act.CARD_FREEZE, r"(stolen|fraud|fraudulent|unauthoris|unauthoriz|compromised"
                      r"|lost my card|card.{0,12}(stolen|lost))"),
    (Act.CARD_UNFREEZE, r"(unfreeze|un-?block|unlock|reactivate|re-?enable).{0,16}card"),
    (Act.CARD_FREEZE, r"(freeze|block|lock|suspend|disable).{0,16}card"),
    (Act.CARD_LIMIT, r"(limit).{0,20}(change|raise|increase|lower|reduce|set|to \d)"
                     r"|(change|raise|increase|lower|reduce|set).{0,20}limit"),

    (Act.CANCEL_MANDATE, r"(cancel|stop|end|unsubscribe|get rid of|kill).{0,28}"
                         r"(subscription|direct debit|auto.?pay|auto.?debit|membership|recurring)"),
    (Act.LIST_MANDATES, r"(what|which|list|show|any|how many).{0,28}"
                        r"(subscription|direct debit|auto.?pay|auto.?debit|recurring)"),

    (Act.REDEEM, r"(redeem|sell|cash out|liquidate).{0,20}(fund|product|investment|holding)?"),
    (Act.INVEST, r"(buy|purchase|invest|put money).{0,28}(fund|product|wealth|investment)"),

    (Act.BALANCE, r"(balance|my accounts?\b|account overview"
                  r"|how much.{0,16}(do i have|is in|left|remaining))"),
    (Act.ANALYSE_BILLS, r"(statement|spending|spent|expenses?|outgoings?|transactions?"
                        r"|breakdown|where.{0,16}(money|it).{0,12}go)"),

    (Act.TRANSFER, r"(transfer|send money|pay |wire |remit|send \d)"),

    (Act.HELP, r"(what can you do|help|how do i use|capabilit|what do you do)"),
    (Act.GREETING, r"^\s*(hi|hello|hey|good (morning|afternoon|evening))\s*[.!]?\s*$"),
)

LEGACY_EN_OUT_OF_SCOPE = (
    r"(weather|forecast|temperature|will it rain)",
    r"(flight|hotel|book a table|takeaway|taxi|parcel)",
    r"(stock tip|will .{0,12}(go up|crash)|bitcoin|crypto price|lottery)",
    r"(who are you|which model|are you (an? )?(ai|gpt|llm))",
    r"(write (me )?(code|an essay|my homework)|translate this|tell me a joke)",
    r"(loan approval|open an account|close my account)",
)



# ---------------------------------------------------------------------------
# 理解结果 / Parse results
# ---------------------------------------------------------------------------

@dataclass
class Frame:
    """一个分句理解出来的一件事 / One thing one clause asks for."""
    act: Act
    slots: dict[str, Any] = field(default_factory=dict)
    route: str = "rule"          # rule | similar | confirm | none
    confidence: float = 1.0
    text: str = ""
    flags: set[str] = field(default_factory=set)
    missing: list[str] = field(default_factory=list)


@dataclass
class Utterance:
    """一轮用户输入的理解结果 / What one customer turn was understood to mean."""
    raw: str
    act: Act
    slots: dict[str, Any] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    lang: str = "zh"              # zh | en
    lang_signal: bool = True      # 这一轮是否足以判断客户在用哪种语言
    route: str = "none"           # rule | similar | confirm | none
    confidence: float = 0.0
    frames: list[Frame] = field(default_factory=list)
    ordinal: int | None = None    # 「第二个」→ 2；「最后一个」→ -1
    flags: set[str] = field(default_factory=set)

    @property
    def scenario(self) -> str:
        return self.act.scenario

    def to_dict(self) -> dict[str, Any]:
        return {"raw": self.raw, "act": self.act.value, "scenario": self.scenario,
                "slots": {k: v for k, v in sorted(self.slots.items()) if not isinstance(v, set)},
                "missing": self.missing, "lang": self.lang,
                "route": self.route, "confidence": round(self.confidence, 3),
                "frames": [{"act": f.act.value, "route": f.route,
                            "slots": {k: v for k, v in sorted(f.slots.items())}} for f in self.frames],
                "ordinal": self.ordinal, "flags": sorted(self.flags)}


class DialogueModel(Protocol):
    """接入真实大模型时实现这个协议 / Implement this to plug in a real model."""
    def understand(self, text: str, history: list[dict[str, str]]) -> Utterance: ...


# ---------------------------------------------------------------------------
# 相似度兜底 / Similarity fallback
# ---------------------------------------------------------------------------

# 门槛是跑 benchmarks/paraphrases.py 调出来的，不是拍出来的。
_SIM_ACCEPT_READ = 0.16      # 只读/信息类：猜错只是白读一次
_SIM_ACCEPT_ACTION = 0.30    # 动钱类：要更有把握才直接往下走
_SIM_CONFIRM_FLOOR = 0.16    # 动钱类在这之上、门槛之下：先问一句「您是想…吗」

_MATCHER = None


def _matcher():
    """语料只有几百条，构建一次不到十毫秒，第一次用到时再建。"""
    global _MATCHER
    if _MATCHER is None:
        from arbiter.dialogue.corpus import pairs
        from arbiter.dialogue.similarity import SimilarityMatcher
        _MATCHER = SimilarityMatcher(pairs())
    return _MATCHER


def _looks_like_a_question(text: str) -> bool:
    """问句形状、且不含任何动作词 / interrogative in shape, with no verb of action."""
    from arbiter.dialogue.similarity import _ACTION as _A
    from arbiter.dialogue.similarity import _QUESTION as _Q
    return bool(_Q.search(text)) and not _A.search(text)


_INQUIRY = re.compile(r"(咨询|了解|介绍|问问|打听|方面的?信息|相关信息|有哪些|有什么|都有什么|有啥|都有啥|什么产品|哪些产品"
                      r"|(能|可以|可)(买|投|申购)(哪|什么)|哪些|哪几|哪款|哪种"
                      r"|哪几只|哪几种|列表|目录|清单|推荐|在售|能买的|可买的|可投的|货架|提供什么"
                      r"|what .*(have|offer)|which .*available|which (funds|products)|what (funds|products)"
                      r"|can i buy|list|show)")
# 「能买的」「可以申购的」「i can buy」问的是有什么可买，不是要买
_EXPLICIT_BUY = re.compile(r"((?<!能)(?<!可以)(?<!可)(?<!能够)(买|购买|申购|认购|入手)(?!的|什么|哪)"
                           r"|(?<!can )(?<!can i )\bbuy\b(?! what)|(?<!can )(?<!can i )purchase)")


def transfer_to_person(slots: dict[str, Any]) -> bool:
    """点名了收款人或收款账号 / a payee or payee account was named."""
    return bool(slots.get("payee") or slots.get("payee_account"))


class RuleBasedNLU:
    """
    离线理解器 / The offline understander.

    覆盖六大场景的常见口语说法。它会犯错——漏掉一种没见过的表达方式——但犯错的
    代价被架构限制住了：最坏的结果是多问客户一句，或者提议一个客户会拒绝签署的
    信封。
    """

    def __init__(self, core: Any = None):
        # 核心系统里真实存在的名字 / the names that actually exist in core state
        self.vocab: dict[str, tuple[str, str]] = {}
        if core is not None:
            self.bind(core)

    def bind(self, core: Any) -> None:
        """
        把核心系统里的标识符接进词表。

        **界面显示得出的东西，解析器必须听得懂。** 卡号、产品代码、商户名都是系统
        自己印在屏幕上的，让客户照着念回来却认不出，是自相矛盾。所以这张表从
        BankCore 的状态里生成，换一套数据也自动跟着变。
        """
        v: dict[str, tuple[str, str]] = {}

        def put(key: str, slot: str, value: str) -> None:
            key = unicodedata.normalize("NFKC", (key or "")).strip().lower()
            if len(key) >= 2:
                v.setdefault(key, (slot, value))

        for c in core.state.cards.values():
            put(c.card_id, "card", c.card_id)
            tail = c.card_id.rsplit("-", 1)[-1]
            if tail.isdigit():
                put(tail, "card", c.card_id)
                put("尾号" + tail, "card", c.card_id)
                put("尾号是" + tail, "card", c.card_id)
        for m in core.state.mandates.values():
            put(m.mandate_id, "mandate", m.mandate_id)
            put(m.merchant, "mandate", m.mandate_id)
            short = re.sub(r"^某", "", m.merchant)
            short = re.sub(r"(服务|有限公司|公司)$", "", short)
            put(short, "mandate", m.mandate_id)
        for pr in core.state.products.values():
            put(pr.code, "product", pr.code)
            put(pr.name, "product", pr.code)
        for pe in core.state.payees.values():
            put(pe.name, "payee_literal", pe.account_no)
            put(pe.alias, "payee_literal", pe.account_no)
            put(pe.account_no, "payee_literal", pe.account_no)
        for a in core.state.accounts.values():
            put(getattr(a, "nickname", ""), "account_literal", a.account_no)
        self.vocab = v

    # ------------------------------------------------------------------

    def understand(self, text: str, history: list[dict[str, str]] | None = None,
                   context: dict[str, Any] | None = None) -> Utterance:
        raw = (text or "").strip()
        if not raw:
            return Utterance(raw, Act.UNKNOWN, lang_signal=False)

        norm = _normalize(raw)
        low = norm.lower()

        # 「CARD-8847」是拉丁字母，但它不是英文——它是界面上印着的卡号。
        residue = low
        for key in sorted(self.vocab, key=len, reverse=True):
            if key in residue:
                residue = residue.replace(key, " ")
        lang_signal = any(ch.isalpha() for ch in residue)
        lang = "en" if is_english(residue) else "zh"

        core = _core(norm)
        core_en = _core_en(norm)
        # 去掉语气词之前的样子：「算了」剥掉「了」只剩「算」，两种都要认
        full = _PUNCT.sub("", low)

        def whole(act: Act, **kw: Any) -> Utterance:
            return Utterance(raw, act, lang=lang, lang_signal=lang_signal, route="rule",
                             confidence=1.0, **kw)

        # ---- 整句应答 / whole-utterance replies -------------------------
        # 「好的谢谢」先当成道谢，而不是确认：确认必须没有歧义。
        if {core, full, core_en} & L.THANKS_WORDS:
            return whole(Act.THANKS)
        if ({core, full, core_en} & L.BYE_WORDS or L.BYE_RE.match(full) or L.BYE_RE.match(core_en)
                or (L.BYE_CHAIN_RE.match(full) and L.BYE_CHAIN_MUST.search(full))
                or (core_en and L.BYE_EN_RE.match(core_en))):
            return whole(Act.BYE)
        if self._is_confirm(core, core_en) or self._is_confirm(full, ""):
            return whole(Act.CONFIRM)
        if self._is_deny(core, core_en) or self._is_deny(full, ""):
            return whole(Act.DENY)
        if {core, full, core_en} & L.GREETING_WORDS:
            return whole(Act.GREETING)
        if L.HELP_RE.search(low) and not self._has_task_words(low):
            return whole(Act.HELP)

        ordinal = self._ordinal(core, core_en)
        if ordinal is not None:
            return Utterance(raw, Act.UNKNOWN, lang=lang, lang_signal=False, route="none",
                             confidence=0.0, ordinal=ordinal)

        spaced = re.sub(r"[\s,.!?;:]+", " ", low).strip()
        correction_en = bool(L.CORRECTION_EN_RE.search(spaced))
        correction = bool(L.CORRECTION_RE.search(core) or L.CORRECTION_RE.search(core_en)) or correction_en
        if correction and (L.OBJ_CREDENTIAL.search(low) or L.OBJ_SENSITIVE.search(low)):
            correction = False          # 「改成新的手机号」是凭证变更，不是改刚才那件事
        body = norm
        if correction:
            if L.CORRECTION_RE.search(core):
                body = L.CORRECTION_RE.sub("", core, count=1)
            elif correction_en:
                body = L.CORRECTION_EN_RE.sub(" ", spaced, count=1).strip() or norm

        # ---- 分句 / clauses ---------------------------------------------
        pieces = [p.strip() for p in L.CLAUSE_SPLIT.split(body) if p and p.strip()]
        pieces = [L.LEADING_JOINERS.sub("", p).strip() for p in pieces]
        pieces = [p for p in pieces if p]

        pieces = self._join_continuations(pieces)

        flags: set[str] = set()
        if pieces and len(pieces) > 1 and (self._is_deny(_core(pieces[0]), _core_en(pieces[0]))):
            flags.add("leading_deny")      # 「算了，看看余额」
            pieces = pieces[1:]

        frames: list[Frame] = []
        for piece in pieces:
            f = self._clause(piece, context)
            if f is not None:
                frames.append(f)
        if not frames and len(pieces) > 1:
            f = self._clause(" ".join(pieces), context)
            if f is not None:
                frames.append(f)

        frames = self._merge(frames)
        # 一句话里有正事时，夹在中间的寒暄就不单独回应了
        if len(frames) > 1:
            frames = [f for f in frames if f.act not in (Act.GREETING, Act.THANKS)] or frames

        if correction:
            slots: dict[str, Any] = {}
            for f in frames:
                for k, v in f.slots.items():
                    slots.setdefault(k, v)
            u = whole(Act.CORRECTION, slots=slots, frames=frames, flags=flags)
            return u

        if not frames:
            return Utterance(raw, Act.UNKNOWN, lang=lang, lang_signal=lang_signal, route="none",
                             confidence=0.0, flags=flags)

        primary = next((f for f in frames if f.act in ACTION_ACTS), frames[0])
        for f in frames:
            flags |= f.flags
        return Utterance(raw, primary.act, dict(primary.slots), list(primary.missing),
                         lang=lang, lang_signal=lang_signal, route=primary.route,
                         confidence=primary.confidence, frames=frames, flags=flags)

    # ------------------------------------------------------------------
    # 整句应答的判定 / Whole-utterance classifiers
    # ------------------------------------------------------------------

    _CONFIRM_RE = re.compile(
        r"^(好|行|可以|确认|确定|是|对|嗯|同意|没问题|ok|okay|yes|sure)+(的|吧|啊|呀|嘞|滴|哒)?"
        r"(确认|执行|办吧|办理|转吧|就这样|就这么办|没问题|please|goahead)?$")
    _DENY_RE = re.compile(
        r"^(那就|那|还是|先|暂时|现在|我)?(不|别|取消|算了|不要|不用|不需要|不了|停|放弃|撤销|不想)"
        r"(了|吧|啦|呢|啊)*$"
        r"|^不(转|买|冻|冻结|解冻|取消|办|弄|做|退|赎|调|要|用|续)了?(吧)?$")
    # 「算了不转了」「那算了吧不用了」：两三个否定说法叠在一起，仍然只是「不要」
    _DENY_CHAIN_RE = re.compile(
        r"^(那就|那|还是|先|暂时|现在|我|哎|唉)?"
        r"((算了|不要|不用|不需要|不了|放弃|撤销|取消|不想|别|不)(了|吧|啦|呢|啊)*"
        r"|不(转|买|冻|冻结|解冻|取消|办|弄|做|退|赎|调|要|用|续)(了|吧|啦)*){2,3}$")

    @classmethod
    def _is_confirm(cls, core: str, core_en: str) -> bool:
        if core in L.CONFIRM_WORDS or core_en in L.CONFIRM_WORDS:
            return True
        return bool(cls._CONFIRM_RE.match(core)) and len(core) <= 12

    @classmethod
    def _is_deny(cls, core: str, core_en: str) -> bool:
        if core in L.DENY_WORDS or core_en in L.DENY_WORDS:
            return True
        if cls._DENY_CHAIN_RE.match(core) and len(core) <= 12:
            return True
        return bool(cls._DENY_RE.match(core)) and len(core) <= 10

    @staticmethod
    def _ordinal(core: str, core_en: str) -> int | None:
        for probe in (core, core_en):
            m = L.ORDINAL_RE.match(probe)
            if not m:
                continue
            if m.group(2):
                return L.CN_ORDINAL.get(m.group(2)) or int(m.group(2))
            if m.group(4) or probe.startswith(("最后", "末尾")):
                return -1
            if m.group(8):
                return L.EN_ORDINAL[m.group(8)]
            if m.group(10):
                return int(m.group(10))
            if m.group(13):
                return int(m.group(13))
        return None

    @staticmethod
    def _has_task_words(low: str) -> bool:
        return bool(L.ACT_TRANSFER.search(low) or L.ACT_FREEZE.search(low) or L.ACT_BUY.search(low)
                    or L.OBJ_STATEMENT.search(low) or L.OBJ_BALANCE.search(low))

    # ------------------------------------------------------------------
    # 承接前一句的分句 / Clauses that lean on the previous one
    # ------------------------------------------------------------------

    _OBJECTS = (L.OBJ_CARD, L.OBJ_MANDATE, L.OBJ_PRODUCT)
    _ACTIONS = (L.ACT_UNFREEZE, L.ACT_FREEZE, L.ACT_CANCEL, L.ACT_BUY, L.ACT_SELL, L.ACT_LIMIT)

    def _names_something(self, piece: str) -> bool:
        low = piece.lower()
        if any(rx.search(low) for rx in self._OBJECTS):
            return True
        if re.fullmatch(r"(我的|那张|这张)?卡.{0,6}", low):     # 「卡找到了」「卡冻上了」
            return True
        slots, _ = self._slots_and_residue(piece)
        return bool(slots.keys() & {"card", "cards", "mandate", "mandates", "product", "payee", "payee_account"})

    def _join_continuations(self, pieces: list[str]) -> list[str]:
        """
        「信用卡冻上了，帮我解开」「I found my card, turn it back on」：后一句只有动作，
        要么没说对什么做，要么用「它 / it」指回前一句。分开理解时，前一句被读成「冻结」
        或「查卡」，方向正好相反。这样的后一句并回前一句一起理解。

        只在前一句点名了卡、代扣或产品、且没有转账时才并：「给房东转 4500，然后冻结」
        里的两件事不能合成一件。
        """
        out: list[str] = []
        for piece in pieces:
            if out:
                low = piece.lower()
                prev = out[-1]
                prev_low = prev.lower()
                leans = (any(rx.search(low) for rx in self._ACTIONS) or L.PRONOUN_RE.search(low)) \
                    and not self._names_something(piece)
                if leans and self._names_something(prev) and not (
                        L.ACT_TRANSFER.search(prev_low) or L.ACT_TRANSFER_FRAME.search(prev_low)):
                    out[-1] = f"{prev} {piece}"
                    continue
            out.append(piece)
        return out

    # ------------------------------------------------------------------
    # 一个分句 / One clause
    # ------------------------------------------------------------------

    def _clause(self, piece: str, context: dict[str, Any] | None) -> Frame | None:
        low = piece.lower()
        slots, residue = self._slots_and_residue(piece)
        flags: set[str] = set()
        if L.SCAM_RE.search(low):
            flags.add("scam")
        if slots.pop("_amount_invalid", False):
            flags.add("amount_invalid")

        act = self._decide(piece, low, slots, flags, residue)
        route, conf = "rule", 1.0

        if act is None:
            act = self._legacy(piece, low)

        stripped = re.sub(r"[\W_]+", "", residue)
        if act is None:
            if not stripped and any(k in slots for k in ("card", "mandate", "product", "payee_account",
                                                        "mandates", "cards")):
                act = Act.REFERENCE
            elif "amount" in slots and not re.sub(
                    r"[0-9零一二两三四五六七八九十百千万亿块元钱整人民币rmbyuanwk\s]+", "", stripped):
                act = Act.AMOUNT_ONLY

        if act is None:
            act, route, conf = self._similar(piece)
            if act is None:
                return None

        if act is Act.GREETING and len(piece) > 8:
            return None
        frame = Frame(act, slots, route, conf, piece, flags)
        frame.missing = self._missing(act, slots)
        return frame

    def _decide(self, piece: str, low: str, slots: dict[str, Any], flags: set[str],
                residue: str = "") -> Act | None:
        """
        决策表 / The decision table.

        顺序有讲究，但讲究的是**具体性**而不是先来后到：
          1. 信息套取与凭证——无论怎么问都不做，最先认出来
          2. 无数据、超范围——但一句话里有明确的银行动作时不算超范围
          3. 卡片、订阅、理财、转账——由「东西 + 动作 + 否定」组合判定
          4. 账单、余额——纯查询
        """
        has = lambda rx: bool(rx.search(low))  # noqa: E731
        q = has(L.QUESTION_RE)

        card_obj = has(L.OBJ_CARD) or "card" in slots or "cards" in slots
        unfreeze = has(L.ACT_UNFREEZE)
        freeze = has(L.ACT_FREEZE) and not unfreeze
        fraud_strong = has(L.ACT_FRAUD) and not re.search(r"(出问题|有问题)", low)
        fraud_weak = bool(re.search(r"(出问题|有问题|异常)", low))
        limit = has(L.ACT_LIMIT)
        mandate_obj = has(L.OBJ_MANDATE) or "mandate" in slots or "mandates" in slots
        product_obj = has(L.OBJ_PRODUCT) or "product" in slots
        cancel = has(L.ACT_CANCEL)
        buy = has(L.ACT_BUY)
        sell = has(L.ACT_SELL)
        transfer = has(L.ACT_TRANSFER) or has(L.ACT_TRANSFER_FRAME)
        if card_obj and not (has(L.CARD_BARE) or has(L.OBJ_CARD)):
            card_obj = True
        bare_card = has(L.CARD_BARE)

        banking_action = (unfreeze or freeze or limit or (cancel and mandate_obj) or buy or sell
                          or transfer or fraud_strong)

        # 1 · 信息套取 / information extraction --------------------------
        if has(L.EXFIL_RE):
            flags.add("exfil")
            return Act.SENSITIVE_INFO
        if has(L.THIRD_PARTY_RE) and not transfer:
            flags.add("third_party")
            return Act.SENSITIVE_INFO
        strong_action = unfreeze or freeze or limit or buy or sell or (transfer and "amount" in slots)
        if has(L.OBJ_SENSITIVE) and not strong_action:
            if has(L.ACT_CHANGE) and not has(L.ACT_DISCLOSE):
                return Act.CREDENTIAL
            if has(L.OTP_RE):
                flags.add("otp")
            return Act.SENSITIVE_INFO
        if has(L.OBJ_CREDENTIAL) and has(L.ACT_CHANGE) and not strong_action and not mandate_obj:
            return Act.CREDENTIAL

        # 2 · 无数据 / 超范围 ---------------------------------------------
        if has(L.OBJ_RATE) and not (buy and "amount" in slots):
            return Act.NO_DATA
        if has(L.LOCATION_RE):
            return Act.OUT_OF_SCOPE
        if has(L.OUT_OF_SCOPE_RE) and not has(L.PRODUCT_NOT_STOCK) and not banking_action:
            return Act.OUT_OF_SCOPE
        # 「我想买股票」「which stock should I buy」：有「买」也不是申购理财
        if has(L.STOCK_TIP_RE) and not has(L.PRODUCT_NOT_STOCK) and "product" not in slots:
            return Act.OUT_OF_SCOPE

        # 3a · 卡片 / cards ------------------------------------------------
        card_ctx = card_obj or (bare_card and (unfreeze or freeze or limit or fraud_strong or fraud_weak))
        # 「解冻」「unfreeze」本身就只能是卡：没说「卡」也不该落到相似度去猜
        explicit_unfreeze = re.search(r"(解冻|解锁|\bun-?freeze\b|\bun-?block\b|\bun-?lock\b|\bunsuspend\b"
                                      r"|\bthaw\b|reactivate)", low)
        if unfreeze and (card_ctx or re.search(r"(冻结|锁定|挂失|停用)", low)
                         or (explicit_unfreeze and not (mandate_obj or product_obj))):
            return Act.CARD_UNFREEZE
        if (freeze and (card_ctx or not mandate_obj)) and not cancel_mandate_context(low, mandate_obj):
            return Act.CARD_FREEZE
        if fraud_strong or (fraud_weak and card_ctx):
            return Act.CARD_FREEZE
        if limit and (card_ctx or re.search(r"(额度|限额|上限)", low)):
            return Act.CARD_LIMIT

        # 3b · 订阅与代扣 / mandates ---------------------------------------
        if cancel and mandate_obj:
            return Act.CANCEL_MANDATE

        # 3b' · 订阅的查询要排在转账前面：「自动付款」里的「付款」不是转账
        if mandate_obj and (q or has(L.ACT_QUERY)) and "amount" not in slots and not slots.get("payee"):
            return Act.LIST_MANDATES

        # 3c · 理财 / wealth ----------------------------------------------
        if product_obj or buy or sell:
            if sell or (cancel and product_obj and not mandate_obj):
                return Act.REDEEM
            # 点名了产品、又给了金额，就是申购：「投五万到 F000455」里的「到」不是转账
            if "product" in slots and "amount" in slots and not transfer_to_person(slots):
                return Act.INVEST
            if (has(_INQUIRY) and "amount" not in slots and not has(_EXPLICIT_BUY)) or \
                    (product_obj and not buy and (q or has(L.ACT_QUERY))):
                if "product" in slots and not has(_INQUIRY) and not q and not has(L.ACT_QUERY):
                    return None            # 只点了一只产品的名字：交给「说明是什么」
                return Act.LIST_PRODUCTS
            if buy:
                return Act.INVEST

        # 3d · 收款人名单 / payee list ------------------------------------
        if has(L.OBJ_PAYEE_LIST) and "amount" not in slots and (q or has(L.ACT_QUERY) or not transfer):
            return Act.LIST_PAYEES

        # 3e · 转账 / transfers -------------------------------------------
        if transfer:
            return Act.TRANSFER
        if slots.get("payee") and "amount" in slots:
            return Act.TRANSFER            # 「王建国 4500」

        # 3f · 卡片与订阅的查询 / queries ---------------------------------
        if card_obj and not mandate_obj:
            # 「CARD-5521」里的 card 是卡号的一部分，不是「卡」这个词：在去掉标识符的
            # 剩余文字上判断客户有没有说「卡」。
            if "card" in slots and not (q or has(L.ACT_QUERY)) and not L.OBJ_CARD.search(residue or low):
                return None                # 只说了卡号：交给「说明是什么」
            if q or has(L.ACT_QUERY) or has(L.OBJ_CARD):
                return Act.LIST_CARDS
        if mandate_obj:
            named_only = ("mandate" in slots or "mandates" in slots) and not (
                q or has(L.ACT_QUERY) or re.search(r"(订阅|代扣|自动扣|续费|扣费|扣款|subscription)", low))
            if named_only:
                return None                # 只说了商户名：交给「说明是什么」
            return Act.LIST_MANDATES

        # 4 · 账单与余额 / statements and balances ------------------------
        if has(L.OBJ_STATEMENT):
            return Act.ANALYSE_BILLS
        if has(L.OBJ_BALANCE):
            return Act.BALANCE

        # 寒暄夹在分句里 / a greeting inside a longer turn
        c = _core(piece)
        if c in L.GREETING_WORDS:
            return Act.GREETING
        if c in L.THANKS_WORDS:
            return Act.THANKS
        return None

    def _legacy(self, piece: str, low: str) -> Act | None:
        skip = (Act.CONFIRM, Act.DENY, Act.GREETING, Act.CORRECTION, Act.HELP)
        for pattern in LEGACY_OUT_OF_SCOPE:
            if re.search(pattern, piece):
                return Act.OUT_OF_SCOPE
        for candidate, pattern in LEGACY_PATTERNS:
            if candidate in skip:
                continue
            if re.search(pattern, piece):
                return candidate
        if is_english(piece):
            for pattern in LEGACY_EN_OUT_OF_SCOPE:
                if re.search(pattern, low):
                    return Act.OUT_OF_SCOPE
            for candidate, pattern in LEGACY_EN_PATTERNS:
                if candidate in skip:
                    continue
                if re.search(pattern, low):
                    return candidate
        return None

    def _similar(self, piece: str) -> tuple[Act | None, str, float]:
        """规则认不出来时，和语料里的真人说法比一比。"""
        hit = _matcher().best(piece)
        if hit is None:
            return None, "none", 0.0
        label, score, _example = hit
        # 问句形状、又没有动作词：不许被相似度送进动钱或动控制的意图
        if label in ACTION_ACTS and _looks_like_a_question(piece):
            return None, "none", score
        if label in ACTION_ACTS:
            if score >= _SIM_ACCEPT_ACTION:
                return label, "similar", score
            if score >= _SIM_CONFIRM_FLOOR:
                return label, "confirm", score
            return None, "none", score
        if label in UTTERANCE_ACTS:
            return None, "none", score
        if score >= _SIM_ACCEPT_READ:
            return label, "similar", score
        return None, "none", score

    @staticmethod
    def _merge(frames: list[Frame]) -> list[Frame]:
        """「基金不想要了，换成现金」是一件事，不是两件。"""
        out: list[Frame] = []
        for f in frames:
            if out and out[-1].act is f.act and f.act not in (Act.UNKNOWN,):
                prev = out[-1]
                conflict = any(k in prev.slots and prev.slots[k] != v for k, v in f.slots.items()
                               if not k.startswith("stmt_"))
                if not conflict:
                    for k, v in f.slots.items():
                        prev.slots.setdefault(k, v)
                    prev.flags |= f.flags
                    prev.missing = RuleBasedNLU._missing(prev.act, prev.slots)
                    continue
            out.append(f)
        return out

    # ------------------------------------------------------------------
    # 槽位 / Slots
    # ------------------------------------------------------------------

    def _slots(self, text: str, act: Act | None = None) -> dict[str, Any]:
        slots, _ = self._slots_and_residue(text)
        slots.pop("_amount_invalid", None)
        return slots

    def _slots_and_residue(self, text: str) -> tuple[dict[str, Any], str]:
        slots: dict[str, Any] = {}
        norm = _normalize(text)
        low = norm.lower()

        # ---- 先认出核心系统里的真实名字，记下它们占的位置 ----------------
        spans: list[tuple[int, int]] = []
        found: dict[str, list[str]] = {}
        found_at: dict[str, list[tuple[int, str]]] = {}
        for key in sorted(self.vocab, key=len, reverse=True):
            start = 0
            while True:
                i = low.find(key, start)
                if i < 0:
                    break
                j = i + len(key)
                if not any(a < j and i < b for a, b in spans):
                    spans.append((i, j))
                    slot, value = self.vocab[key]
                    found.setdefault(slot, [])
                    if value not in found[slot]:
                        found[slot].append(value)
                        found_at.setdefault(slot, []).append((i, value))
                start = j

        residue = low
        for a, b in sorted(spans, reverse=True):
            residue = residue[:a] + " " + residue[b:]
        residue_amount = re.sub(r"[a-z]+-?\d{3,}", " ", residue)
        residue_amount = re.sub(r"(?<!\d)\d{12,19}(?!\d)", " ", residue_amount)
        residue_amount = re.sub(r"尾号\s*\d{4}", " ", residue_amount)

        def free(i: int, j: int) -> bool:
            return not any(a < j and i < b for a, b in spans)

        # ---- 金额 -------------------------------------------------------
        amount = extract_amount(residue_amount)
        if amount:
            slots["amount"] = amount
        if _NEGATIVE_AMOUNT.search(norm) or (not amount and _ZERO_AMOUNT.search(norm)):
            slots["_amount_invalid"] = True

        # ---- 收款人 -----------------------------------------------------
        literal = re.search(r"(?<!\d)(\d{12,19})(?!\d)", norm)
        if found.get("payee_literal"):
            slots["payee"] = "literal"
            slots["payee_account"] = found["payee_literal"][0]
        elif literal:
            slots["payee"] = "literal"
            slots["payee_account"] = literal.group(1)
        else:
            for hint, key in L.PAYEE_HINTS:
                i = low.find(hint)
                if i >= 0 and free(i, i + len(hint)):
                    slots["payee"] = key
                    break

        # ---- 卡片 -------------------------------------------------------
        if found.get("card"):
            slots["card"] = found["card"][0]
            if len(found["card"]) > 1:
                slots["cards"] = list(found["card"])
        else:
            for hint, key in L.CARD_TYPE_HINTS:
                i = low.find(hint)
                if i >= 0 and free(i, i + len(hint)):
                    slots["card"] = key
                    break

        # ---- 代扣：具体商户名压过泛化类别词 ----------------------------
        # 「取消视频会员和健身房」：一个是真实商户名，一个是类别词——两个都要。
        # 按在句子里出现的先后排，信封上的顺序才和客户说的一致。
        hits: list[tuple[int, str]] = list(found_at.get("mandate", []))
        taken: list[tuple[int, int]] = []
        for hint, key in L.MANDATE_HINTS:
            i = low.find(hint)
            if i >= 0 and free(i, i + len(hint)) and not any(a < i + len(hint) and i < b
                                                             for a, b in taken):
                taken.append((i, i + len(hint)))
                if key not in [k for _, k in hits]:
                    hits.append((i, key))
        mandates = [k for _, k in sorted(hits)]
        if not mandates and L.MANDATE_GENERIC_MEMBER[0] in low and not re.search(r"(的会员|会员费)", low):
            mandates = ["member"]
        if mandates:
            slots["mandate"] = mandates[0]
            if len(mandates) > 1:
                slots["mandates"] = mandates

        # ---- 产品与风险 -------------------------------------------------
        if found.get("product"):
            slots["product"] = found["product"][0]
        for rx, level in L.RISK_HINTS:
            if rx.search(low):
                slots["risk"] = level
                break

        if found.get("account_literal"):
            slots["account_no"] = found["account_literal"][0]

        # ---- 范围与细节 -------------------------------------------------
        if L.SCOPE_ALL_RE.search(low):
            slots["scope_all"] = True
        if re.search(r"(储蓄|定期)", low) and not re.search(r"(利率|利息|定存)", low):
            slots["account"] = "savings"
        elif re.search(r"(活期|工资卡)", low):
            slots["account"] = "current"
        if re.search(r"(单笔|每笔|single)", low):
            slots["limit_which"] = "single"
        elif re.search(r"(日限|每日|每天|daily|per day)", low):
            slots["limit_which"] = "daily"

        for rx, cat in L.STATEMENT_CATEGORY:
            if rx.search(low):
                slots["stmt_category"] = cat
                break
        if L.INCOME_RE.search(low):
            slots["stmt_income"] = True
        if L.LARGEST_RE.search(low):
            slots["stmt_largest"] = True
        if L.RECENT_RE.search(low):
            slots["stmt_recent"] = True
        if L.OTHER_PERIOD_RE.search(low):
            slots["stmt_other_period"] = True
        return slots, residue

    @staticmethod
    def _missing(act: Act, slots: dict[str, Any]) -> list[str]:
        """
        哪些槽位还不够下一步 / What is still needed.

        一次性问清所有缺失项，而不是一轮问一个——「对话轮次效率」是评分项，
        而逐个追问也确实让人烦躁。
        """
        need: list[str] = []
        if act is Act.TRANSFER:
            if "amount" not in slots:
                need.append("amount")
            if "payee" not in slots:
                need.append("payee")
        elif act is Act.INVEST:
            if "amount" not in slots:
                need.append("amount")
        elif act is Act.CANCEL_MANDATE:
            if "mandate" not in slots and not slots.get("scope_all"):
                need.append("mandate")
        elif act in (Act.CARD_FREEZE, Act.CARD_UNFREEZE, Act.CARD_LIMIT):
            if "card" not in slots and not slots.get("scope_all"):
                need.append("card")
            if act is Act.CARD_LIMIT and "amount" not in slots:
                need.append("amount")
        return need


def cancel_mandate_context(low: str, mandate_obj: bool) -> bool:
    """「停掉健身房的扣费」里的「停」说的是代扣，不是卡。"""
    return mandate_obj and not re.search(r"(卡|card)", low)
