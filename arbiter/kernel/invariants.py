"""
约束校验 / Invariant evaluation over observed effects.

Every function here takes the *simulated effects* of a plan and the envelope
the customer ratified, and answers one question: did the plan stay inside what
the customer agreed to?

Nothing here reads the plan's stated purpose, the model's rationale, a memo,
or a merchant name. It reads balance deltas, mandate changes, card-control
changes, holdings, credential changes and payees. That separation is the
point. A model can describe a transfer convincingly; it cannot change what the
core is observed to do when the plan runs against it.
"""

from __future__ import annotations

from arbiter.types import (
    UNCAPPED,
    EffectSet,
    Finding,
    IntentSpec,
    Invariant,
    InvariantKind,
    RiskLevel,
    cny,
    mask_account,
)


def evaluate(spec: IntentSpec, fx: EffectSet, now: int) -> list[Finding]:
    findings: list[Finding] = []
    for inv in spec.invariants:
        handler = _HANDLERS.get(inv.kind)
        if handler is None:
            # An invariant nobody wrote an evaluator for is not assumed to hold.
            findings.append(Finding(
                "INV_UNKNOWN", "high",
                f"约束 {inv.kind} 没有对应的校验器，不能假定其成立",
                f"no evaluator for invariant {inv.kind}; refusing to assume it holds",
                {"kind": str(inv.kind)},
            ))
            continue
        findings.extend(handler(inv, spec, fx, now))
    return findings


# ---------------------------------------------------------------------------

def _max_total_out(inv: Invariant, spec: IntentSpec, fx: EffectSet, now: int) -> list[Finding]:
    limit = inv.limit or 0
    # Net, not gross: moving money between the customer's own accounts is not
    # spending, and charging it against their cap would be wrong in the
    # direction that blocks legitimate work.
    outflow = fx.total_outflow()
    if outflow > limit:
        return [Finding(
            "INV_MAX_TOTAL_OUT", "critical",
            f"计划支出 {cny(outflow)}，超出已授权上限 {cny(limit)}",
            f"plan pays out {cny(outflow)}, over the ratified ceiling of {cny(limit)}",
            {"observed_fen": outflow, "limit_fen": limit, "excess_fen": outflow - limit},
        )]
    return []


def _max_single_out(inv: Invariant, spec: IntentSpec, fx: EffectSet, now: int) -> list[Finding]:
    limit = inv.limit or 0
    out = []
    for bal in fx.balances:
        if bal.delta < 0 and -bal.delta > limit:
            out.append(Finding(
                "INV_MAX_SINGLE_OUT", "critical",
                f"单笔支出 {cny(-bal.delta)} 超出单笔上限 {cny(limit)}",
                f"a single payment of {cny(-bal.delta)} exceeds the per-payment cap "
                f"of {cny(limit)}",
                {"amount_fen": -bal.delta, "limit_fen": limit,
                 "account": mask_account(bal.account)},
            ))
    return out


def _payee_allowlist(inv: Invariant, spec: IntentSpec, fx: EffectSet, now: int) -> list[Finding]:
    allowed = {str(a) for a in inv.accounts}
    out = []
    for payee in sorted(set(fx.payees_paid)):
        if payee in allowed:
            continue
        out.append(Finding(
            "INV_PAYEE_NOT_ALLOWED", "critical",
            f"计划向 {mask_account(payee)} 付款，该收款人不在客户授权名单中",
            f"plan pays {mask_account(payee)}, who is not on the ratified payee list",
            {"payee": mask_account(payee), "allowlist_size": len(allowed)},
        ))
    return out


def _no_new_payee(inv: Invariant, spec: IntentSpec, fx: EffectSet, now: int) -> list[Finding]:
    return [
        Finding(
            "INV_NEW_PAYEE", "critical",
            f"计划新增收款人 {mask_account(payee)}，该授权不允许新增收款人",
            f"plan adds payee {mask_account(payee)}; the envelope permits no new payees",
            {"payee": mask_account(payee)},
        )
        for payee in sorted(set(fx.payees_added))
    ]


def _no_new_mandate(inv: Invariant, spec: IntentSpec, fx: EffectSet, now: int) -> list[Finding]:
    out = []
    for mandate in fx.mandates:
        if not (mandate.created or mandate.widened):
            continue
        out.append(Finding(
            "INV_NEW_MANDATE", "critical",
            f"计划为「{mandate.merchant}」{'新建' if mandate.created else '扩大'}代扣授权，"
            "该授权不允许任何新增代扣",
            f"plan {'creates' if mandate.created else 'widens'} a direct-debit mandate for "
            f"'{mandate.merchant}'; the envelope permits none",
            {"mandate_id": mandate.mandate_id, "merchant": mandate.merchant,
             "cap_after_fen": mandate.cap_after, "uncapped": mandate.uncapped},
        ))
    return out


def _mandate_cap(inv: Invariant, spec: IntentSpec, fx: EffectSet, now: int) -> list[Finding]:
    limit = inv.limit or 0
    out = []
    for mandate in fx.mandates:
        if not (mandate.created or mandate.widened):
            continue
        if mandate.uncapped:
            out.append(Finding(
                "INV_UNCAPPED_MANDATE", "critical",
                f"计划为「{mandate.merchant}」开通无上限代扣授权；"
                "无上限授权永远不在任何信封范围内",
                f"plan grants an uncapped direct debit to '{mandate.merchant}'; "
                "an uncapped mandate is never inside any envelope",
                {"mandate_id": mandate.mandate_id, "merchant": mandate.merchant},
            ))
        elif mandate.cap_after > limit:
            out.append(Finding(
                "INV_MANDATE_CAP", "high",
                f"「{mandate.merchant}」代扣额度 {cny(mandate.cap_after)} 超出授权上限 {cny(limit)}",
                f"mandate for '{mandate.merchant}' is capped at {cny(mandate.cap_after)}, "
                f"over the ratified ceiling of {cny(limit)}",
                {"mandate_id": mandate.mandate_id, "cap_fen": mandate.cap_after,
                 "limit_fen": limit},
            ))
    return out


def _max_risk_level(inv: Invariant, spec: IntentSpec, fx: EffectSet, now: int) -> list[Finding]:
    """
    适当性管理 / Suitability.

    Selling a customer a product above their assessed risk grade is a
    regulated mis-selling offence in China, not merely a poor outcome, so the
    customer's grade is enforced as a ceiling rather than offered as advice.
    """
    ceiling = inv.limit or 1
    out = []
    for holding in fx.holdings:
        if holding.amount <= 0:        # a redemption reduces risk
            continue
        if holding.risk_level > ceiling:
            got, want = RiskLevel(holding.risk_level), RiskLevel(ceiling)
            out.append(Finding(
                "INV_RISK_LEVEL", "critical",
                f"计划买入「{holding.product_name}」（{got.name} {got.label_zh}），"
                f"超出客户风险承受等级 {want.name}（{want.label_zh}），构成不当销售",
                f"plan buys '{holding.product_name}' at risk grade {got.name} "
                f"({got.label_en}), above the customer's assessed tolerance {want.name} "
                f"({want.label_en}) -- this is mis-selling",
                {"product": holding.product_code, "product_risk": holding.risk_level,
                 "customer_ceiling": ceiling, "amount_fen": holding.amount},
            ))
    return out


def _card_control_scope(inv: Invariant, spec: IntentSpec, fx: EffectSet, now: int) -> list[Finding]:
    """
    客户点名授权调整某张卡的风控 / the customer named the card whose controls may change.

    这条约束存在的理由：解冻自己的卡是一件正当的事，但它在观测上是一次
    「削弱风控」。如果每一次削弱都一律拒绝，客户就永远解不开自己的卡——
    而他会转头去用网银，被绕开的控制保护不了任何人。

    所以把它变成一件**可以被点名授权**的事：信封写明是哪张卡，内核据此把
    它当成「已授权的敏感操作」转人工确认，而不是「没人提过的变更」直接拒绝。
    没写进信封的卡，照旧拒绝——S03 那种把解冻藏进转账计划里的手法不受影响。
    """
    allowed = set(inv.accounts)
    out = []
    for change in fx.card_controls:
        if change.card_id in allowed:
            continue
        out.append(Finding(
            "INV_CARD_OUT_OF_SCOPE", "critical",
            f"计划调整了卡片 {change.card_id} 的风控，而授权只涵盖另外的卡片",
            f"plan changes controls on card {change.card_id}, which this "
            f"authorisation does not cover",
            {"card": change.card_id, "control": change.control},
        ))
    return out


def _no_card_control_weakening(inv: Invariant, spec: IntentSpec, fx: EffectSet, now: int) -> list[Finding]:
    out = []
    for change in fx.card_controls:
        if not change.weakening:
            continue
        out.append(Finding(
            "INV_CARD_WEAKENING", "critical",
            f"计划削弱卡片 {change.card_id} 的风控设置（{change.control}：{change.before} → {change.after}）",
            f"plan weakens a protection on card {change.card_id} "
            f"({change.control}: {change.before} → {change.after})",
            {"card": change.card_id, "control": change.control,
             "before": str(change.before), "after": str(change.after)},
        ))
    return out


def _no_credential_change(inv: Invariant, spec: IntentSpec, fx: EffectSet, now: int) -> list[Finding]:
    out = []
    for change in fx.credentials:
        out.append(Finding(
            "INV_CREDENTIAL_CHANGE", "critical",
            f"计划变更账户凭证（{change.field_name}：{change.before_masked} → {change.after_masked}）；"
            "凭证变更是账户接管的第一步",
            f"plan changes an account credential ({change.field_name}: "
            f"{change.before_masked} → {change.after_masked}); this is the first step "
            "of an account takeover",
            {"field": change.field_name, "before": change.before_masked,
             "after": change.after_masked},
        ))
    return out


def _account_scope(inv: Invariant, spec: IntentSpec, fx: EffectSet, now: int) -> list[Finding]:
    allowed = {str(a) for a in inv.accounts}
    out = []
    for account in sorted(set(fx.accounts_touched)):
        if account in allowed:
            continue
        out.append(Finding(
            "INV_ACCOUNT_SCOPE", "critical",
            f"计划操作账户 {mask_account(account)}，超出授权范围",
            f"plan operates on account {mask_account(account)}, outside the ratified scope",
            {"account": mask_account(account)},
        ))
    return out


def _expiry(inv: Invariant, spec: IntentSpec, fx: EffectSet, now: int) -> list[Finding]:
    limit = inv.limit if inv.limit is not None else spec.expires_at
    if now > limit:
        return [Finding(
            "INV_EXPIRED", "high",
            f"授权已于 {limit} 过期，当前时间 {now}",
            f"the ratified envelope expired at {limit}; now {now}",
            {"expires_at": limit, "now": now, "late_by_s": now - limit},
        )]
    return []


_HANDLERS = {
    InvariantKind.MAX_TOTAL_OUT: _max_total_out,
    InvariantKind.MAX_SINGLE_OUT: _max_single_out,
    InvariantKind.PAYEE_ALLOWLIST: _payee_allowlist,
    InvariantKind.NO_NEW_PAYEE: _no_new_payee,
    InvariantKind.NO_NEW_MANDATE: _no_new_mandate,
    InvariantKind.MANDATE_CAP: _mandate_cap,
    InvariantKind.MAX_RISK_LEVEL: _max_risk_level,
    InvariantKind.CARD_CONTROL_SCOPE: _card_control_scope,
    InvariantKind.NO_CARD_CONTROL_WEAKENING: _no_card_control_weakening,
    InvariantKind.NO_CREDENTIAL_CHANGE: _no_credential_change,
    InvariantKind.ACCOUNT_SCOPE: _account_scope,
    InvariantKind.EXPIRY: _expiry,
}


# ---------------------------------------------------------------------------
# 未授权效果 / Effects the envelope never contemplated
# ---------------------------------------------------------------------------

_EFFECT_GUARDS = (
    ("mandates", InvariantKind.NO_NEW_MANDATE, InvariantKind.MANDATE_CAP),
    ("holdings", InvariantKind.MAX_RISK_LEVEL,),
    ("card_controls", InvariantKind.NO_CARD_CONTROL_WEAKENING,
                      InvariantKind.CARD_CONTROL_SCOPE),
    ("credentials", InvariantKind.NO_CREDENTIAL_CHANGE,),
)


def unratified_effects(spec: IntentSpec, fx: EffectSet) -> list[Finding]:
    """
    未授权效果一律上报 / No effect class passes merely because nobody named it.

    An envelope is a set of limits, and a limit only binds what it names.
    Without this check a plan producing an effect no invariant mentions would
    satisfy every stated constraint trivially -- which is how a "transfer"
    envelope ends up authorising a subscription.

    These are raised as `high`, which routes the plan to the customer rather
    than refusing it outright. That is deliberate and it is where two
    principles meet: deny-by-default says an unexamined effect must not pass
    silently, while the tier model says a reasonable operation the envelope
    simply did not anticipate deserves a question, not a wall. An assistant
    that refuses everything it was not explicitly pre-authorised for is one
    customers route around, and a control people route around protects nobody.

    The genuinely irreversible cases do not rely on this check at all. An
    uncapped mandate, a weakened card control, a product above the customer's
    risk grade and a credential change each have their own `critical` finding
    and reach T3 independently, so escalating here never softens any of them.
    The one exception below -- money leaving with no spending cap whatsoever --
    stays critical, because asking a human to approve an unbounded payment is
    not a meaningful question either.
    """
    kinds = {inv.kind for inv in spec.invariants}
    out: list[Finding] = []

    labels_zh = {"mandates": "代扣授权", "holdings": "理财持仓",
                 "card_controls": "卡片风控设置", "credentials": "账户凭证"}
    labels_en = {"mandates": "direct-debit mandates", "holdings": "wealth holdings",
                 "card_controls": "card controls", "credentials": "account credentials"}

    for attribute, *guards in _EFFECT_GUARDS:
        effects = getattr(fx, attribute)
        if not effects:
            continue
        if attribute == "mandates" and all(m.cancelled for m in effects):
            continue        # cancelling a mandate only ever reduces exposure
        if attribute == "holdings" and all(h.amount <= 0 for h in effects):
            continue        # redeeming only ever reduces risk
        if attribute == "card_controls" and all(not c.weakening for c in effects):
            continue        # strengthening a control needs no authority
        if any(g in kinds for g in guards):
            continue
        out.append(Finding(
            "INV_UNRATIFIED_EFFECT", "high",
            f"计划变更了「{labels_zh[attribute]}」，而客户签署的授权从未提及该类操作",
            f"plan changes {labels_en[attribute]}, which the ratified envelope never "
            "mentioned",
            {"effect_class": attribute, "count": len(effects)},
        ))

    # 资金流出但无任何金额约束 / money leaves with no governing cap at all
    money_guards = {InvariantKind.MAX_TOTAL_OUT, InvariantKind.MAX_SINGLE_OUT}
    if fx.total_outflow() > 0 and not (money_guards & kinds):
        out.append(Finding(
            "INV_UNCAPPED_OUTFLOW", "critical",
            f"计划支出 {cny(fx.total_outflow())}，而授权中没有任何金额上限约束",
            f"plan pays out {cny(fx.total_outflow())} with no spending cap in the envelope",
            {"outflow_fen": fx.total_outflow()},
        ))

    return out
