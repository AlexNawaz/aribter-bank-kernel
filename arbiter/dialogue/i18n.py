"""
双语文案 / Bilingual message catalogue.

对话层说出口的每一句固定文案都在这里，中文和英文成对出现。集中放的理由很实际：
一个测试就能检查每一条都有两种语言，而散落在代码里的字符串做不到这一点——上一版
就是这样漏掉的，客户用英文问，收到的回执却是中文。

带数据的部分（金额、商户名、卡号）由调用方用 format 填进来。商户名、账户昵称这些
取自银行核心系统的数据保持原样，不翻译：翻译存储的数据等于编造它。
"""

from __future__ import annotations

from typing import Any

MESSAGES: dict[str, tuple[str, str]] = {
    # ---- 寒暄 / social ----------------------------------------------------
    "greeting": (
        "您好，{name}。我可以帮您转账、分析账单、管理理财和卡片，以及处理自动扣费。您想做什么？",
        "Hello, {name}. I can help with transfers, statements, wealth products, cards and "
        "direct debits. What would you like to do?"),
    "thanks": ("不客气。还有什么需要我做的吗？", "You're welcome. Anything else I can do?"),
    "bye": ("好的，再见。有需要随时找我。", "Goodbye — I'm here whenever you need me."),
    "anything_else": ("还有什么需要我做的吗？", "Anything else I can do?"),

    # ---- 能力与兜底 / capabilities and fallbacks --------------------------
    "help": (
        "我可以帮您做这些事：\n"
        "· 转账——「给房东转 4500」\n"
        "· 账单——「这个月钱都花哪了」「餐饮花了多少」\n"
        "· 理财——「有哪些理财产品」「买点稳健的，投 2 万」「把理财赎回」\n"
        "· 卡片——「我的卡好像被盗刷了」「解冻信用卡」「借记卡单笔限额调到 3000」\n"
        "· 自动扣费——「有哪些自动扣费」「把健身房的退了」\n"
        "一句话里说几件事也可以，比如「查一下余额，然后给房东转 4500」。\n\n"
        "动钱的操作我都会先把授权范围列出来请您确认，确认之后才执行；"
        "超出范围的部分我会拒绝并说明原因。",
        "Here's what I can do:\n"
        "· transfers — \"transfer 4500 to the landlord\"\n"
        "· statements — \"where did my money go this month\", \"how much on dining\"\n"
        "· wealth — \"what funds do you have\", \"invest 20000 in something stable\", "
        "\"redeem my fund\"\n"
        "· cards — \"my card was stolen\", \"unfreeze my credit card\"\n"
        "· direct debits — \"what subscriptions do I have\", \"cancel the gym one\"\n"
        "You can ask for several things at once: \"check my balance, then transfer 4500 "
        "to the landlord\".\n\n"
        "Anything that moves money, I first show you the exact authorisation and wait for "
        "you to approve it; anything outside it I refuse, and I tell you why."),
    "unknown": (
        "这句话我没太听明白。我能做的是这些，您怎么说都行：\n"
        "· 转账——「给房东转 4500」\n"
        "· 账单——「这个月钱都花哪了」\n"
        "· 理财——「有哪些理财产品」「买点稳健的，投 2 万」\n"
        "· 卡片——「我的卡好像被盗刷了」「我有几张卡」\n"
        "· 自动扣费——「有哪些自动扣费」「把健身房的退了」\n\n"
        "这之外的事——利率、贷款、开户、改手机号——我会直接告诉您我不做，"
        "而不是猜一个答案给您。",
        "I didn't catch that one. Here's what I can actually do — say it any way you like:\n"
        "· transfers — \"transfer 4500 to the landlord\"\n"
        "· statements — \"where did my money go this month\"\n"
        "· wealth — \"what funds do you have\" / \"invest 20000 in something stable\"\n"
        "· cards — \"my card was stolen\" / \"how many cards do I have\"\n"
        "· direct debits — \"what subscriptions do I have\" / \"cancel the gym one\"\n\n"
        "Anything outside that — rates, loans, opening accounts, changing your phone "
        "number — I'll tell you plainly rather than guess."),
    "released": ("刚才那件事先放下，稍后可以再说。\n\n",
                 "I've set that aside for now — we can come back to it.\n\n"),
    "out_of_scope": (
        "这个我帮不上忙——我只负责您在本行账户内的操作。\n"
        "我能做的是：转账、账单分析、理财申购与赎回、卡片冻结、解冻与额度、以及自动扣费的查看和取消。",
        "That one's outside what I do — I only handle operations inside your accounts at "
        "this bank.\nThat means transfers, statement analysis, buying and redeeming wealth "
        "products, freezing, unfreezing and limits on cards, and viewing or cancelling "
        "direct debits."),
    "no_data": (
        "这个数字我给不了。利率、汇率、积分这些不在您的账户数据里，"
        "我不会报一个自己读不到的数——一个看起来很合理的利率比没有利率更糟。\n"
        "请以本行官网公告或网点为准。\n\n"
        "我能回答的都来自您的账户本身：余额、账单、理财持仓、卡片状态和自动扣费。",
        "I don't have that figure. Rates, FX quotes and reward points aren't held in your "
        "account record, and I won't quote a number I can't read from it — a "
        "plausible-looking rate is worse than no rate.\nPlease take those from the bank's "
        "published notices or a branch.\n\nWhat I can answer comes straight out of your "
        "account: balances, statements, wealth holdings, card status and direct debits."),
    "credential": (
        "手机号、邮箱和密码的变更我不能代办——这类操作是账户被接管时的第一步，"
        "所以无论您怎么授权，我都不会执行。\n"
        "请您本人通过手机银行的「安全中心」或到网点办理。\n\n"
        "如果您是因为收到了什么提示才想改，先别急着操作，可以先告诉我，我帮您看看。",
        "Changing your phone number, e-mail or password isn't something I'll do for you — "
        "that is the first step in an account takeover, so I refuse it no matter how you "
        "authorise it.\nPlease do it yourself in the bank's app under Security, or at a "
        "branch.\n\nIf you're changing it because of something you received, don't act on "
        "that yet — tell me about it and I'll take a look."),

    # ---- 信息套取 / information extraction --------------------------------
    "sensitive": (
        "密码、完整卡号、证件号这类信息，我在对话里不会显示，也不会发给任何人——"
        "包括您本人：对话记录可能被别人看到。\n"
        "需要查看的话，请在手机银行里验证身份后查看；密码忘了请通过「安全中心」重置。",
        "I never show or send passwords, full card numbers or ID numbers in a conversation "
        "— not even to you, because a chat log can be seen by someone else.\nTo view them, "
        "verify your identity in the bank's app; if you've forgotten a password, reset it "
        "under Security."),
    "sensitive_otp": (
        "验证码只能您自己在本人操作的页面里填写，任何人向您要验证码都是诈骗——"
        "包括自称客服、警察或银行工作人员的人。银行永远不会索要验证码。\n"
        "我这里也不会读取或转告验证码。如果您已经把验证码告诉了别人，"
        "请马上告诉我，我可以先帮您冻结卡片。",
        "A verification code is only ever typed by you, on a page you opened yourself. "
        "Anyone asking you for one is a scammer — including people claiming to be support "
        "staff, police or the bank. The bank never asks for it.\nI won't read or pass on "
        "codes either. If you've already given a code to someone, tell me now and I can "
        "freeze your card first."),
    "sensitive_exfil": (
        "我不会把您的账户信息发到任何外部地址——邮箱、微信、网址都不行。"
        "我没有这个能力，这是刻意的：一个能把数据发出去的智能体，也能被骗着把数据发出去。\n"
        "账单可以直接在这里看，说一声「这个月钱都花哪了」就行。",
        "I won't send your account information to any outside address — e-mail, messaging "
        "apps, links. I don't have that capability, on purpose: an assistant that can send "
        "data out can be tricked into sending it out.\nYou can read your statement right "
        "here — just ask \"where did my money go this month\"."),
    "sensitive_third_party": (
        "别人的证件号、余额和账户信息我不能提供——我只能看到并处理您本人的账户。",
        "I can't give out anyone else's ID number, balance or account details — I can only "
        "see and act on your own account."),

    # ---- 应答 / replies to confirm, deny, correct -------------------------
    "nothing_pending": ("目前没有待确认的操作。您想做什么？",
                        "There's nothing waiting for confirmation. What would you like to do?"),
    "go_ahead_what": ("好的，您想做什么？", "Sure — what would you like to do?"),
    "denied_done": ("好的，已取消，没有执行任何操作。", "OK — cancelled. Nothing was executed."),
    "denied_offer": ("好的，那就先不用。还有什么需要我做的吗？",
                     "OK, no problem. Anything else I can do?"),
    "denied_nothing": ("好的。还有什么需要我做的吗？", "OK. Anything else I can do?"),
    "stale_digest": (
        "这份授权已经失效了，没有执行。如果还要做，请再说一次要做的事。",
        "That authorisation is no longer valid, so nothing was executed. If you still want "
        "it, just ask again."),
    "withdrawn": ("（刚才那份「{goal}」的授权已作废，没有执行。）\n\n",
                  "(The authorisation for \"{goal}\" has been withdrawn — nothing was "
                  "executed.)\n\n"),
    "reprompt": (
        "刚才那份授权还在等您确认：要执行请回复「确认」，不执行请回复「取消」。",
        "That authorisation is still waiting for you: reply \"confirm\" to go ahead, or "
        "\"cancel\" to drop it."),
    "correction_need": ("好的，要改成什么？请直接说新的金额或收款人。",
                        "Sure — what should I change? Just tell me the new amount or payee."),
    "correction_none": ("好的，请再说一次要做的事。", "Sure — tell me again what you'd like to do."),
    "amended": ("已按您的修改更新。", "Updated as you asked. "),

    # ---- 追问 / clarifying questions --------------------------------------
    "ask_prefix": ("好的——请问{questions}？", "Sure — {questions}?"),
    "ask_plain": ("请问{questions}？", "{questions}?"),
    "reask_prefix": ("抱歉，我没听清。", "Sorry, I didn't catch that. "),
    "q_amount": ("转多少钱", "how much should I send"),
    "q_payee": ("转给谁", "who should I send it to"),
    "q_mandate": ("要取消哪一项", "which one should I cancel"),
    "q_card": ("是哪张卡", "which card is it"),
    "q_invest_amount": ("打算投多少", "how much would you like to invest"),
    "q_limit_amount": ("额度调整到多少", "what should the new limit be"),
    "q_redeem": ("要赎回哪一只", "which holding should I redeem"),
    "q_join": ("、", ", and "),
    "opt_payees": ("您的常用收款人：", "Your saved payees:"),
    "opt_mandates": ("当前的自动扣费：", "Your direct debits:"),
    "opt_cards": ("您名下的卡：", "Your cards:"),
    "opt_holdings": ("您持有的产品：", "Your holdings:"),
    "opt_hint": ("（也可以直接回答「第一个」「第二个」）",
                 "(You can also just say \"the first one\", \"the second one\".)"),
    "confirm_guess": ("您是想{what}吗？是的话我来准备，不是的话您再说一遍就行。",
                      "Did you want to {what}? Say yes and I'll set it up, or just tell me "
                      "again in your own words."),
    "bare_amount": ("{money}——您是想转账，还是申购理财？",
                    "{money} — would you like to transfer it, or invest it?"),
    "amount_invalid": ("金额需要大于零。", "The amount has to be more than zero."),
    "g_transfer": ("转一笔账", "make a transfer"),
    "g_invest": ("申购理财", "buy a wealth product"),
    "g_redeem": ("赎回理财", "redeem a holding"),
    "g_cancel": ("取消一项自动扣费", "cancel a direct debit"),
    "g_freeze": ("冻结卡片", "freeze a card"),
    "g_unfreeze": ("解冻卡片", "unfreeze a card"),
    "g_limit": ("调整卡片额度", "change a card limit"),

    # ---- 信封 / envelopes --------------------------------------------------
    "envelope_intro": ("我准备这样做：{goal}\n\n请您确认以下授权范围（确认后我才会执行）：",
                       "Here's what I'll do: {goal}\n\nPlease check the authorisation below — "
                       "nothing happens until you confirm:"),
    "envelope_intro_multi": (
        "我准备这样做（一共 {n} 步，您签一次就行）：\n{steps}\n\n请您确认以下授权范围（确认后我才会执行）：",
        "Here's what I'll do ({n} steps, one approval covers them all):\n{steps}\n\nPlease "
        "check the authorisation below — nothing happens until you confirm:"),

    # ---- 结果 / results ----------------------------------------------------
    "done": ("已完成：", "Done:"),
    "done_nothing": ("已完成。", "Done."),
    "refused": ("这一步我没有执行。{reason}\n\n{suggestion}", "I didn't do that. {reason}\n\n{suggestion}"),
    "go_on": ("\n\n要继续吗？", "\n\nShall I go ahead?"),
    "plan_changed": ("计划在确认过程中发生了变化，为安全起见我停下了。请重新说一次。",
                     "The plan changed while waiting for your approval, so I stopped. Please "
                     "ask again."),
    "expired_renewed": ("刚才那份授权在您确认之前已经过期，没有执行。我按同样的内容重新准备了一份：",
                        "That authorisation expired before you confirmed it, so nothing was "
                        "executed. Here it is again:"),
    "exec_reason_balance": ("余额不足", "insufficient balance"),
    "exec_reason_core": ("核心系统拒绝了这次操作", "the core banking system rejected it"),
    "exec_error": ("执行时出了问题，没有完成：{err}", "Something went wrong and nothing was completed: {err}"),

    # ---- 没有可做的 / nothing to do ----------------------------------------
    "noop_card_frozen": ("{card} 已经是冻结状态，不需要再操作。", "{card} is already frozen — nothing to do."),
    "noop_card_active": ("{card} 本来就是正常状态，不需要解冻。",
                         "{card} is already active — no need to unfreeze it."),
    "noop_mandate_gone": ("没有找到「{name}」这项自动扣费，它可能已经取消了。现在的自动扣费是：{items}",
                          "I can't find a direct debit for \"{name}\" — it may already be "
                          "cancelled. Your current direct debits: {items}"),
    "noop_no_mandates": ("您目前没有任何自动扣费。", "You have no direct debits."),
    "noop_no_holdings": ("您目前没有可赎回的理财持仓。", "You don't hold any wealth products to redeem."),
    "noop_below_min": ("「{name}」起购金额是 {min}，{amount} 不够起购。",
                       "\"{name}\" has a minimum of {min}; {amount} is below it."),
    "noop_insufficient": ("活期账户可用余额 {balance}，不够{what} {amount}。",
                          "Your current account has {balance} available — not enough to "
                          "{what_en} {amount}."),
    "noop_over_grade": (
        "「{name}」（{code}）的风险等级是{risk}，高于您的评级（风险测评结果：{grade}），"
        "按适当性管理的要求我不能为您购买。",
        "\"{name}\" ({code}) is rated {risk_en}, above your assessed grade ({grade_en}), so "
        "suitability rules don't allow me to buy it for you."),
    "alt_offer": ("在您的评级内我可以买「{name}」，要这样吗？",
                  "Within your grade I could buy \"{name}\" instead — shall I?"),
    "below_min_offer": ("要改成按起购金额 {min} 申购吗？",
                        "Shall I make it the minimum, {min}, instead?"),
    "insufficient_hint": ("您可以改成一个不超过可用余额的金额。",
                          "You could choose an amount within your available balance instead."),
    "redeem_which": ("您持有不止一只产品，要赎回哪一只？{items}",
                     "You hold more than one product — which should I redeem? {items}"),

    # ---- 风险提示 / risk warnings typed by the customer --------------------
    "scam_warning": (
        "⚠ 先提醒您：「安全账户」「资金核查」「配合调查」都是诈骗话术。银行、公安、客服都不会"
        "让您把钱转到所谓的安全账户。如果有人在电话或短信里这样要求您，请马上挂断，拨打银行官方"
        "客服核实。",
        "⚠ First, a warning: \"safe account\", \"fund verification\" and \"assisting an "
        "investigation\" are scam scripts. No bank, police force or support line will ever ask "
        "you to move money to a so-called safe account. If someone on the phone or by text is "
        "asking you to, hang up and call the bank's official number."),
    "scam_no_payee": (
        "我不会把钱转到一个说不出名字的「安全账户」。如果您确实要转账给某个人，"
        "请告诉我收款人和金额。",
        "I won't send money to an unnamed \"safe account\". If you do want to pay someone, "
        "tell me who and how much."),

    # ---- 动作说明（用于信封事项、作废提示）/ step descriptions --------------
    "step_transfer": ("向{name}转账 {amount}", "Transfer {amount} to {name}"),
    "step_invest": ("申购 {amount} 的{what}", "Invest {amount} in {what}"),
    "step_invest_ceiling": ("，风险等级不超过 {grade}（{label}）", " at risk grade {grade} or below"),
    "step_redeem": ("赎回「{name}」{amount}，资金回到活期账户", "Redeem {amount} of \"{name}\" back to the current account"),
    "step_cancel": ("取消「{name}」的自动扣费", "Cancel the direct debit for \"{name}\""),
    "step_freeze": ("冻结卡片 {card}", "Freeze card {card}"),
    "step_unfreeze": ("解冻卡片 {card}", "Unfreeze card {card}"),
    "step_limit": ("将卡片 {card} 的{which}限额调整为 {amount}", "Set the {which_en} limit on card {card} to {amount}"),
    "what_product": ("理财产品", "a wealth product"),
    "which_single": ("单笔", "single"),
    "which_daily": ("日", "daily"),

    # ---- 回执 / receipts ---------------------------------------------------
    "r_out": ("从 {acct} 支出 {amount}", "{amount} paid out of {acct}"),
    "r_in": ("{acct} 收到 {amount}", "{amount} credited to {acct}"),
    "r_cancel": ("已取消「{name}」的自动扣费", "Cancelled the direct debit for \"{name}\""),
    "r_mandate_new": ("已为「{name}」开通代扣，上限 {cap}", "Set up a direct debit for \"{name}\", capped at {cap}"),
    "r_frozen": ("卡片 {card} 已冻结", "Card {card} is now frozen"),
    "r_unfrozen": ("卡片 {card} 已解冻", "Card {card} is now active again"),
    "r_limit": ("卡片 {card} 的{control}已调整为 {value}", "The {control} on card {card} is now {value}"),
    "r_transfer": ("已向{name}转账 {amount}", "Sent {amount} to {name}"),
    "r_balance": ("活期账户余额：{balance}", "Current account balance: {balance}"),
    "r_receipt": ("回单号：{ids}", "Receipt no.: {ids}"),
    "ctl_daily_limit": ("日限额", "daily limit"),
    "ctl_single_limit": ("单笔限额", "single-payment limit"),
    "ctl_online_payment": ("线上支付", "online payments"),
    "r_buy": ("已申购「{name}」{amount}", "Bought {amount} of \"{name}\""),
    "r_sell": ("已赎回「{name}」{amount}", "Redeemed {amount} of \"{name}\""),

    # ---- 引用说明 / what a bare name refers to ----------------------------
    "ref_card": ("{card} 是您的{kind}，{state}。\n我可以帮您{action}，或者调整它的额度。您想做哪一样？",
                 "{card} is your {kind_en}, {state_en}.\nI can {action_en}, or change its "
                 "limits. Which would you like?"),
    "ref_mandate": ("「{name}」是一项自动扣费，{cap}/月，下次扣费 {next}。\n要帮您取消吗？",
                    "\"{name}\" is a direct debit of {cap} a month, next charged {next}.\n"
                    "Would you like me to cancel it?"),
    "ref_product": ("{name}（{code}）·{risk}·起购 {min}。\n您打算投多少？",
                    "{name} ({code}) · {risk_en} · minimum {min}.\nHow much would you like to invest?"),
    "ref_payee": ("{name} 是您的常用收款人，{bank} {acct}。\n您想转多少？",
                  "{name} is a saved payee at {bank}, {acct}.\nHow much would you like to send?"),
    "card_debit": ("借记卡", "debit card"),
    "card_credit": ("信用卡", "credit card"),
    "state_frozen": ("目前已冻结", "currently frozen"),
    "state_active": ("目前正常", "active"),
    "action_freeze": ("冻结", "freeze it"),
    "action_unfreeze": ("解冻", "unfreeze it"),
}


def t(key: str, lang: str = "zh", **kw: Any) -> str:
    zh, en = MESSAGES[key]
    template = en if lang == "en" else zh
    return template.format(**kw) if kw else template
