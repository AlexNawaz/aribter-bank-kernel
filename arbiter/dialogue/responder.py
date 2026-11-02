"""
把内核的裁决翻译成客户听得懂的话 / Turning kernel findings into plain language.

内核输出的是结构化发现：`INV_MAX_TOTAL_OUT`、`GRD_CLAIM_MISMATCH`、
`KRN_LOOKALIKE`。客户需要的是一句人话，外加「那我现在能怎么办」。

这一层只读内核已经做出的决定，不参与决定本身。它改写不了任何裁决——它只是把
裁决说清楚，用客户正在用的那种语言。错误恢复之所以能做得具体，是因为内核给出的
理由本身就是具体的：它知道超了哪条限制、超了多少，所以建议也能是具体的。
"""

from __future__ import annotations

from dataclasses import dataclass

from arbiter.types import Decision, Tier, cny


@dataclass
class Recovery:
    """一条拒绝的说明，以及客户接下来能做什么 / A refusal, and a way forward."""
    reason: str
    suggestion: str


# 代码 → (中文说明, 中文建议, 英文说明, 英文建议)
_EXPLAIN: dict[str, tuple[str, str, str, str]] = {
    'INV_MAX_TOTAL_OUT': (
        '这笔操作的总支出超过了您刚才授权的额度。',
        '如果确实要转这笔钱，我可以重新拟一份额度更高的授权请您确认；或者您也可以先转一部分。',
        'This would spend more in total than you just authorised.',
        'If you do want to send this much, I can draft a new authorisation with a higher limit for you to approve — or you can send part of it now.'),
    'INV_MAX_SINGLE_OUT': (
        '单笔金额超过了您授权的单笔上限。',
        '可以分几笔转，或者重新授权一个更高的单笔额度。',
        'A single payment would exceed the per-payment limit you authorised.',
        'You can split it into several payments, or authorise a higher single limit.'),
    'INV_PAYEE_NOT_ALLOWED': (
        '收款人不在您这次授权的名单里。',
        '如果这个收款人是您认识的，我可以重新拟一份包含他的授权；如果不认识，建议先核实对方身份。',
        "The payee isn't on the list you authorised this time.",
        "If you know this payee, I can draft an authorisation that includes them; if you don't, check who they are first."),
    'INV_NEW_PAYEE': (
        '这次授权不允许新增收款人。',
        '向新收款人付款需要单独授权，我可以帮您拟一份。',
        "This authorisation doesn't allow adding a new payee.",
        'Paying someone new needs its own authorisation — I can draft one.'),
    'INV_NEW_MANDATE': (
        '这次授权不包含「建立长期扣款」这一项。',
        '开通代扣是一项独立的许可，需要您单独确认额度上限。',
        "This authorisation doesn't cover setting up a standing payment.",
        'A direct debit is a separate permission and needs its own cap, approved by you.'),
    'INV_UNCAPPED_MANDATE': (
        '这会给对方一个没有上限的扣款授权，意味着他之后可以随时划走任意金额。',
        '我可以改成带上限的授权，比如每月不超过某个金额。无上限的授权我不会执行。',
        'This would give the merchant an uncapped right to take money — any amount, any time.',
        "I can set it up with a cap instead, say a monthly maximum. An uncapped mandate I won't execute."),
    'INV_MANDATE_CAP': (
        '代扣额度超过了您授权的上限。',
        '可以调低额度，或者重新授权一个更高的上限。',
        'The direct debit cap is above what you authorised.',
        'Lower the cap, or authorise a higher one.'),
    'KRN_SUITABILITY': (
        '这只产品的风险等级高于您在我行的风险测评结果，按适当性管理的要求不能向您销售。',
        '我可以帮您挑风险等级相符的产品。如果您确实想买更高风险的，需要先到网点或 App 重新做一次风险测评——这一步我代办不了。',
        "This product's risk grade is above your risk assessment with the bank, so suitability rules forbid selling it to you.",
        "I can find a product that matches your grade. If you really want a riskier product, you'd need to redo the risk assessment in the app or at a branch first — I can't do that step for you."),
    'INV_RISK_LEVEL': (
        '这只产品的风险等级高于您的风险承受能力评级，属于不当销售。',
        '我可以帮您找风险等级相符的产品；如果您确实想买更高风险的产品，需要先到网点或 App 重新做风险测评。',
        "This product's risk grade is above your assessed risk tolerance — that would be mis-selling.",
        "I can find a product within your grade; for a riskier one you'd need to redo the risk assessment first."),
    'INV_CARD_WEAKENING': (
        '这个操作会削弱卡片的安全设置。',
        '提高限额、解冻卡片、开启线上支付都属于高危操作，需要您本人单独确认。',
        'This would weaken a protection on your card.',
        'Raising limits, unfreezing a card and enabling online payments are high-risk and need your explicit approval.'),
    'INV_CREDENTIAL_CHANGE': (
        '这个操作会变更账户的预留手机号或邮箱——这是账户被接管的第一步。',
        '凭证变更我不会代为执行，请通过网点或官方 App 本人办理。',
        'This would change the phone number or e-mail on your account — the first step of an account takeover.',
        "I won't do credential changes; please do them yourself in the official app or at a branch."),
    'INV_ACCOUNT_SCOPE': (
        '这笔操作动用了本次授权范围之外的账户。',
        '如果您想从那个账户出账，我可以重新拟一份包含它的授权。',
        'This would use an account outside what you authorised.',
        'If you want to pay from that account, I can draft an authorisation that includes it.'),
    'INV_EXPIRED': (
        '这次授权已经过期了。',
        '重新确认一次就可以继续。',
        'This authorisation has expired.',
        'Just confirm again to continue.'),
    'INV_UNRATIFIED_EFFECT': (
        '这个计划会产生一类您这次授权没有提到的变更。',
        '我把它单独拿出来请您确认，而不是默认替您决定。',
        'The plan would make a kind of change your authorisation never mentioned.',
        "I'm raising it with you separately rather than deciding for you."),
    'INV_UNCAPPED_OUTFLOW': (
        '这次授权里没有任何金额上限，而计划要出账。',
        '请先给一个支出上限，我再继续。',
        'This authorisation has no spending cap, and the plan would pay money out.',
        "Give me a spending limit first and I'll continue."),
    'GRD_UNKNOWN_ACCOUNT': (
        '计划里引用了一个不属于您的账户——这是我这边出的错，不是您的问题。',
        '麻烦您确认一下账号，或者直接告诉我用哪个账户。',
        "The plan referred to an account that isn't yours — that's my mistake, not yours.",
        'Please confirm the account number, or tell me which account to use.'),
    'GRD_UNKNOWN_ENTITY': (
        '计划里引用了一个在系统里不存在的产品或授权编号——我把它弄错了。',
        '我重新查一遍再给您确认。',
        "The plan referred to a product or mandate that doesn't exist — I got it wrong.",
        "I'll look it up again before asking you to confirm."),
    'GRD_MALFORMED_ACCOUNT': (
        '收款账号格式不对。',
        '麻烦您再核对一遍账号。',
        "The payee account number isn't in a valid format.",
        'Please double-check the number.'),
    'GRD_CLAIM_MISMATCH': (
        '我刚才准备告诉您的数字和核心系统对不上——这是我记错了，不是账户有问题。',
        '我以系统实际数据为准重新说一遍。',
        "A figure I was about to tell you doesn't match the core system — I misremembered it; nothing is wrong with your account.",
        "I'll restate it from the system's actual data."),
    'GRD_AMOUNT_MISMATCH': (
        '我说出来的金额和实际要执行的金额不一致。',
        '为避免误解，这笔操作我停下了。请您确认到底要转多少。',
        "The amount I described doesn't match the amount that would actually move.",
        'To avoid a misunderstanding I stopped. Please confirm how much you want to send.'),
    'KRN_LOOKALIKE': (
        '这个收款账号和您常用的某个账号在界面显示上完全一样，但并不是同一个账号——这是账号混淆诈骗的典型手法。',
        '请当面或电话向对方核实完整账号后再转。我不会凭相似就放行。',
        'This payee account looks identical on screen to one you use regularly, but it is a different account — a classic look-alike scam.',
        "Check the full account number with the person face to face or by phone before sending. I won't let it through because it looks similar."),
    'KRN_TIER_CEILING': (
        '这个操作需要的权限高于您这次授权的等级。',
        '如果确实要做，请重新确认一份权限更高的授权。',
        'This needs a higher level of authority than you granted this time.',
        'If you do want it, approve a higher-level authorisation.'),
    'KRN_INJECTION_CONTENT': (
        '您账单里有一条记录的附言伪装成了银行通知，试图诱导我转账。我已经把它当作普通文本隔离，没有照做。',
        '这类「转入安全账户」的话术是诈骗。银行不会通过转账附言下达指令，建议您留意一下这笔小额转入。',
        "A memo on one of your transactions is pretending to be a bank notice and trying to get me to move money. I've isolated it as plain text and didn't act on it.",
        '"Move funds to a safe account" is a scam script. Banks never give instructions through payment memos — keep an eye on that small incoming payment.'),
    'KRN_TAINT_ESCALATION': (
        '这次操作的上下文里出现了可疑的诱导内容，所以我没有直接执行。',
        '请您确认这确实是您本人的意思。',
        "Suspicious instructions appeared in the context of this operation, so I didn't execute it directly.",
        'Please confirm this really is what you want.'),
    'POL_CREDENTIAL_OP': (
        '计划里包含变更账户凭证的操作，这超出了我的权限范围。',
        '手机号、邮箱、密码的变更请本人通过官方渠道办理。',
        "The plan includes changing account credentials, which is outside what I'm allowed to do.",
        'Phone, e-mail and password changes must be done by you through official channels.'),
    'POL_INTENT_MISMATCH': (
        '这个计划绑定的不是您刚才确认的那份授权。',
        '我重新生成一次。',
        "This plan isn't bound to the authorisation you just approved.",
        "I'll generate it again."),
    'KRN_CORE_REJECTED': (
        '核心系统拒绝了这笔操作。',
        '常见原因是余额不足或账户状态异常，您可以先查一下余额。',
        'The core banking system rejected the operation.',
        'The usual causes are an insufficient balance or an account restriction — you could check your balance first.'),
    'INV_CARD_OUT_OF_SCOPE': (
        '这个计划调整了一张您这次授权没有点名的卡。',
        '告诉我是哪张卡，我只为那一张拟授权。',
        "This would change a card your authorisation doesn't cover.",
        "Tell me which card you mean and I'll draft an authorisation for that one."),
    'KRN_DISCLOSURE': (
        '这个计划会把您的敏感信息——账号、卡号、证件号、手机号或验证码——发给银行以外的人。',
        '这正是信息套取诈骗的手法。这类信息我不会发给任何人，无论谁要。',
        'The plan would send your sensitive information — an account, card or ID number, phone, or a code — to someone outside the bank.',
        "That's how information-extraction scams work. I never send that kind of data anywhere, whoever asks."),
}

_FALLBACK = (
    "这个计划没有通过安全校验。",
    "我不会在没弄清楚的情况下动您的钱。您可以换一种说法，或者把要做的事说得更具体一些。",
    "This plan didn't pass the safety checks.",
    "I won't move your money without being sure. Try saying it another way, or be more specific.",
)

# 按这个顺序挑主要原因：先说最能解释「为什么」的那一条。
# 幻觉类排在最前，因为那是我方的错误，客户有权先知道这一点。
_PRIORITY = (
    "KRN_DISCLOSURE", "KRN_SUITABILITY",
    "GRD_CLAIM_MISMATCH", "GRD_AMOUNT_MISMATCH", "GRD_UNKNOWN_ACCOUNT",
    "GRD_UNKNOWN_ENTITY", "GRD_MALFORMED_ACCOUNT",
    "KRN_LOOKALIKE", "INV_CREDENTIAL_CHANGE", "POL_CREDENTIAL_OP",
    "INV_UNCAPPED_MANDATE", "INV_CARD_OUT_OF_SCOPE", "INV_CARD_WEAKENING", "INV_RISK_LEVEL",
    "INV_PAYEE_NOT_ALLOWED", "INV_NEW_PAYEE", "INV_NEW_MANDATE",
    "INV_MAX_SINGLE_OUT", "INV_MAX_TOTAL_OUT", "INV_ACCOUNT_SCOPE",
    "INV_MANDATE_CAP", "INV_EXPIRED", "INV_UNCAPPED_OUTFLOW",
    "KRN_CORE_REJECTED", "KRN_TIER_CEILING", "INV_UNRATIFIED_EFFECT",
)


def explain_refusal(decision: Decision, lang: str = "zh") -> Recovery:
    """
    为什么拒绝，以及现在能做什么 / Why it was refused, and what to do now.

    只挑一条主要原因来说。一次性抛出六条阻断性发现，客户读完还是不知道该怎么办；
    内核的完整发现列表留在审计日志和界面右侧的裁决面板里，供复核而不是供对话。
    """
    en = lang == "en"
    codes = {f.code for f in decision.findings}

    def pick(code: str) -> Recovery:
        zr, zs, er, es = _EXPLAIN[code]
        reason, suggestion = (er, es) if en else (zr, zs)
        detail = _detail(decision, code, lang)
        return Recovery(reason=(reason + " " + detail if en else reason + detail) if detail else reason,
                        suggestion=suggestion)

    for code in _PRIORITY:
        if code in codes:
            return pick(code)
    for finding in decision.blocking():
        if finding.code in _EXPLAIN:
            return pick(finding.code)
    zr, zs, er, es = _FALLBACK
    return Recovery(er, es) if en else Recovery(zr, zs)


def _detail(decision: Decision, code: str, lang: str) -> str:
    """把具体数字补进说明里——「超了」不如「超了多少」有用。"""
    finding = next((f for f in decision.findings if f.code == code), None)
    if finding is None:
        return ""
    ev = finding.evidence
    en = lang == "en"
    if code in ("INV_MAX_TOTAL_OUT", "INV_MAX_SINGLE_OUT"):
        observed = ev.get("observed_fen") or ev.get("amount_fen")
        limit = ev.get("limit_fen")
        if observed and limit is not None:
            return (f"The plan pays out {cny(observed)}; the limit is {cny(limit)}." if en
                    else f"计划支出 {cny(observed)}，授权上限是 {cny(limit)}。")
    if code == "GRD_CLAIM_MISMATCH":
        claimed, actual = ev.get("claimed_fen"), ev.get("actual_fen")
        if claimed is not None and actual is not None:
            return (f"I said {cny(claimed)}; the actual figure is {cny(actual)}." if en
                    else f"我说的是 {cny(claimed)}，实际是 {cny(actual)}。")
    if code == "GRD_AMOUNT_MISMATCH":
        claimed, planned = ev.get("claimed_fen"), ev.get("planned_fen")
        if claimed is not None and planned is not None:
            return (f"I said {cny(claimed)}, but the plan would send {cny(planned)}." if en
                    else f"我说的是 {cny(claimed)}，而计划实际要转 {cny(planned)}。")
    if code in ("INV_RISK_LEVEL", "KRN_SUITABILITY"):
        product_risk = ev.get("product_risk")
        ceiling = ev.get("customer_ceiling") or ev.get("customer_grade")
        if product_risk and ceiling:
            return (f"The product is R{product_risk}; your grade is R{ceiling}." if en
                    else f"产品是 R{product_risk}，您的评级是 R{ceiling}。")
    if code == "KRN_LOOKALIKE":
        candidate, resembles = ev.get("candidate"), ev.get("resembles")
        if candidate and resembles:
            return (f"The plan pays {candidate}; the one you normally use is {resembles}." if en
                    else f"计划转给 {candidate}，而您常用的是 {resembles}。")
    if code == "INV_PAYEE_NOT_ALLOWED":
        payee = ev.get("payee")
        if payee:
            return f"The payee is {payee}." if en else f"收款人是 {payee}。"
    if code == "KRN_CORE_REJECTED":
        why = str(ev.get("reason", ""))
        if "余额不足" in why or "insufficient" in why:
            return ("Your balance isn't enough for this." if en else "原因是余额不足。")
    return ""


def describe_escalation(decision: Decision, lang: str = "zh") -> str:
    """为什么要客户再确认一次 / Why this one needs a second look."""
    en = lang == "en"
    tier = decision.tier
    if any(f.code == "KRN_TAINT_ESCALATION" for f in decision.findings):
        # 注入升级要第一个说，而且要说清楚。客户被要求再确认一次，却不知道
        # 是因为有人正在骗他，那这次确认就只是一次多余的点击。
        if en:
            head = ("A message in your statement is pretending to be a bank notice and trying to get "
                    "money moved. This operation is one you asked for, and I've isolated that text "
                    "without acting on it — but since someone is targeting this account, I want to "
                    "check with you once more.")
        else:
            head = ("您的账单里有一条伪装成银行通知的文字，正在试图诱导转账。"
                    "这笔操作本身是您提出的，我已经把那段文字隔离、没有照做，"
                    "但既然有人正在针对这个账户，我想再跟您确认一次。")
        if any(f.code == "GRD_UNKNOWN_PAYEE" for f in decision.findings):
            head += ("\n\nPlease note: you have never paid this payee before. If someone on the phone "
                     "or by text asked you to send money to this account, it is almost certainly a "
                     "scam — hang up and call the bank's official number."
                     if en else
                     "\n\n请特别注意：这个收款人您从来没有付过款。"
                     "如果是电话或短信里有人让您转到这个账号，那几乎可以确定是诈骗，"
                     "请先挂断并拨打银行官方客服核实。")
        return head

    if en:
        head = ("This is a high-risk operation, so I need you to confirm it once more: "
                if tier is Tier.T3_CRITICAL else
                "This goes beyond the everyday scope you just authorised, so please confirm: ")
    else:
        head = ("这是一个高危操作，我需要您本人再确认一次："
                if tier is Tier.T3_CRITICAL else "这一步超出了您刚才授权的日常范围，我需要您确认一下：")
    reasons: list[str] = []
    for finding in decision.findings:
        code = finding.code
        if code == "TIER_CARD_WEAKENING":
            control = finding.evidence.get("control", "")
            card = finding.evidence.get("card", "")
            if control == "status":
                reasons.append(f"unfreezing {card} restores its spending ability" if en
                               else f"解冻会恢复 {card} 的消费能力")
            elif "limit" in str(control):
                reasons.append(f"raising the limit on {card} increases what can be spent on it" if en
                               else f"调高 {card} 的限额会扩大可被消费的金额")
            else:
                reasons.append(f"it weakens a protection on {card}" if en else f"它会削弱 {card} 的一项安全设置")
        elif code in ("TIER_FIRST_PAYMENT", "GRD_UNKNOWN_PAYEE"):
            reasons.append("you have never paid this account before" if en
                           else "这个收款账号您此前从未付过款")
        elif code == "TIER_NEW_PAYEE":
            reasons.append("it adds a new payee" if en else "会新增一个收款人")
        elif code == "TIER_MANDATE":
            merchant = finding.evidence.get("merchant", "")
            reasons.append(f"it sets up a standing payment to \"{merchant}\"" if en
                           else f"会为「{merchant}」建立长期扣款授权")
        elif code == "TIER_PURCHASE":
            reasons.append("it buys a product whose risk grade you didn't set" if en
                           else "会买入一只您这次没有约定风险等级的产品")
        elif code == "INV_UNRATIFIED_EFFECT":
            reasons.append("it makes a kind of change your authorisation didn't mention" if en
                           else "会产生一类您这次授权没有提到的变更")
        elif code == "KRN_PAYEE_BOOK_POISONED":
            reasons.append("your payee list contains a look-alike account" if en
                           else "您的收款人名单里混进了一个显示位相同的账号")
    if not reasons:
        reasons.append("it goes beyond the everyday limits" if en else "它超出了日常额度范围")
    joiner = "; " if en else "、"
    text = head + joiner.join(dict.fromkeys(reasons)) + ("." if en else "。")
    if any(f.code == "TIER_FIRST_PAYMENT" for f in decision.findings):
        # 首次付款是电信诈骗最常见的形态：确认之前，把识别方法说给客户听。
        text += ("\n\nIf someone on the phone, by text or online asked you to send money to "
                 "this account — claiming to be police, the bank, a courier, customer support "
                 "or an investment adviser — stop: it is almost certainly a scam. Hang up and "
                 "check through the bank's official number first."
                 if en else
                 "\n\n如果是有人通过电话、短信或网络让您转到这个账号——不论对方自称公安、"
                 "银行、快递、客服还是投资老师——请先停下：这几乎可以肯定是诈骗。"
                 "挂断后通过银行官方电话核实。")
    return text


def fraud_warning(report, lang: str = "zh") -> str:
    """
    主动提醒客户：有人正在针对这个账户。

    内核已经把这些内容隔离了，操作本身是安全的——但客户并不知道有人在骗他。
    说出来比默默拦住更有用：被拦住的只是这一次，而客户接下来很可能自己去
    网银或手机上操作，那边没有任何保护。
    """
    if report is None:
        return ""
    en = lang == "en"
    notes: list[str] = []
    flagged = [t for t in report.taint_reports if t["score"] >= 55]
    if flagged:
        origins = {t["origin"] for t in flagged}
        if en:
            where = "transaction memos" if "txn_memo" in origins else "statement"
            notes.append(f"Your {where} contain text pretending to be a bank notice, asking for money "
                         "to be moved to a \"safe account\". That is a classic scam script — banks "
                         "never give instructions through payment memos. I treated it as plain text "
                         "and did not act on it.")
        else:
            where = "交易附言" if "txn_memo" in origins else "账单内容"
            notes.append(f"您的{where}里有一条伪装成银行通知的文字，要求把资金转入所谓「安全账户」。"
                         "这是典型的诈骗话术，银行不会通过转账附言下达指令。"
                         "我已经把它当作普通文本处理，没有照做。")
    poisoned = next((f for f in report.decision.findings if f.code == "KRN_PAYEE_BOOK_POISONED"), None)
    if poisoned is not None:
        masked = poisoned.evidence.get("paying", "")
        mine = poisoned.evidence.get("paying_full", "")
        twin = poisoned.evidence.get("twin_full", "")
        # 这里必须给完整账号。界面上两个都显示成 6228****0018，
        # 而客户要能分辨出该删哪一个——用掩码写这条提醒，等于把攻击
        # 赖以生效的那层遮挡又盖了回去。
        if en:
            notes.append(f"Your payee list has two accounts that both display as {masked} on screen, "
                         f"but they are not the same (they look alike):\n"
                         f"    · {mine}   ← the one you've always used, and the one paid this time\n"
                         f"    · {twin}   ← check and remove it — it was very likely added by someone else")
        else:
            notes.append(f"您的收款人名单里有两个账号，在界面上都显示为 {masked}，但它们并不是同一个"
                         f"（看起来很像）：\n"
                         f"    · {mine}　← 您一直在用的，本次付的就是这个\n"
                         f"    · {twin}　← 建议核实后删除，很可能是别人故意加进来的")
    if not notes:
        return ""
    lead = "⚠ One more thing: " if en else "⚠ 另外提醒您："
    return "\n\n" + "\n\n".join(lead + n for n in notes)
