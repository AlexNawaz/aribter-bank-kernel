"""
只读答复 / Read-only answers.

查余额、看账单、列自动扣费、看产品与卡片——这些答复的每一个数字都直接取自核心
系统，不经过任何模型。

这里不让模型复述数字。幻觉防护拦的就是「复述出错」，而最稳妥的做法是根本不给它
复述的机会：答案由代码从核心状态里读出来、格式化，再交给客户。

交易附言一个字都不会出现在这些答复里——附言是付款方写的，属于不可信内容。商户名
照常显示（银行流水本来就显示商户名），但截断到一定长度，不让一段伪装成通知的长文本
借着商户名字段完整地出现在智能体的回答里。
"""

from __future__ import annotations

from typing import Any

from arbiter.bank.core import BankCore
from arbiter.types import cny, mask_account

_CATEGORY_EN = {"住房": "housing", "出行": "travel", "订阅": "subscriptions", "餐饮": "dining",
                "收入": "income", "其他": "other"}


def _merchant(name: str) -> str:
    name = (name or "").replace("\n", " ").strip()
    return name if len(name) <= 16 else name[:15] + "…"


def balance(core: BankCore, lang: str) -> str:
    rows = [f"{a.nickname} {mask_account(a.account_no)}：{cny(a.balance)}"
            for a in core.state.accounts.values()]
    head = "Your balances:" if lang == "en" else "您的账户余额："
    return head + "\n" + "\n".join(f"· {r}" for r in rows)


def mandates(core: BankCore, lang: str) -> str:
    st = core.state
    if not st.mandates:
        return "You have no direct debits." if lang == "en" else "您当前没有任何自动扣费。"
    total = sum(m.cap for m in st.mandates.values())
    if lang == "en":
        rows = [f"{m.merchant}: {cny(m.cap)} per {'month' if m.period == 'monthly' else m.period}, "
                f"next charged {m.next_charge}" for m in st.mandates.values()]
        return (f"You have {len(rows)} direct debit{'' if len(rows) == 1 else 's'}, {cny(total)} a month in total:\n"
                + "\n".join(f"· {r}" for r in rows))
    rows = [f"{m.merchant}：{cny(m.cap)}/{'月' if m.period == 'monthly' else m.period}，"
            f"下次扣费 {m.next_charge}" for m in st.mandates.values()]
    return (f"您当前有 {len(rows)} 项自动扣费，每月合计 {cny(total)}：\n"
            + "\n".join(f"· {r}" for r in rows))


def statement(core: BankCore, slots: dict[str, Any], lang: str) -> tuple[str, str]:
    """
    账单分析。返回 (答复, 追问)；追问为空表示不提议任何事。

    「需要我帮您看看哪些可以取消吗？」单独返回而不是拼在答复里：如果同一轮里还生成了
    一份授权信封，这句追问就必须拿掉——否则客户回一句「好」，到底是在答哪一个？

    客户问的往往不是「全部」：「餐饮花了多少」「这个月收入多少」「最大的一笔是什么」。
    能回答具体问题就回答具体问题，答不了的（比如上个月——数据里只有本期）就说清楚。
    """
    en = lang == "en"
    txns = list(core.state.transactions)
    spend = [t for t in txns if t.amount < 0]
    income = [t for t in txns if t.amount > 0]
    total_out = -sum(t.amount for t in spend)
    total_in = sum(t.amount for t in income)
    period = txns[0].date[:7] if txns else ""

    prefix = ""
    if slots.get("stmt_other_period"):
        prefix = (f"I only hold this period's records ({period}), so here's that:\n\n" if en else
                  f"我手上只有本期（{period}）的流水，先按本期回答：\n\n")

    if slots.get("stmt_income"):
        rows = [f"{t.date} {_merchant(t.merchant) or t.counterparty} {cny(t.amount)}" for t in income]
        head = (f"Income this period: {cny(total_in)}" if en else f"本期收入合计 {cny(total_in)}")
        body = "\n".join(f"· {r}" for r in rows) if rows else ("(none)" if en else "（没有收入记录）")
        return prefix + head + ("\n" if rows else " ") + body, ""

    if slots.get("stmt_largest") and spend:
        t = min(spend, key=lambda x: x.amount)
        cat = t.category or ("other" if en else "其他")
        if en:
            return prefix + (f"Your largest payment this period: {cny(-t.amount)} to "
                             f"{_merchant(t.merchant) or 'a payee'} on {t.date} "
                             f"({_CATEGORY_EN.get(cat, cat)})."), ""
        return prefix + f"本期最大的一笔支出：{t.date} 付给{_merchant(t.merchant) or '收款人'} {cny(-t.amount)}（{cat}）。", ""

    if slots.get("stmt_recent") and txns:
        t = max(txns, key=lambda x: x.date)
        direction_en = "paid" if t.amount < 0 else "received"
        if en:
            return prefix + (f"Most recent transaction: {t.date}, {direction_en} {cny(abs(t.amount))} "
                             f"({_merchant(t.merchant) or t.counterparty})."), ""
        word = "支出" if t.amount < 0 else "收入"
        return prefix + f"最近一笔交易：{t.date}，{word} {cny(abs(t.amount))}（{_merchant(t.merchant) or t.counterparty}）。", ""

    cat = slots.get("stmt_category")
    if cat:
        hits = [t for t in spend if (t.category or "其他") == cat]
        amount = -sum(t.amount for t in hits)
        share = (amount * 100 // total_out) if total_out else 0
        rows = [f"{t.date} {_merchant(t.merchant) or t.counterparty} {cny(-t.amount)}" for t in hits]
        if en:
            head = (f"{_CATEGORY_EN.get(cat, cat).capitalize()} this period: {cny(amount)} "
                    f"({share}% of spending)")
        else:
            head = f"本期{cat}支出 {cny(amount)}，占总支出 {share}%"
        body = "\n".join(f"· {r}" for r in rows)
        return prefix + head + ("：\n" + body if (rows and not en) else (":\n" + body if rows else ".")), ""

    # 全部 / the full breakdown
    buckets: dict[str, int] = {}
    for t in spend:
        buckets[t.category or "其他"] = buckets.get(t.category or "其他", 0) - t.amount
    if not buckets:
        return prefix + ("No spending in this period." if en else "这段时间没有支出记录。"), ""
    ordered = sorted(buckets.items(), key=lambda kv: -kv[1])
    if en:
        rows = [f"{_CATEGORY_EN.get(n, n)}: {cny(a)} ({a * 100 // total_out}%)" for n, a in ordered]
        head = f"Spending this period {cny(total_out)}, income {cny(total_in)}:"
    else:
        rows = [f"{n}：{cny(a)}（{a * 100 // total_out}%）" for n, a in ordered]
        head = f"本期支出合计 {cny(total_out)}，收入 {cny(total_in)}："
    text = prefix + head + "\n" + "\n".join(f"· {r}" for r in rows)
    subs = buckets.get("订阅", 0)
    question = ""
    if subs and core.state.mandates:
        question = (f"Subscriptions come to {cny(subs)} of that. Shall I show you which ones you "
                    f"could cancel?" if en else
                    f"其中订阅类共 {cny(subs)}。需要我帮您看看哪些可以取消吗？")
    return text, question


def products(core: BankCore, lang: str) -> str:
    st = core.state
    en = lang == "en"
    grade = st.customer.risk_tolerance
    rows = []
    for pr in sorted(st.products.values(), key=lambda x: x.risk_level.value):
        over = pr.risk_level.value > grade.value
        if en:
            mark = "  — above your assessed grade, I can't buy this for you" if over else ""
            rows.append(f"{pr.name} ({pr.code}) · {pr.risk_level.label_en} · min {cny(pr.min_amount)}{mark}")
        else:
            mark = "  — 高于您的评级，我不能为您购买" if over else ""
            rows.append(f"{pr.name}（{pr.code}）·{pr.risk_level.label_zh}·起购 {cny(pr.min_amount)}{mark}")
    if en:
        head = f"These are the products on file. Your assessed grade is {grade.label_en} ({grade.name}):"
        tail = ("\n\nProduct marketing copy is untrusted content — I don't repeat it, and it never "
                "reaches a decision. The risk grade above is read from the core record, not from "
                "the blurb.\nWealth products are not deposits; they carry risk and returns are not "
                "guaranteed.")
    else:
        head = f"在售产品如下。您的风险评级是{grade.label_zh}（{grade.name}）："
        tail = ("\n\n产品宣传文案属于不可信内容，我不会复述，它也进不了任何裁决。"
                "上面的风险等级取自核心系统的记录，不是取自文案。\n理财非存款，产品有风险，投资须谨慎。")
    return head + "\n" + "\n".join(f"· {r}" for r in rows) + tail


def payees(core: BankCore, lang: str) -> str:
    st = core.state
    en = lang == "en"
    if not st.payees:
        return "You have no saved payees." if en else "您还没有常用收款人。"
    if en:
        rows = [f"{p.name} · {p.bank} · {mask_account(p.account_no)} (paid {p.times_used} time{'' if p.times_used == 1 else 's'})"
                for p in st.payees.values()]
        return "Your saved payees:\n" + "\n".join(f"· {r}" for r in rows)
    rows = [f"{p.name}·{p.bank}·{mask_account(p.account_no)}（已付款 {p.times_used} 次）"
            for p in st.payees.values()]
    return "您的常用收款人：\n" + "\n".join(f"· {r}" for r in rows)


def cards(core: BankCore, lang: str) -> str:
    st = core.state
    en = lang == "en"
    if not st.cards:
        return "You have no cards on file." if en else "您名下没有卡片。"
    rows = []
    for c in st.cards.values():
        if en:
            kind = "credit card" if c.kind == "credit" else "debit card"
            status = "frozen" if c.status == "frozen" else "active"
            rows.append(f"{c.card_id} · {kind} · {status} · daily {cny(c.daily_limit)} / "
                        f"single {cny(c.single_limit)} · online payments "
                        f"{'on' if c.online_payment else 'off'}")
        else:
            kind = "信用卡" if c.kind == "credit" else "借记卡"
            status = "已冻结" if c.status == "frozen" else "正常"
            rows.append(f"{c.card_id}·{kind}·{status}·日限额 {cny(c.daily_limit)}／单笔 "
                        f"{cny(c.single_limit)}·线上支付{'开启' if c.online_payment else '关闭'}")
    head = "Your cards:" if en else "您名下的卡："
    return head + "\n" + "\n".join(f"· {r}" for r in rows)
