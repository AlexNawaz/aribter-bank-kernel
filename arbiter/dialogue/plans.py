"""
从意图到授权信封与操作计划 / From intents to envelopes and plans.

对话层理解出一件或几件要做的事（`Frame`）。这里把它们落成：

    Step       解析到核心系统里的具体对象：哪个收款账号、哪张卡、哪项代扣、多少钱
    IntentSpec 客户签署的授权信封——约束、权限上限、有效期、风险提示
    OpPlan     交给内核裁决的操作序列

几件事可以放进同一份信封、签一次（「取消视频会员和健身房」「冻结借记卡，再给
房东转4500」）。合并的规则是保守的：支出上限相加，收款人名单取并集，权限上限取
最高的那一个；而内核照样按整个计划在模拟里逐步观测到的效果裁决——S03 那种
「解冻→转账→再冻结」的组合之所以拦得住，正是因为效果是按过程记录的。

**风险提示写在信封里，而不是写在信封外。** 风险揭示的意义在于客户确认时看到了它，
所以它是被签署内容的一部分，进入信封摘要；改动一个字，签名就对不上。

**目标不能被静默替换。** 客户点名的东西如果办不了（已经取消、超出风险等级、余额
不够），这里给出说明或替代建议，而不是悄悄换一个能办的。
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from typing import Any

from arbiter.bank.core import BankCore
from arbiter.dialogue.i18n import t
from arbiter.dialogue.nlu import Act, Frame
from arbiter.types import (
    IntentSpec,
    Invariant,
    InvariantKind,
    Operation,
    OpKind,
    OpPlan,
    RiskLevel,
    Tier,
    cny,
    mask_account,
)

LARGE_TRANSFER = 50_000_00       # 分；超过这个数的转账在信封里加一条提示


# ---------------------------------------------------------------------------
# 解析到具体对象 / Resolution against core state
# ---------------------------------------------------------------------------

def _resolve_payee(core: BankCore, key: str, slots: dict[str, Any] | None = None) -> str | None:
    from arbiter.bank.world import PAYEE_LANDLORD, PAYEE_MOTHER, PAYEE_UTILITY
    if key == "literal" and slots:
        return slots.get("payee_account")
    return {"landlord": PAYEE_LANDLORD, "mother": PAYEE_MOTHER,
            "utility": PAYEE_UTILITY}.get(key)


def _resolve_card(core: BankCore, key: str) -> str | None:
    if not key:
        return None
    if key in core.state.cards:
        return key
    for card in core.state.cards.values():
        if card.kind == key:
            return card.card_id
    return None


_MANDATE_KEYS = {"video": "视频", "gym": "健身", "cloud": "云存储"}


def _resolve_mandate(core: BankCore, key: str) -> str | None:
    # 空槽位不能匹配任何东西。原先是子串匹配，而空串是每一个商户名的子串，
    # 于是「没说是哪一项」会静默地解析成第一项代扣。
    if not key:
        return None
    if key in core.state.mandates:
        return key
    if key == "member":
        hits = [m.mandate_id for m in core.state.mandates.values() if "会员" in m.merchant]
        return hits[0] if len(hits) == 1 else None
    want = _MANDATE_KEYS.get(key, key)
    for mandate in core.state.mandates.values():
        if want and want in mandate.merchant:
            return mandate.mandate_id
    return None


def _resolve_product(core: BankCore, risk: int) -> str | None:
    """在客户风险等级内挑一只产品 / pick a product at or under the grade."""
    best = None
    for product in core.state.products.values():
        if product.risk_level.value <= risk:
            if best is None or product.risk_level.value > best.risk_level.value:
                best = product
    return best.code if best else None


def _resolve_holding(core: BankCore, key: str) -> str | None:
    """赎回只能动客户真正持有的东西 / redemption only touches an actual holding."""
    held = [code for code, h in core.state.holdings.items() if h.amount > 0]
    if key and key in held:
        return key
    return held[0] if len(held) == 1 else None


def mandate_label(core: BankCore, key: str) -> str:
    """一项代扣在提示里怎么称呼——已经取消的也要叫得出名字。"""
    if key in core.state.mandates:
        return core.state.mandates[key].merchant
    from arbiter.bank.world import MANDATE_CLOUD, MANDATE_GYM, MANDATE_VIDEO
    names = {MANDATE_VIDEO: "某视频会员", MANDATE_GYM: "星力健身", MANDATE_CLOUD: "云存储服务",
             "video": "视频会员", "gym": "健身房", "cloud": "云存储", "member": "会员"}
    return names.get(key, key)


def mandate_items(core: BankCore, lang: str) -> str:
    sep = "; " if lang == "en" else "；"
    per = "/month" if lang == "en" else "/月"
    return sep.join(f"{m.merchant} {cny(m.cap)}{per}" for m in core.state.mandates.values()) or (
        "none" if lang == "en" else "无")


def available(core: BankCore) -> int:
    from arbiter.bank.world import ACC_CURRENT
    acct = core.state.accounts.get(ACC_CURRENT)
    return acct.balance if acct else 0


# ---------------------------------------------------------------------------
# 具体步骤 / Concrete steps
# ---------------------------------------------------------------------------

@dataclass
class Step:
    act: Act
    target: str = ""
    amount: int = 0
    extra: dict[str, Any] = field(default_factory=dict)
    desc_zh: str = ""
    desc_en: str = ""


@dataclass
class Resolution:
    """一件事落到核心系统上的结果 / what one frame resolved to."""
    steps: list[Step] = field(default_factory=list)
    notes: list[tuple[str, str]] = field(default_factory=list)   # (zh, en)
    offer: tuple[Act, dict[str, Any]] | None = None             # 替代建议
    offer_note: tuple[str, str] | None = None                   # 替代建议怎么问（单独放，便于丢弃）
    ask: tuple[str, list[tuple[str, Any]]] | None = None        # (slot, options)
    refusal: bool = False                                       # 这是一次拒绝（附原因与出路）


def _note(res: Resolution, key: str, **kw: Any) -> None:
    res.notes.append((t(key, "zh", **kw), t(key, "en", **kw)))


def resolve_frame(frame: Frame, core: BankCore) -> Resolution:
    act, slots = frame.act, frame.slots
    res = Resolution()
    st = core.state

    if act is Act.TRANSFER:
        payee = _resolve_payee(core, slots.get("payee", ""), slots)
        if "scam" in frame.flags:
            _note(res, "scam_warning")
            if not payee:
                _note(res, "scam_no_payee")
                return res
        if not payee:
            res.ask = ("payee", payee_options(core))
            return res
        amount = int(slots.get("amount") or 0)
        if amount <= 0 or "amount_invalid" in frame.flags:
            if "amount_invalid" in frame.flags:
                _note(res, "amount_invalid")
            res.ask = ("amount", [])
            return res
        if amount > available(core):
            _note(res, "noop_insufficient", balance=cny(available(core)), amount=cny(amount),
                  what="转", what_en="send")
            _note(res, "insufficient_hint")
            res.refusal = True
            return res
        known = core.resolve_payee(payee)
        # 已知收款人带上掩码账号，客户核对的是「名字 + 屏幕上那 8 位」；
        # 从没付过款的账号给出完整号码——要核对的恰恰是每一位数字。
        if known is not None:
            name = f"{known.name}（{mask_account(payee)}）"
            name_en = f"{known.name} ({mask_account(payee)})"
        else:
            name = f"账号 {payee}（此前从未付过款）"
            name_en = f"account {payee} (never paid before)"
        res.steps.append(Step(Act.TRANSFER, payee, amount,
                              {"trusted": bool(known and known.verified and known.times_used > 0)},
                              t("step_transfer", "zh", name=name, amount=cny(amount)),
                              t("step_transfer", "en", name=name_en, amount=cny(amount))))
        return res

    if act is Act.INVEST:
        amount = int(slots.get("amount") or 0)
        grade = st.customer.risk_tolerance
        named = st.products.get(slots.get("product") or "")
        ceiling = int(slots.get("risk", grade.value))
        if named is not None and named.risk_level.value > grade.value:
            _over_grade(res, core, named.name, named.code, named.risk_level)
            return res
        if named is None and ceiling > grade.value:
            # 客户要的风险等级高于测评结果：说明原因、给出评级内的替代，**问他要不要**——
            # 不悄悄换一只买掉。
            _over_grade(res, core, "", "", RiskLevel(ceiling))
            return res
        code = named.code if named is not None else _resolve_product(core, min(ceiling, grade.value))
        product = st.products.get(code or "")
        if product is None:
            return res
        if amount <= 0:
            res.ask = ("amount", [])
            return res
        if amount < product.min_amount:
            _note(res, "noop_below_min", name=product.name, min=cny(product.min_amount), amount=cny(amount))
            res.refusal = True
            if product.min_amount <= available(core):
                # 客户点名的产品不变，只把金额提到起购线——问他，不替他改。
                res.offer = (Act.INVEST, {"product": product.code, "amount": product.min_amount})
                res.offer_note = (t("below_min_offer", "zh", min=cny(product.min_amount)),
                                  t("below_min_offer", "en", min=cny(product.min_amount)))
            return res
        if amount > available(core):
            _note(res, "noop_insufficient", balance=cny(available(core)), amount=cny(amount),
                  what="申购", what_en="invest")
            _note(res, "insufficient_hint")
            res.refusal = True
            return res
        level = product.risk_level
        what_zh = f"「{product.name}」（{product.code}）"
        what_en = f"\"{product.name}\" ({product.code})"
        res.steps.append(Step(Act.INVEST, product.code, amount, {"ceiling": level.value},
                              t("step_invest", "zh", amount=cny(amount), what=what_zh)
                              + t("step_invest_ceiling", "zh", grade=level.name, label=level.label_zh),
                              t("step_invest", "en", amount=cny(amount), what=what_en)
                              + t("step_invest_ceiling", "en", grade=level.name, label=level.label_en)))
        return res

    if act is Act.REDEEM:
        held = [code for code, h in st.holdings.items() if h.amount > 0]
        if not held:
            _note(res, "noop_no_holdings")
            return res
        code = _resolve_holding(core, slots.get("product", ""))
        if code is None:
            res.ask = ("product", [(st.products[c].name, c) for c in held])
            return res
        holding = st.holdings[code].amount
        amount = int(slots.get("amount") or holding)
        amount = min(amount, holding)
        product = st.products[code]
        res.steps.append(Step(Act.REDEEM, code, amount, {},
                              t("step_redeem", "zh", name=product.name, amount=cny(amount)),
                              t("step_redeem", "en", name=product.name, amount=cny(amount))))
        return res

    if act is Act.CANCEL_MANDATE:
        if not st.mandates:
            _note(res, "noop_no_mandates")
            return res
        keys: list[str] = []
        if slots.get("scope_all") and not slots.get("mandates") and slots.get("mandate") is None:
            keys = list(st.mandates)
        elif slots.get("scope_all") and slots.get("mandate") is None:
            keys = list(st.mandates)
        else:
            keys = list(slots.get("mandates") or ([slots["mandate"]] if slots.get("mandate") else []))
            if slots.get("scope_all") and not keys:
                keys = list(st.mandates)
        if not keys:
            res.ask = ("mandate", mandate_options(core))
            return res
        seen: set[str] = set()
        for key in keys:
            mid = _resolve_mandate(core, key)
            if mid is None:
                _note(res, "noop_mandate_gone", name=mandate_label(core, key),
                      items=mandate_items(core, "zh"))
                res.notes[-1] = (res.notes[-1][0], t("noop_mandate_gone", "en", name=mandate_label(core, key),
                                                     items=mandate_items(core, "en")))
                continue
            if mid in seen:
                continue
            seen.add(mid)
            name = st.mandates[mid].merchant
            res.steps.append(Step(Act.CANCEL_MANDATE, mid, 0, {},
                                  t("step_cancel", "zh", name=name), t("step_cancel", "en", name=name)))
        return res

    if act in (Act.CARD_FREEZE, Act.CARD_UNFREEZE, Act.CARD_LIMIT):
        cards: list[str] = []
        if slots.get("cards"):
            cards = [c for c in (_resolve_card(core, k) for k in slots["cards"]) if c]
        elif slots.get("card"):
            c = _resolve_card(core, slots["card"])
            cards = [c] if c else []
        elif slots.get("scope_all"):
            cards = list(st.cards)
        if not cards:
            res.ask = ("card", card_options(core))
            return res
        for cid in dict.fromkeys(cards):
            card = st.cards[cid]
            if act is Act.CARD_FREEZE:
                if card.status == "frozen":
                    _note(res, "noop_card_frozen", card=cid)
                    continue
                res.steps.append(Step(act, cid, 0, {}, t("step_freeze", "zh", card=cid),
                                      t("step_freeze", "en", card=cid)))
            elif act is Act.CARD_UNFREEZE:
                if card.status != "frozen":
                    _note(res, "noop_card_active", card=cid)
                    continue
                res.steps.append(Step(act, cid, 0, {}, t("step_unfreeze", "zh", card=cid),
                                      t("step_unfreeze", "en", card=cid)))
            else:
                amount = int(slots.get("amount") or 0)
                if amount <= 0:
                    res.ask = ("amount", [])
                    return res
                which = slots.get("limit_which", "daily")
                res.steps.append(Step(act, cid, amount, {"which": which},
                                      t("step_limit", "zh", card=cid, amount=cny(amount),
                                        which=t("which_single" if which == "single" else "which_daily", "zh")),
                                      t("step_limit", "en", card=cid, amount=cny(amount),
                                        which_en=t("which_single" if which == "single" else "which_daily", "en"))))
        return res

    return res


def _over_grade(res: Resolution, core: BankCore, name: str, code: str, risk: RiskLevel) -> None:
    grade = core.state.customer.risk_tolerance
    alt = _resolve_product(core, grade.value)
    if name:
        _note(res, "noop_over_grade", name=name, code=code, risk=risk.label_zh, risk_en=risk.label_en,
              grade=grade.label_zh, grade_en=grade.label_en)
    else:
        res.notes.append((
            f"您要的是{risk.label_zh}（{risk.name}）的产品，高于您的风险测评结果"
            f"（{grade.label_zh}，{grade.name}），按适当性管理的要求我不能为您购买。",
            f"You asked for a {risk.label_en} ({risk.name}) product, which is above your assessed "
            f"grade ({grade.label_en}, {grade.name}); suitability rules don't allow me to buy it for you."))
    res.refusal = True
    if alt:
        alt_name = core.state.products[alt].name
        res.offer_note = (t("alt_offer", "zh", name=alt_name), t("alt_offer", "en", name=alt_name))
        res.offer = (Act.INVEST, {"product": alt})


# ---------------------------------------------------------------------------
# 追问时给出的选项 / Options shown with a clarifying question
# ---------------------------------------------------------------------------

def payee_options(core: BankCore) -> list[tuple[str, Any]]:
    return [(f"{p.name}（{p.alias or '常用'}）", p.account_no) for p in core.state.payees.values()]


def mandate_options(core: BankCore) -> list[tuple[str, Any]]:
    return [(f"{m.merchant} {cny(m.cap)}/月", m.mandate_id) for m in core.state.mandates.values()]


def card_options(core: BankCore) -> list[tuple[str, Any]]:
    out = []
    for c in core.state.cards.values():
        kind = "信用卡" if c.kind == "credit" else "借记卡"
        state = "已冻结" if c.status == "frozen" else "正常"
        out.append((f"{c.card_id}（{kind}，{state}）", c.card_id))
    return out


# ---------------------------------------------------------------------------
# 信封 / The envelope
# ---------------------------------------------------------------------------

_SCENARIO = {Act.TRANSFER: "智能转账", Act.INVEST: "理财操作", Act.REDEEM: "理财操作",
             Act.CANCEL_MANDATE: "订阅与代扣管理", Act.CARD_FREEZE: "卡片管理",
             Act.CARD_UNFREEZE: "卡片管理", Act.CARD_LIMIT: "卡片管理"}


def compile_spec(steps: list[Step], core: BankCore, customer_id: str, now: int) -> IntentSpec:
    """
    把一组步骤合成一份信封。

    约束取的是**最紧的那个能容纳全部步骤的集合**：支出上限是各步之和，单笔上限是
    最大的那一步，收款人名单是这几步用到的收款人，卡片风控只对点名的卡放开。
    """
    from arbiter.bank.world import ACC_CURRENT

    out_total = sum(s.amount for s in steps if s.act in (Act.TRANSFER, Act.INVEST))
    out_single = max([s.amount for s in steps if s.act in (Act.TRANSFER, Act.INVEST)] or [0])
    payees = tuple(dict.fromkeys(s.target for s in steps if s.act is Act.TRANSFER))
    scoped_cards = tuple(dict.fromkeys(s.target for s in steps
                                       if s.act in (Act.CARD_FREEZE, Act.CARD_UNFREEZE, Act.CARD_LIMIT)))
    weakening = any(s.act in (Act.CARD_UNFREEZE, Act.CARD_LIMIT) for s in steps)
    ceiling = max([s.extra.get("ceiling", 0) for s in steps if s.act is Act.INVEST] or [0])

    inv: list[Invariant] = [Invariant(InvariantKind.MAX_TOTAL_OUT, out_total)]
    if out_single:
        inv.append(Invariant(InvariantKind.MAX_SINGLE_OUT, out_single))
    if payees:
        inv.append(Invariant(InvariantKind.PAYEE_ALLOWLIST, accounts=payees))
    if ceiling:
        inv.append(Invariant(InvariantKind.MAX_RISK_LEVEL, ceiling))
    inv += [Invariant(InvariantKind.NO_NEW_PAYEE), Invariant(InvariantKind.NO_NEW_MANDATE),
            Invariant(InvariantKind.ACCOUNT_SCOPE, accounts=(ACC_CURRENT,)),
            Invariant(InvariantKind.NO_CREDENTIAL_CHANGE)]
    if scoped_cards:
        inv.append(Invariant(InvariantKind.CARD_CONTROL_SCOPE, accounts=scoped_cards))
    if not weakening:
        inv.append(Invariant(InvariantKind.NO_CARD_CONTROL_WEAKENING))

    first_payment = any(s.act is Act.TRANSFER and not s.extra.get("trusted") for s in steps)
    # 权限上限按「这份信封允许发生的最高一级」定：削弱卡片风控到 T3；首次向某个账号
    # 付款到 T2（内核会再请客户确认一次）；其余在额度内直接执行。
    tier = (Tier.T3_CRITICAL if weakening
            else Tier.T2_ELEVATED if first_payment else Tier.T1_BOUNDED)
    scenarios = {_SCENARIO[s.act] for s in steps}
    scenario = scenarios.pop() if len(scenarios) == 1 else "跨场景联动"
    ttl = 600 if any(s.act in (Act.CANCEL_MANDATE, Act.CARD_FREEZE, Act.CARD_UNFREEZE, Act.CARD_LIMIT)
                     for s in steps) else 900

    risk_zh, risk_en = risk_notes(steps, core)
    goal_zh = "；".join(s.desc_zh for s in steps) + "。"
    goal_en = "; ".join(s.desc_en for s in steps) + "."
    return IntentSpec(goal_zh=goal_zh, goal_en=goal_en, invariants=tuple(inv), max_tier=tier,
                      customer_id=customer_id, created_at=now, expires_at=now + ttl,
                      nonce=secrets.token_hex(16), scenario=scenario,
                      risk_zh=tuple(risk_zh), risk_en=tuple(risk_en))


def risk_notes(steps: list[Step], core: BankCore) -> tuple[list[str], list[str]]:
    """
    风险提示 / Risk disclosures — part of what the customer signs.

    每一类操作只写它真正相关的那一条，不堆免责声明：一份写满十条提示的确认页，
    客户一条都不会读。
    """
    zh: list[str] = []
    en: list[str] = []

    def add(a: str, b: str) -> None:
        if a not in zh:
            zh.append(a)
            en.append(b)

    for s in steps:
        if s.act is Act.TRANSFER:
            add("转账一经执行无法撤回，请核对收款人和金额。",
                "A transfer cannot be reversed once executed — check the payee and amount.")
            if not s.extra.get("trusted"):
                add("这个收款人您此前没有付过款。请核实对方身份，谨防电信网络诈骗。",
                    "You have never paid this payee before. Verify who they are — this is how "
                    "most telecom fraud works.")
            if s.amount >= LARGE_TRANSFER or s.amount * 2 >= max(available(core), 1):
                add(f"这是一笔大额转账（{cny(s.amount)}）。如果是有人在电话里催您转账，请先挂断核实。",
                    f"This is a large transfer ({cny(s.amount)}). If someone on the phone is "
                    f"rushing you, hang up and check first.")
        elif s.act is Act.INVEST:
            product = core.state.products.get(s.target)
            grade = core.state.customer.risk_tolerance
            add("理财非存款，产品有风险，投资须谨慎；收益不作保证，以产品说明书为准。",
                "Wealth products are not deposits. They carry risk, returns are not "
                "guaranteed, and the product prospectus governs.")
            if product is not None:
                add(f"「{product.name}」风险等级 {product.risk_level.name}（{product.risk_level.label_zh}），"
                    f"与您的风险测评结果 {grade.name}（{grade.label_zh}）相符。",
                    f"\"{product.name}\" is rated {product.risk_level.name} "
                    f"({product.risk_level.label_en}), within your assessed grade {grade.name} "
                    f"({grade.label_en}).")
        elif s.act is Act.REDEEM:
            add("赎回后资金回到活期账户；部分产品提前赎回可能损失收益，以产品说明书为准。",
                "Redeemed funds return to your current account; redeeming some products early "
                "can forfeit returns, as set out in the prospectus.")
        elif s.act is Act.CANCEL_MANDATE:
            add("取消后对应服务可能在当前计费周期结束后停止，已经扣过的费用不会因此退回。",
                "After cancelling, the service may stop at the end of the current billing "
                "period; charges already taken are not refunded by this.")
        elif s.act is Act.CARD_FREEZE:
            add("冻结后这张卡将无法消费和取现，绑定在这张卡上的自动扣费也可能失败。",
                "Once frozen, the card can't be used for payments or withdrawals, and direct "
                "debits tied to it may fail.")
        elif s.act is Act.CARD_UNFREEZE:
            add("解冻会恢复这张卡的消费能力。如果当初冻结是因为卡片丢失或被盗刷，请确认风险已经排除。",
                "Unfreezing restores the card's spending ability. If it was frozen because it "
                "was lost or misused, make sure that risk is gone.")
        elif s.act is Act.CARD_LIMIT:
            add("提高限额会扩大这张卡可被消费的金额；如果卡片有被盗用的风险，请不要调高。",
                "Raising a limit increases how much can be spent on the card; don't raise it if "
                "the card may be compromised.")
    return zh, en


def build_plan(steps: list[Step], spec: IntentSpec) -> OpPlan:
    from arbiter.bank.world import ACC_CURRENT
    ops: list[Operation] = []
    for s in steps:
        if s.act is Act.TRANSFER:
            ops.append(Operation(OpKind.TRANSFER, ACC_CURRENT, s.target, s.amount, rationale="客户要求的转账"))
        elif s.act is Act.INVEST:
            ops.append(Operation(OpKind.PURCHASE_PRODUCT, ACC_CURRENT, s.target, s.amount,
                                 rationale="客户要求的申购"))
        elif s.act is Act.REDEEM:
            ops.append(Operation(OpKind.REDEEM_PRODUCT, ACC_CURRENT, s.target, s.amount,
                                 rationale="客户要求的赎回"))
        elif s.act is Act.CANCEL_MANDATE:
            ops.append(Operation(OpKind.CANCEL_MANDATE, target=s.target, rationale="客户要求取消"))
        elif s.act is Act.CARD_FREEZE:
            ops.append(Operation(OpKind.FREEZE_CARD, target=s.target, rationale="客户要求冻结"))
        elif s.act is Act.CARD_UNFREEZE:
            ops.append(Operation(OpKind.UNFREEZE_CARD, target=s.target, rationale="客户要求解冻"))
        elif s.act is Act.CARD_LIMIT:
            ops.append(Operation(OpKind.SET_CARD_LIMIT, target=s.target, amount=s.amount,
                                 params={"which": s.extra.get("which", "daily")},
                                 rationale="客户要求调整限额"))
    return OpPlan(operations=tuple(ops), intent_digest=spec.digest(), model="dialogue")


# ---------------------------------------------------------------------------
# 只读查询的信封与计划 / Envelope and plan for read-only queries
# ---------------------------------------------------------------------------

def read_spec(core: BankCore, customer_id: str, now: int) -> IntentSpec:
    from arbiter.bank.world import ACC_CREDIT, ACC_CURRENT
    return IntentSpec(
        goal_zh="查询账户与账单信息，不做任何变更。",
        goal_en="Read account and statement information only.",
        invariants=(
            Invariant(InvariantKind.MAX_TOTAL_OUT, 0),
            Invariant(InvariantKind.ACCOUNT_SCOPE, accounts=(ACC_CURRENT, ACC_CREDIT)),
            Invariant(InvariantKind.NO_CREDENTIAL_CHANGE),
            Invariant(InvariantKind.NO_CARD_CONTROL_WEAKENING),
        ),
        max_tier=Tier.T0_READ, customer_id=customer_id, created_at=now, expires_at=now + 600,
        nonce=secrets.token_hex(16), scenario="账单分析")


def read_plan(act: Act, spec: IntentSpec) -> OpPlan:
    from arbiter.bank.world import ACC_CURRENT
    if act is Act.BALANCE:
        ops = [Operation(OpKind.QUERY_BALANCE, ACC_CURRENT, rationale="查询余额")]
    else:
        ops = [Operation(OpKind.QUERY_TRANSACTIONS, ACC_CURRENT, rationale="读取流水"),
               Operation(OpKind.CATEGORISE_SPENDING, ACC_CURRENT, rationale="按类别归集")]
    return OpPlan(operations=tuple(ops), intent_digest=spec.digest(), model="dialogue")


def describe_payee(core: BankCore, account: str) -> str:
    p = core.resolve_payee(account)
    return f"{p.name} {mask_account(account)}" if p else mask_account(account)
