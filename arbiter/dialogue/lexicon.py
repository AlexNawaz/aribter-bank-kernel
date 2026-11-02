"""
词汇表 / Lexicon.

理解客户的话需要的所有词，集中在这一个文件里。

原先这些词散落在一张按顺序匹配的正则表里，谁排在前面谁赢。实机试用暴露的
问题几乎都出在这里：「取消冻结」被冻结规则抢走、「信用卡信息」被判成冻结、
「我想投资5000块」被转账规则吃掉、「取消星力健身的会员」指向了视频会员。

新的理解方式不再让一条正则一锤定音。它分别回答四个问题——这句话说的是
**什么东西**（卡、订阅、理财、收款人、账单……）、要对它**做什么**（冻结、解冻、
取消、买、卖、转、查……）、有没有**否定**（取消冻结＝解冻）、是**问句**还是
**祈使句**——再由一张决策表把四个答案组合成意图。这个文件只回答「哪些词
算什么」，决策在 `nlu.py`。

**这一层仍然在不可信侧。** 理解错了的代价是多问一句，或者提议一个客户会拒签
的信封；钱只在客户确认了一份他看过的授权之后才会动，而那由内核按观测到的
效果裁决，不读这里的判断。
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# 整句应答 / Whole-utterance replies
# ---------------------------------------------------------------------------

# 句末语气词：判断「好的」「确认吧」之类整句应答时先剥掉
PARTICLES = "了吧啊呀哦嘛呢啦哈嘞喔滴哟噢"

CONFIRM_WORDS = {
    "确认", "确定", "好", "好的", "好吧", "好嘞", "好滴", "可以", "可以的", "行", "行吧", "行的",
    "是", "是的", "对", "对的", "没错", "嗯", "嗯嗯", "恩", "同意", "没问题", "继续", "执行",
    "办吧", "办", "就这样", "就这样吧", "确认无误", "确认执行", "确认转账", "确认办理", "确认取消",
    "确认冻结", "确认解冻", "确认申购", "确认赎回", "好的确认", "确认好的", "成", "中", "要",
    "要的", "需要", "麻烦了", "那就这样", "那好", "可", "同意执行", "是我本人", "是本人",
    "确实是我", "是我本人的意思", "我确认", "我同意", "批准",
    "ok", "okay", "yes", "yep", "yeah", "yup", "sure", "confirm", "confirmed",
    "go ahead", "do it", "proceed", "yes please", "yes go ahead", "sure go ahead",
    "ok go ahead", "yes confirm", "confirm it", "approve", "approved", "please do",
}

DENY_WORDS = {
    "取消", "算了", "还是算了", "那算了", "不要", "不要了", "不用", "不用了", "不需要",
    "不需要了", "不了", "先不", "先不了", "先不用", "暂时不用", "暂时不要", "现在不用",
    "不", "别", "停", "停下", "撤销", "放弃", "不转了", "不买了", "不冻了", "不解冻了",
    "不取消了", "不办了", "不弄了", "不做了", "不退了", "不赎了", "不调了", "不对不要",
    "否", "拒绝", "不同意", "不是我", "不是我本人", "我没有", "不是我的意思",
    "no", "nope", "nah", "cancel", "stop", "never mind", "nevermind", "dont", "don't",
    "no thanks", "not now", "no cancel", "cancel it", "abort", "decline",
}

THANKS_WORDS = {
    "谢谢", "谢谢你", "谢谢您", "多谢", "感谢", "谢了", "谢啦", "好的谢谢", "好谢谢",
    "非常感谢", "太感谢了", "辛苦了", "谢谢啦", "ok谢谢", "好的谢谢你", "嗯谢谢",
    "thanks", "thank you", "thx", "ty", "thanks a lot", "cheers", "ok thanks", "great thanks",
}
BYE_WORDS = {
    "再见", "拜拜", "拜", "88", "byebye", "回头见", "没事了", "没别的了", "就这些",
    "先这样", "先这样吧", "就这样了", "没有了", "没了", "不用了谢谢", "好的再见",
    "bye", "goodbye", "see you", "that's all", "thats all", "nothing else", "no thats all",
}
BYE_RE = re.compile(r"^(好的?|嗯|那|那就|行)?(先|就)?(这样|这样吧|这些)?(了)?(拜拜|再见|拜|回见|下次聊|bye|goodbye)$")

# 「好的，没别的了，再见」：结束语可以叠着说；至少要有一个真正的结束词
BYE_CHAIN_RE = re.compile(
    r"^(好的?|嗯|那|那就|行|ok|okay)?(谢谢|谢了|多谢)?(先|就)?(这样|这样吧|这些|这样了)?(了)?"
    r"(没别的了?|没有了|没事了|没其他的?了?|没什么了|就这些了?|就这样了?|没别的事了?)?(谢谢|谢了|多谢)?"
    r"(拜拜|再见|拜|回见|下次聊|bye|goodbye)?$")
# 「就这样吧」在信封之后是「就按这个办」——单靠「这样」「这些」不算结束语，要有真正的结束词
BYE_CHAIN_MUST = re.compile(r"(没别的|没有了|没事了|没其他|没什么了|拜拜|再见|拜|回见|下次聊|bye|goodbye)")
# 英文：that's all / nothing else / I'm done，可带 thanks 与 bye
BYE_EN_RE = re.compile(
    r"^(?:(?:ok|okay|alright|right|well|no|nope|great|cool|perfect)\s+)*"
    r"(?:(?:(?:that s|thats|that is|that ll be|that will be)\s+(?:all|it|everything)(?:\s+for\s+(?:now|today))?"
    r"|nothing else|i m done|im done|i am done|all done|we re done)"
    r"(?:\s+(?:thanks|thank you|cheers))?(?:\s+(?:good\s*bye|bye(?:\s*bye)?|see you(?:\s+later)?|see ya|take care))?"
    r"|(?:(?:thanks|thank you|cheers)\s+)?(?:good\s*bye|bye(?:\s*bye)?|see you(?:\s+later)?|see ya|take care"
    r"|have a (?:good|nice|great) (?:day|one|evening)))$")

GREETING_WORDS = {
    "你好", "您好", "你好啊", "嗨", "哈喽", "在吗", "在不在", "有人吗", "早上好", "上午好",
    "中午好", "下午好", "晚上好", "早", "hi", "hello", "hey", "hi there", "good morning",
    "good afternoon", "good evening",
}

HELP_RE = re.compile(
    r"(你能做什么|你会做什么|你会什么|你都会.{0,2}(什么|啥)|会些(什么|啥)|能干什么|能干啥|能干嘛|能做些?(什么|啥)"
    r"|能帮我.{0,4}(什么|啥)|可以帮我.{0,4}什么"
    r"|有什么功能|都有什么功能|怎么用|如何使用|使用说明|帮助|你是干什么的|你能办(哪些|什么)"
    r"|你可以做什么|都能(处理|办)什么|说说你会|你都能帮上什么忙|能帮什么忙"
    r"|what can you do|how do i use|what do you do|capabilit|^help$|^help me$)")

# 改主意：句首出现这些词时，这一轮是在修正上一份授权或上一个问题
CORRECTION_RE = re.compile(
    r"^(不对|错了|说错了|搞错了|不是不是|不是这样|我说错了|我是说|我的意思是|应该是|其实是"
    r"|改成|改为|换成|金额改|收款人改|不对不对|等等|等一下"
    r"|no wait|wait|actually|i meant|i mean|change it to|make it)")

# ---------------------------------------------------------------------------
# 句子之间 / Clause boundaries
# ---------------------------------------------------------------------------

# 英文的改主意：「no, make it 5000」「actually 5000」「send it to my mum instead」
CORRECTION_EN_RE = re.compile(
    r"^(?:(?:no|nope|wait|sorry|oops|hmm)\b[ ,]*)*"
    r"(?:actually|i meant|i mean|make it|change it to|change that to|change the amount to|"
    r"not \d[\d,.]*\s*(?:but|,)|to my|send it to|pay it to)\b"
    r"|^(?:no|nope|wait|sorry|oops)\b[ ,]*(?:(?:no|wait)\b[ ,]*)?(?=[\d¥$])"
    r"|\binstead\b")

CLAUSE_SPLIT = re.compile(
    r"[，,；;。!！？?\n]+|(?:然后|接着|之后|随后|顺便|另外|同时|并且|还有就是|最后|再然后"
    r"|and then|after that|also)")

# 「再」开头的分句：「再看看这个月花了多少」
LEADING_JOINERS = re.compile(r"^(再|还|并|也|又|那|那就|就|然后|接着|帮我再|再帮我|and|then)\s*")

# ---------------------------------------------------------------------------
# 说的是什么 / Objects
# ---------------------------------------------------------------------------

OBJ_BALANCE = re.compile(
    r"(余额|账户余额|还有多少钱|有多少钱|还剩多少|剩多少|还剩几个|还有钱|钱够不够|账上|户头|兜里"
    r"|卡里.{0,4}(钱|多少|余)|卡上.{0,4}(钱|多少|余)|我的账户|账户情况|账户概览|账户信息|账户状况"
    r"|手头.{0,4}(多少|还有)|工资到账|到账了没|余额给我报"
    r"|\bbalance\b|account overview|account information|account info|my accounts?\b"
    r"|how much (money )?(do i have|is left|is in|left|remaining))")

OBJ_STATEMENT = re.compile(
    r"(账单|流水|明细|支出|开支|开销|花销|花费|收支|账目|对账|对一下账|花了多少|花得多|花了不少"
    r"|花在|花到|花哪|花去|用在哪|使在|搭在|消费(记录|情况|明细|构成|习惯|分析)?"
    r"|钱.{0,6}(去|跑|搭|花|使|用).{0,4}(哪|何处|什么地方)|钱.{0,4}怎么没|钱像流水"
    r"|收入|进账|入账|交易记录|最近.{0,4}(交易|花销|消费)|最大(一笔|的一笔)|最贵|上一笔|最近一笔"
    r"|statement|spending|spent|spend\b|expenses?|outgoings?|transactions?|income|breakdown|\bbills?\b"
    r"|where.{0,16}(money|it).{0,12}go|what am i (spending|paying))")

OBJ_CARD = re.compile(
    r"(信用卡|借记卡|储蓄卡|银行卡|工资卡|贷记卡|卡片|这张卡|那张卡|我的卡|名下的卡|卡号|尾号\s*\d{4}"
    r"|手里.{0,2}卡|几张卡|卡(的)?(状态|信息|情况|详情)|\bcards?\b|debit card|credit card)")
CARD_BARE = re.compile(r"卡")          # 「把卡冻了」「卡丢了」——单字「卡」只在有动作时才算

OBJ_MANDATE = re.compile(
    r"(订阅|代扣|自动扣费|自动扣款|自动续费|自动付费|自动付款|自动划|续费|月费|月付|会员|扣费|扣款"
    r"|定期扣|按月扣|每月扣|每个月.{0,4}扣|在扣.{0,2}钱|扣我.{0,2}钱|扣走|划走"
    r"|subscriptions?|subscribed|direct debits?|auto.?pay|auto.?debit|membership|recurring"
    r"|automatic (?:payments?|charges?|debits?|deductions?|renewals?)|standing orders?"
    r"|paying for every month|charged? (me )?(automatically|monthly|every month))")

OBJ_PRODUCT = re.compile(
    r"(理财|基金|产品|净值|持仓|在售|货架|可投|可买|申购的|能买的|\bfunds?\b|investments?|wealth"
    r"|portfolio|holdings?|products?)")

OBJ_PAYEE_LIST = re.compile(
    r"(收款人|联系人|收款方|收款名单|转账名单|常用收款|常转的|存了哪些.{0,6}人|能转.{0,4}(的|给)?(人|谁)"
    r"|(转账|转过账|转过钱|付过款|汇过款|打过钱)的(人|对象|账户)|(转|付|汇)给过(谁|哪些人)"
    r"|(往|给|跟|向)谁.{0,8}(转|汇|打|付)|谁.{0,6}(能收|可以收).{0,4}钱|(能|可以).{0,4}收.{0,6}(转账|钱|款)"
    r"|payees?|beneficiar|saved recipients?|recipient list|address book|who can i (send|transfer))")

OBJ_RATE = re.compile(
    r"(利率|利息|年化|收益率|业绩比较基准|收益是多少|收益多少|什么息|息怎么算|汇率|外汇|牌价|换汇|挂牌价"
    r"|换(美元|欧元|日元|英镑|港币|韩元|卢布)|(美元|欧元|日元|英镑|港币|韩元).{0,6}(价|多少|怎么换|汇)"
    r"|积分|里程|信用分|征信|\binterest\b|deposit rate|savings rate|exchange rate|\bfx\b|\byield\b"
    r"|\bapy\b|\bapr\b|credit score|reward points|air ?miles)")

OBJ_CREDENTIAL = re.compile(
    r"(手机号|手机号码|预留手机|预留号码|预留电话|电话号码|号码|登录密码|支付密码|交易密码|取款密码|密码|邮箱"
    r"|e-?mail|phone( number)?|mobile( number)?|password|passcode|\bpin\b)")

# 信息套取的目标：这些东西智能体在任何授权下都不会说出来、也不会发出去
OBJ_SENSITIVE = re.compile(
    r"(密码|验证码|动态码|短信码|校验码|动态密码|cvv|cvc|cvn|安全码|卡背面.{0,4}(三位|数字)|有效期"
    r"|完整卡号|完整账号|全部卡号|卡号|账号全部|身份证|证件号|证件号码|身份证号"
    r"|password|passcode|\bpin\b|\botp\b|verification code|security code|full card number"
    r"|card number|id number)")
OTP_RE = re.compile(r"(验证码|动态码|短信码|校验码|动态密码|\botp\b|verification code)")

# 把数据发到外面：邮箱、微信、QQ、网址
EXFIL_RE = re.compile(
    r"([\w.+-]+@[\w-]+\.[\w.]+|(发|发送|转发|传|导出).{0,6}(到|给|至).{0,10}(邮箱|微信|qq|QQ|手机|网址|链接|号码|外部|别人)"
    r"|发到.{0,3}[\w.+-]+@|send (it|this|my statement|the statement) to|email (it|me|my statement) to)")

# 别人的资料
THIRD_PARTY_RE = re.compile(
    r"((王建国|李秀兰|房东|妈妈|我妈|别人|他|她|对方|老婆|老公|妻子|丈夫|爱人|媳妇|对象|男朋友|女朋友"
    r"|儿子|女儿|孩子|爸爸|我爸|父亲|爸|哥哥|姐姐|弟弟|妹妹|朋友|同事|邻居|家人|亲戚)"
    r".{0,4}的.{0,6}(身份证|证件|余额|账户|卡号|账号|密码|手机号|电话|地址|流水|账单)"
    r"|\b(my )?(wife|husband|partner|son|daughter|father|mother|dad|mom|friend|colleague|neighbou?r)'s "
    r"(balance|account|card|id|password|statement)"
    r"|(查|看|告诉我).{0,6}(别人|他人|其他人).{0,6}(账户|余额|信息)"
    # 「我老公卡里有多少钱」：「的」可以省，但要紧跟着说的是对方的数据
    r"|(老婆|老公|妻子|丈夫|爱人|媳妇|对象|男朋友|女朋友|儿子|女儿|孩子|爸爸|我爸|父亲|妈妈|我妈|母亲|哥哥|姐姐"
    r"|弟弟|妹妹|朋友|同事|邻居|家人|亲戚|别人|他人)的?.{0,2}(卡里|卡上|账上|账户里|户头|余额|还有多少|有多少钱"
    r"|还剩多少|工资|收入|消费|花了多少|交易记录|转账记录|流水|账单|密码|卡号|账号|身份证))")

# 诈骗话术：出现时要在回答里给风险提示
SCAM_RE = re.compile(
    r"(安全账户|资金核查|配合调查|公检法|冻结.{0,4}资金.{0,4}(转|汇)|系统通知.{0,8}(转|汇)|刷单"
    r"|客服让我|退款需要|解冻费|保证金|中奖|领奖|贷款需要先|验证资金|洗钱"
    r"|safe account|security account)")

LOCATION_RE = re.compile(
    r"((便利店|餐厅|饭店|商场|超市|加油站|医院|酒店|地铁|网点|支行|营业厅|柜台|atm|ATM).{0,8}"
    r"(在哪|哪里|附近|怎么走|地址|营业时间|几点)"
    r"|(最近|附近|周边|离我最近).{0,6}(的)?(支行|网点|营业厅|柜台|atm|ATM|便利店|餐厅|商场|超市|银行))")

OUT_OF_SCOPE_RE = re.compile(
    r"(天气|气温|下雨|下雪|带伞|雨伞|明天.{0,4}穿|空气质量"
    r"|机票|酒店|订餐|外卖|打车|快递|高铁|火车票|电影票|门票|好吃的|吃点什么|吃什么好|美食"
    r"|(股票|个股|大盘).{0,4}(推荐|预测|涨|跌|行情|信息|价格|走势)|实时股票|买(什么|哪只)?股票|炒股|推荐.{0,4}股票"
    r"|哪只股票|币价|比特币|虚拟币|彩票"
    r"|你是谁|什么模型|GPT|大模型|你是.{0,4}(AI|人工智能|机器人)|是不是(AI|人工智能|机器人)|你叫什么"
    r"|写.{0,4}(代码|作业|论文|作文)|翻译|讲个笑话|笑话|电影|电视剧|小说|音乐|唱歌|游戏|球赛|比赛几点"
    r"|贷款|借钱|开户|开个户|销户|注销账户|办卡|申请信用卡|房贷|车贷"
    r"|weather|forecast|temperature|will it rain|flight|hotel|book a table|takeaway|taxi|parcel"
    r"|stock tip|which stock|bitcoin|crypto|lottery|who are you|which model|are you (an? )?(ai|gpt|llm)"
    r"|write (me )?(code|an essay|my homework)|homework|translate|tell me a joke|movie|loan|open an account"
    r"|close my account)")

# 「股票基金」「股票型」是理财产品，不是荐股
PRODUCT_NOT_STOCK = re.compile(r"(股票(型|基金|类基金)|stock (?:fund|type|product)|equity fund)")
# 荐股、炒币、彩票：句子里有「买」也不是理财申购
STOCK_TIP_RE = re.compile(
    r"(哪只股票|什么股票|买(什么|哪只|点|些|个)?股票|炒股|荐股|推荐.{0,4}股票|股票.{0,4}(推荐|预测|行情|走势|代码)"
    r"|(个股|大盘).{0,4}(推荐|预测|涨|跌|行情|走势)|比特币|虚拟币|数字货币|炒币|彩票"
    r"|\bstocks?\b|\bshares?\b|stock tips?|bitcoin|crypto|lottery)")

# ---------------------------------------------------------------------------
# 要做什么 / Actions
# ---------------------------------------------------------------------------

# 解冻：包括「取消冻结」「冻结取消掉」这种用否定词表达的解冻
ACT_UNFREEZE = re.compile(
    r"(解冻|解锁|解开|放开|恢复使用|恢复|重新开通|重新启用|重新打开|重新使用|启用|开回来|激活|打开.{0,2}卡"
    r"|(取消|解除|撤销|去掉|去除|关闭)(?:(?!订阅|扣费|代扣|会员|并|和|再|然后|还).){0,10}(冻结|锁定|挂失|停用|锁卡)"
    r"|(冻结|锁定|挂失|停用).{0,6}(取消|解除|撤销|去掉|打开)"
    r"|\bun-?freeze\b|\bun-?block\b|\bun-?lock\b|\bunsuspend\b|\bthaw\b|reactivate|re-?enable"
    r"|(turn|switch|put).{0,24}\bback on\b"
    # 英文里同样有「取消冻结」：lift / remove / cancel the freeze —— 冻结词出现了，方向却相反
    r"|\b(lift|remove|cancel|undo|release|end|clear|drop)\b(?:(?!\b(?:and|then|also|plus)\b).){0,14}"
    r"\b(freeze|block|lock|hold|suspension)\b"
    r"|\btake\b.{0,10}\b(freeze|block|lock|hold)\b.{0,6}\boff\b|\btake off\b.{0,6}\b(freeze|block|lock|hold)\b"
    r"|\b(freeze|block|lock|hold|suspension)\b.{0,24}\b(lifted|removed|taken off|cancell?ed|undone|released)\b"
    r"|\b(frozen|blocked|locked|on hold)\b.{0,30}\b(lift|remove|undo|cancel|release|clear)\b\s+(it|that|this)\b)")

ACT_FREEZE = re.compile(
    r"(冻结|冻上|冻了|冻住|冻一下|锁卡|锁上|锁住|锁一下|锁了|控制住|停用|挂失|停掉.{0,2}卡|把卡停"
    r"|卡.{0,4}(停掉|停了|停一下|停用|封掉|封了)|把.{0,8}卡.{0,4}(停|封)"
    r"|\bfreeze\b|\bblock\b|\block\b|suspend|disable)")

ACT_FRAUD = re.compile(
    r"(盗刷|被盗|盗用|被刷|被人(用|刷|盗|拿)|让人用|别人(用|刷)|不是我(刷|消费|花|买)的|不是我的消费"
    r"|可疑(扣款|交易|消费)|陌生(扣款|交易|消费)|丢了|丢失|弄丢|丢外面|不见了|找不(到|着)|泄露|泄漏"
    r"|被偷|出问题|有问题|异常交易|异常扣款"
    r"|stolen|fraud|unauthori[sz]ed|compromised|lost my card|someone (used|is using)|without (me|permission)"
    r"|used by someone|by someone else|got used|someone else (used|is using))")

ACT_LIMIT = re.compile(
    r"((额度|限额|上限|日限|单笔限|每日限|每天能刷|单笔能刷).{0,10}(调|改|设|提|降|升|增加|减少|变成|成|到|为|抬|往上|往下)"
    r"|(调|改|设|提|降|升|增加|减少|调高|调低|提高|降低|往上调|往下调|上调|下调).{0,8}(额度|限额|上限)"
    r"|(每天|单笔|每日).{0,6}(能刷|可刷|最多).{0,8}(调|改|少|多|提|太低|太高)"
    r"|\blimit\b)")

ACT_CANCEL = re.compile(
    r"(取消|退订|退掉|退了|退订掉|退个|退一个|退一下|退下|退出|停掉|停了|关掉|关了|关闭|不要了|不想要了"
    r"|不需要了|不续了|不续|不想续"
    r"|别再扣|不要再扣|别扣|别续|别再续|清掉|清理|清一清|理一理|解约|终止|不再扣"
    r"|\bcancel\b|\bstop\b|\bend\b|unsubscribe|get rid of|kill)")

ACT_BUY = re.compile(
    r"(买|购买|申购|认购|投资|投入|投进|投点|投个|投一|投了|投到|配置|加仓|入手|放进去|放到.{0,4}(基金|理财|产品)"
    r"|投\s*[0-9零一二两三四五六七八九十百千万]"
    r"|\bbuy\b|\binvest\b|purchase|put (money|\d+) into|put money in)")

ACT_SELL = re.compile(
    r"(赎回|赎掉|赎了|卖掉|卖了|卖出|清仓|结掉|退出来|取出来|取回|取回来|拿回来|倒回|转回活期|换成现金"
    r"|不想持有|不做了|不想要了|不要了|清掉"
    r"|\bredeem\b|\bsell\b|cash out|liquidate)")

ACT_TRANSFER = re.compile(
    r"(转账|转钱|转给|转到|转去|转一笔|转笔|转.{0,2}过去|转些|转点|汇款|汇钱|汇给|汇过去|汇到|打钱|打款|打给|打过去"
    r"|(?<!自动)付款|付给|付钱|付掉|还钱|还给|送.{0,6}过去|交房租|交电费|交水费|缴费|缴掉|划给"
    r"|\btransfer\b|\bsend\b|\bwire\b|\bremit\b|\bpay\b"
    r"|\bmove\b.{0,12}(money|balance|funds|cash|savings|\d))")
# 「给房东转4500」：动词夹在中间的结构
ACT_TRANSFER_FRAME = re.compile(
    r"((给|向|往|帮).{1,14}(转|汇|打|付|送|划|存))"
    r"|((转|汇|打|付|送|划|存|给).{0,10}(\d{2,}|[零一二两三四五六七八九十百千万]{1,8}(块|元|钱|万|千)))"
    r"|((交|缴|付|结).{0,4}(房租|电费|水费|话费|物业费|水电|燃气费))"
    r"|((存|打|转|汇|送).{0,4}(点|些)(钱)?)")

ACT_QUERY = re.compile(
    r"(查|查询|查看|看看|看一下|看下|看一眼|瞧瞧|瞅瞅|有哪些|有什么|都有|多少|几个|几张|几项|几笔|几只"
    r"|列出|列一下|列一列|报一下|报给我|情况|状态|信息|详情|怎么样|如何|是什么|有没有|啥"
    r"|\bshow\b|\blist\b|\bwhat\b|\bwhich\b|how many|how much|status|\binfo\b|details?)")

ACT_CHANGE = re.compile(
    r"(改|修改|更改|换|更换|变更|更新|重置|重设|重新设|设置|绑定|解绑|忘了|忘记"
    r"|change|reset|update|modify|replace|forgot)")

ACT_DISCLOSE = re.compile(
    r"(告诉我|告诉|发给|发到|发送|提供|报给|报一下|给我看|显示|是多少|是什么|多少|念一下|说一下"
    r"|tell me|send|give me|show me|what is|what's|read out)")

QUESTION_RE = re.compile(
    r"([吗呢么嘛][？?。.!！\s]*$|[？?]|什么|哪|几|多少|谁|有没有|是不是|能不能|可不可以|怎么样|如何|咋样|来着"
    r"|^(what|which|how|is|are|do|does|can|could|any)\b)")

# ---------------------------------------------------------------------------
# 槽位提示 / Slot hints
# ---------------------------------------------------------------------------

PAYEE_HINTS = (
    ("房东王建国", "landlord"), ("房东", "landlord"), ("王建国", "landlord"), ("房租", "landlord"),
    ("老妈", "mother"), ("妈妈", "mother"), ("母亲", "mother"), ("李秀兰", "mother"), ("我妈", "mother"),
    ("给妈", "mother"), ("老娘", "mother"),
    ("国家电网", "utility"), ("电网", "utility"), ("电费", "utility"), ("水电", "utility"),
    ("the landlord", "landlord"), ("my landlord", "landlord"), ("landlord", "landlord"), ("rent", "landlord"),
    ("my mom", "mother"), ("my mum", "mother"), ("my mother", "mother"), ("mom", "mother"),
    ("mum", "mother"), ("mother", "mother"), ("electricity", "utility"), ("electric", "utility"),
    ("power bill", "utility"), ("utility", "utility"),
)

CARD_TYPE_HINTS = (
    ("信用卡", "credit"), ("贷记卡", "credit"), ("credit", "credit"),
    ("借记卡", "debit"), ("储蓄卡", "debit"), ("工资卡", "debit"), ("debit", "debit"),
    ("salary card", "debit"),
)

# 泛化的代扣类别词。只有一句话里没有出现任何具体商户名时才用。
MANDATE_HINTS = (
    ("视频会员", "video"), ("视频", "video"), ("优酷", "video"), ("爱奇艺", "video"), ("腾讯视频", "video"),
    ("健身房", "gym"), ("健身", "gym"), ("星力", "gym"),
    ("云存储", "cloud"), ("网盘", "cloud"), ("云盘", "cloud"),
    ("streaming", "video"), ("video", "video"), ("gym", "gym"), ("fitness", "gym"),
    ("cloud", "cloud"), ("storage", "cloud"),
)
MANDATE_GENERIC_MEMBER = ("会员", "membership")   # 单独的「会员」：唯一一个名字里带会员的那项

RISK_HINTS = (
    (re.compile(r"(保本|稳妥|不想亏|低风险|风险低|谨慎|货币基金|最稳|保守|capital.?protect|safest|lowest risk|money market)"), 1),
    (re.compile(r"(稳健|稳一点|稳一些|稳当|债券|中低风险|stable|steady|conservative|bond|low risk)"), 2),
    (re.compile(r"(平衡|混合|中等风险|balanced|moderate|mixed)"), 3),
    (re.compile(r"(进取|股票型|股票基金|高收益|激进|搏一把|高风险|aggressive|growth|equit|high.?yield|high risk|risky)"), 4),
)

SCOPE_ALL_RE = re.compile(r"(全部|所有|都|统统|一律|全都|每一个|每一项|\ball\b|every|everything)")

STATEMENT_CATEGORY = (
    (re.compile(r"(住房|房租|租金|rent|housing)"), "住房"),
    (re.compile(r"(出行|交通|打车|地铁|公交|加油|高铁|taxi|transport|travel)"), "出行"),
    (re.compile(r"(订阅|会员费|subscription)"), "订阅"),
    (re.compile(r"(餐饮|吃饭|外卖|餐厅|饭钱|吃的|吃上|dining|food|restaurant|eating)"), "餐饮"),
)
INCOME_RE = re.compile(r"(收入|进账|入账|工资|income|salary|earned)")
LARGEST_RE = re.compile(r"(最大(的)?(一笔|那笔)|最贵|花得最多|biggest|largest)")
RECENT_RE = re.compile(r"(最近(的)?一笔|上一笔|最新(的)?一笔|last transaction|most recent)")
OTHER_PERIOD_RE = re.compile(r"(上个月|上月|去年|上周|前几个月|last month|last year)")

# 序数：照着列表回答「第二个」
ORDINAL_RE = re.compile(
    r"^(第\s*([一二三四五六七八九十1-9])\s*(个|位|张|项|只|笔|条|种)?|(最后|末尾)\s*(一|那)?\s*(个|位|张|项|只|条)?"
    r"|(the )?(first|second|third|fourth|last)( one)?|([1-9])(st|nd|rd|th)?( one)?|([1-9])\s*号)$")
CN_ORDINAL = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
EN_ORDINAL = {"first": 1, "second": 2, "third": 3, "fourth": 4, "last": -1}

# 理解客套话时要先剥掉的前缀；不改变意图
FILLER_PREFIX = re.compile(
    r"^(那个|这个|嗯|呃|啊|哦|请问|麻烦|麻烦你|麻烦您|帮我|帮忙|劳驾|你好|您好|我想|我要|我需要|我想要|能不能|可不可以|可以帮我)+")


# 指回前一个分句的代词 / pronouns that point back to the previous clause
PRONOUN_RE = re.compile(r"(它们|它|\bit\b|\bthem\b|\bthat one\b|\bthis one\b)")
