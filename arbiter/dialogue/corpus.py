"""
意图语料 / Intent corpus.

规则模式要求「动词 + 类别词」按某种写法出现。写得再多也会漏——客户换一个
说法就落空，而正则没有「接近」这个概念：要么命中，要么不命中。

这个文件给每个意图准备一组真人会说的话。它不是给正则用的，是给相似度匹配
用的：规则落空时，拿客户这句话和下面这些比一比，最像哪一类就归到哪一类。
这样「我想了解一下我的钱都去哪儿了」不需要被任何一条规则覆盖，也能靠和
「这个月钱都花哪了」的字面重叠找到归属。

写这些句子时刻意保留了口语的毛病：省略、语气词、错别字般的俗写、半句话。
那才是客户真正会打进输入框的东西。

**这一层仍然在不可信侧。** 它可能归错类，代价是多问一句，或者提议一个客户
会拒签的信封——不会是一笔错误的转账。内核读的是效果，不是这里的判断。
"""

from __future__ import annotations

from arbiter.dialogue.nlu import Act

CORPUS: dict[Act, tuple[str, ...]] = {

    Act.TRANSFER: (
        "给房东转4500", "转4500给房东", "帮我转账", "我要转钱",
        "打钱给房东", "付一下房租", "把房租付了", "这个月房租该交了",
        "汇5000到我妈账户", "给我妈打点钱", "转两千给妈妈", "给妈妈汇款",
        "交一下电费", "电费该交了", "把电费缴了", "交水电费",
        "我想给人转账", "帮我把钱转过去", "转笔钱出去", "付款给收款人",
        "给王建国转钱", "转账到我的常用收款人", "把这笔钱打过去",
        "我要付一笔钱", "需要转一笔账", "帮我汇一笔钱",
        "转三千块", "转个账", "给他打两千", "把钱汇过去",
        "send money to the landlord", "transfer money", "pay my rent",
        "i need to make a transfer", "wire 5000 to my mother",
        "pay the electricity bill", "move money to my landlord",
    ),

    Act.ANALYSE_BILLS: (
        "这个月钱都花哪了", "我想查询账户明细", "看看我的账单",
        "这个月花了多少钱", "我的钱都去哪了", "钱花到哪里去了",
        "帮我分析一下支出", "看看消费情况", "这个月开销怎么样",
        "我想了解一下我的钱都去哪儿了", "最近花钱有点多，看看",
        "查一下流水", "交易明细给我看看", "收支明细",
        "我这个月是不是花超了", "帮我看看花销", "消费记录",
        "都有哪些支出", "这段时间花了多少", "账单分析一下",
        "我想知道钱花在什么地方了", "最近的消费都有什么",
        "对一下账", "看看这个月的收支", "花费情况怎么样",
        "我想看看自己的消费习惯", "钱是怎么没的",
        "where did my money go", "show me my statement",
        "how much did i spend this month", "break down my spending",
        "what did i spend money on", "my transactions please",
    ),

    Act.BALANCE: (
        "工资到账了没", "户头上什么情况", "账户里现在多少钱", "钱够不够",
        "看一眼余额", "账上情况", "户头还有多少", "我的钱还剩多少",
        "check my balance", "what is my balance",
        "我还有多少钱", "查一下余额", "我的账户", "账户里还剩多少",
        "余额是多少", "我卡里还有多少", "看看我的账户情况",
        "账上还有多少钱", "现在有多少钱", "我有多少存款",
        "账户概览", "我的账户情况", "还剩多少",
        "my balance", "my account", "how much do i have",
        "what's left in my account", "account overview",
    ),

    Act.INVEST: (
        "帮我挑个风险低的产品买了", "闲钱搁着不划算，买点什么吧",
        "挑一只合适的买了", "拿两万出来做投资", "买入一只基金",
        "想买点理财", "我要买基金", "帮我申购理财产品",
        "有点闲钱想投资", "想做点投资", "买两万的理财",
        "想买点稳健的，投2万", "帮我配置一下资产",
        "我想理财", "钱放着也是放着，买点产品吧",
        "申购一只基金", "投点钱进去", "想买个保本的",
        "帮我买理财产品", "我想投资基金", "买点低风险的",
        "存点钱到理财里", "把钱投到基金里",
        "i want to invest", "buy a fund for me",
        "put 20000 into something stable", "purchase a wealth product",
    ),

    Act.REDEEM: (
        "手上那只基金清掉", "把理财里的钱倒回活期", "理财的钱退出来",
        "持仓清一清", "把产品结束掉", "不想持有了，退了吧",
        "把买的基金处理掉", "理财转回活期",
        "把理财赎回", "我要赎回基金", "卖掉我的理财",
        "把投的钱取出来", "理财能取出来吗，取了吧",
        "赎回产品", "把基金卖了", "我想把投资的钱拿回来",
        "提前赎回", "把持仓卖掉",
        "redeem my fund", "sell my investment", "cash out my holdings",
    ),

    Act.LIST_PRODUCTS: (
        "理财这块你们提供什么", "能买的产品都有什么", "你们卖什么理财",
        "有什么可选的基金", "产品都有哪几只", "可投的有哪些",
        "理财货架上有什么", "都能买到什么产品",
        "有哪些理财产品", "你们有什么产品", "理财产品列表",
        "看看有什么可以买的", "都有哪些基金", "产品目录",
        "介绍一下你们的理财", "有什么推荐的产品",
        "我能买什么", "有哪些产品可以选", "给我看看产品清单",
        "现在有什么在卖", "理财都有哪些选择",
        "what funds do you have", "show me your products",
        "which products are available", "list the wealth products",
    ),

    Act.CARD_FREEZE: (
        "我的卡好像被盗刷了", "卡丢了", "冻结我的信用卡",
        "把卡锁了", "卡不见了赶紧停掉", "有人盗刷我的卡",
        "卡被偷了", "信用卡挂失", "我要锁卡",
        "刚收到一笔不是我消费的扣款", "卡片停用",
        "我的卡可能泄露了", "先把卡冻上", "卡有问题，先停了",
        "出现了可疑扣款", "卡片安全有问题",
        "my card was stolen", "freeze my card", "block my credit card",
        "there's a charge i didn't make", "i lost my card",
    ),

    Act.CARD_UNFREEZE: (
        "那张冻着的卡放开吧", "把锁住的卡解开", "卡解开吧",
        "冻结的那张放开", "让卡重新能用",
        "解冻我的信用卡", "把卡解锁", "恢复卡片使用",
        "卡能用了吗，解冻吧", "把冻结的卡打开", "启用我的卡",
        "解除卡片冻结", "卡找回来了，解冻",
        "unfreeze my card", "unlock my credit card", "reactivate the card",
    ),

    Act.CARD_LIMIT: (
        "把额度调高", "信用卡限额改一下", "调整一下刷卡额度",
        "日限额太低了", "提高我的额度", "把单笔限额降下来",
        "额度调到一万", "限额改成5000", "调低额度",
        "raise my card limit", "change the daily limit", "lower my limit",
    ),

    Act.LIST_MANDATES: (
        "每个月自动划走的都有哪些", "我订了些什么服务在扣钱",
        "有哪些定期扣款", "每月固定扣的是什么", "自动划账都有哪几笔",
        "按月扣的费用有哪些", "什么在按月扣我的钱",
        "what am i paying for every month", "which services charge me monthly",
        "有哪些自动扣费", "我订阅了什么", "看看代扣",
        "每个月都在扣什么钱", "有什么自动续费", "订阅列表",
        "我都开通了哪些扣费", "自动扣款有哪些", "哪些在定期扣我钱",
        "看看哪些可以取消", "都在扣什么费用", "有多少个订阅",
        "what subscriptions do i have", "list my direct debits",
        "what's being charged automatically",
    ),

    Act.CANCEL_MANDATE: (
        "把健身房的自动扣费退了", "取消视频会员", "帮我把没用的订阅清一清",
        "退订健身房", "不想要这个会员了", "停掉云存储的扣费",
        "把这个订阅关了", "取消自动续费", "健身房那个不要了",
        "别再扣我钱了", "把代扣停掉", "退掉一个订阅",
        "这个会员不续了", "取消掉用不上的扣费",
        "cancel the gym subscription", "stop the auto payment",
        "unsubscribe from the video membership",
    ),

    Act.LIST_PAYEES: (
        "我能往谁那儿转钱", "转账能转给哪些人", "能收我钱的有谁",
        "收款名单给我看看",
        "我的收款人有哪些", "看看收款人名单", "都能转给谁",
        "常用收款人列表", "我存了哪些联系人", "收款方有谁",
        "show my payees", "who can i transfer to", "my beneficiary list",
    ),

    Act.LIST_CARDS: (
        "我有几张卡", "看看我的卡", "名下的卡片",
        "我都有什么卡", "卡片列表", "我的银行卡情况",
        "how many cards do i have", "list my cards", "show my cards",
    ),

    Act.NO_DATA: (
        "三年定期什么息", "今天日元怎么换", "欧元什么价", "存三年利息多少",
        "定期存款的息怎么算", "换汇的价格", "挂牌价是多少",
        "有新的存款利率吗", "现在的利率是多少", "定期存款利息多少",
        "汇率是多少", "美元汇率", "我有多少积分",
        "年化收益率是多少", "业绩比较基准", "我的信用分",
        "贷款利率现在怎么样", "存款利息怎么算",
        "what's the interest rate", "deposit rates", "exchange rate today",
        "how many reward points do i have",
    ),

    Act.CREDENTIAL: (
        "改一下预留手机号", "换绑手机", "修改登录密码",
        "重置支付密码", "更新我的邮箱", "手机号换了要改",
        "绑定新的手机号码", "密码忘了要改",
        "change my phone number", "reset my password", "update my email",
    ),

    Act.HELP: (
        "你能做什么", "你会什么", "有什么功能",
        "怎么用", "能帮我干什么", "你都能处理什么业务",
        "帮助", "我可以让你做什么",
        "what can you do", "help", "what are your capabilities",
    ),

    Act.OUT_OF_SCOPE: (
        "今天天气怎么样", "帮我订张机票", "讲个笑话",
        "你是什么模型", "推荐一只股票", "帮我写代码",
        "明天穿什么", "附近有什么好吃的", "翻译一下这句话",
        "我想贷款", "怎么开户", "能不能帮我销户",
        "比特币现在多少钱", "帮我写作业",
        "what's the weather", "book me a flight", "tell me a joke",
        "which stock should i buy", "write my homework",
    ),
}


def pairs() -> list[tuple[str, Act]]:
    """语料展开成 (句子, 意图) 对 / the corpus as flat labelled examples."""
    return [(text, act) for act, texts in CORPUS.items() for text in texts]


def size() -> int:
    return sum(len(v) for v in CORPUS.values())
