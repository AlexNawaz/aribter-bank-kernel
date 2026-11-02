"""
分级权限机制 / Capability tier resolution.

A tier is assigned from what a plan is *observed* to do, never from what it
says it does, and never from anything the agent happens to have been told.

The organising principle: **a tier measures surprise.** An effect the ratified
envelope explicitly bounded -- a payment to a payee the customer allowlisted,
a mandate they capped -- is not an escalation. It is the agent doing the job it
was authorised to do, and prompting again would train the customer to approve
reflexively, which is how confirmation dialogs stop working. An effect the
envelope never anticipated escalates, however routine it looks.

Authority flows one way: it originates in a signed envelope and nowhere else.
Nothing the agent reads, and nothing that arrives in the customer's account,
can raise a ceiling.
"""

from __future__ import annotations

from arbiter.types import (
    EffectSet,
    Finding,
    IntentSpec,
    InvariantKind,
    Tier,
    cny,
    mask_account,
)


def resolve(spec: IntentSpec, fx: EffectSet, lookalike_hits: int = 0,
            first_payments: list[str] | tuple[str, ...] = ()) -> tuple[Tier, list[Finding]]:
    tier = Tier.T0_READ
    findings: list[Finding] = []

    def raise_to(level: Tier, code: str, severity: str, zh: str, en: str, **ev: object) -> None:
        nonlocal tier
        if level > tier:
            tier = level
        findings.append(Finding(code, severity, zh, en, dict(ev)))  # type: ignore[arg-type]

    # --- T3 高危 / critical ------------------------------------------------
    for change in fx.credentials:
        raise_to(Tier.T3_CRITICAL, "TIER_CREDENTIAL", "critical",
                 f"变更账户凭证（{change.field_name}）",
                 f"account credential change ({change.field_name})",
                 field=change.field_name)

    # 客户在信封里点名授权调整的卡片 / cards whose controls the envelope names
    scoped_cards: set[str] = set()
    for inv in spec.invariants:
        if inv.kind is InvariantKind.CARD_CONTROL_SCOPE:
            scoped_cards.update(inv.accounts)

    for change in fx.card_controls:
        if change.weakening:
            # 分级衡量的是**意外**，不是抽象的危险。
            #
            # 原先这里一律判 critical，而 critical 直接等于拒绝——于是客户
            # 永远解不开自己的卡。但「解冻 CARD-8847」这句话本身就是客户提
            # 出来的，信封上也写明了是哪一张：这不是意外，是他要办的事。
            #
            # 点名授权过的削弱，升到 T3 并转人工确认；没点过名的（比如把解冻
            # 藏在一个转账计划里），仍然是 critical，直接拒绝。
            named = change.card_id in scoped_cards
            raise_to(Tier.T3_CRITICAL, "TIER_CARD_WEAKENING",
                     "high" if named else "critical",
                     f"削弱卡片 {change.card_id} 的风控（{change.control}）"
                     + ("，该卡已在授权中点名" if named else ""),
                     f"weakens a protection on card {change.card_id} ({change.control})"
                     + (", named in the authorisation" if named else ""),
                     card=change.card_id, control=change.control, named=named)

    for mandate in fx.mandates:
        if mandate.uncapped and (mandate.created or mandate.widened):
            raise_to(Tier.T3_CRITICAL, "TIER_UNCAPPED_MANDATE", "critical",
                     f"为「{mandate.merchant}」开通无上限代扣",
                     f"uncapped direct debit for '{mandate.merchant}'",
                     merchant=mandate.merchant)

    if lookalike_hits:
        raise_to(Tier.T3_CRITICAL, "TIER_LOOKALIKE", "critical",
                 f"{lookalike_hits} 个收款账号与客户已知账号高度相似但并不相同，"
                 "这是账号混淆攻击的典型特征",
                 f"{lookalike_hits} destination account{'' if lookalike_hits == 1 else 's'} closely "
                 "resemble a known account without matching it, the signature of account-number poisoning",
                 hits=lookalike_hits)

    ceiling = _risk_ceiling(spec)
    for holding in fx.holdings:
        if holding.amount > 0 and ceiling is not None and holding.risk_level > ceiling:
            raise_to(Tier.T3_CRITICAL, "TIER_RISK_EXCEEDED", "critical",
                     f"买入超出客户风险等级的产品「{holding.product_name}」",
                     f"buys '{holding.product_name}', above the customer's risk grade",
                     product=holding.product_code, risk=holding.risk_level)

    # --- T2 需确认 / elevated ----------------------------------------------
    for payee in first_payments:
        # 信封点名了这个账号，所以它不是「意外」——但首次付款是电信诈骗最常见的
        # 形态，客户在电话里被人催着转账时，签信封那一下往往是被人念着点的。
        # 多一次确认、配一段反诈提示，成本是一轮对话。
        raise_to(Tier.T2_ELEVATED, "TIER_FIRST_PAYMENT", "medium",
                 f"首次向 {mask_account(payee)} 付款（此前从未付过款的账号）",
                 f"first payment to {mask_account(payee)}, an account never paid before",
                 payee=mask_account(payee))

    for payee in set(fx.payees_added):
        raise_to(Tier.T2_ELEVATED, "TIER_NEW_PAYEE", "medium",
                 f"新增收款人 {mask_account(payee)}",
                 f"adds payee {mask_account(payee)}",
                 payee=mask_account(payee))

    for mandate in fx.mandates:
        if not (mandate.created or mandate.widened) or mandate.uncapped:
            continue
        if _mandate_is_ratified(spec, mandate):
            findings.append(Finding(
                "TIER_MANDATE_RATIFIED", "info",
                f"「{mandate.merchant}」代扣额度 {cny(mandate.cap_after)} 在客户已签署的上限之内",
                f"mandate for '{mandate.merchant}' at {cny(mandate.cap_after)} is within a "
                "cap the customer ratified",
                {"merchant": mandate.merchant, "cap_fen": mandate.cap_after},
            ))
            if tier < Tier.T1_BOUNDED:
                tier = Tier.T1_BOUNDED
        else:
            raise_to(Tier.T2_ELEVATED, "TIER_MANDATE", "medium",
                     f"为「{mandate.merchant}」建立代扣授权，授权信封未对此设限",
                     f"creates a mandate for '{mandate.merchant}'; the envelope does not "
                     "bound mandates",
                     merchant=mandate.merchant, cap_fen=mandate.cap_after)

    for change in fx.card_controls:
        if not change.weakening:
            # Strengthening a control -- freezing a card, lowering a limit --
            # is the direction a worried customer wants to be fast, so it is
            # not treated as an escalation.
            findings.append(Finding(
                "TIER_CARD_STRENGTHENING", "info",
                f"加强卡片 {change.card_id} 的风控（{change.control}）",
                f"strengthens a control on card {change.card_id} ({change.control})",
                {"card": change.card_id, "control": change.control},
            ))
            if tier < Tier.T1_BOUNDED:
                tier = Tier.T1_BOUNDED

    for holding in fx.holdings:
        if holding.amount > 0 and not (ceiling is not None and holding.risk_level > ceiling):
            if not _has(spec, InvariantKind.MAX_RISK_LEVEL):
                raise_to(Tier.T2_ELEVATED, "TIER_PURCHASE", "medium",
                         f"买入理财产品「{holding.product_name}」，授权信封未约定风险等级",
                         f"buys '{holding.product_name}'; the envelope sets no risk ceiling",
                         product=holding.product_code)
            elif tier < Tier.T1_BOUNDED:
                tier = Tier.T1_BOUNDED

    # --- T1 限额内 / bounded ------------------------------------------------
    if fx.total_outflow() > 0 and tier < Tier.T1_BOUNDED:
        raise_to(Tier.T1_BOUNDED, "TIER_VALUE_MOVED", "info",
                 f"资金流出 {cny(fx.total_outflow())}",
                 f"moves {cny(fx.total_outflow())} out", amount_fen=fx.total_outflow())

    if any(m.cancelled for m in fx.mandates) and tier < Tier.T1_BOUNDED:
        tier = Tier.T1_BOUNDED

    return tier, findings


# ---------------------------------------------------------------------------

def _has(spec: IntentSpec, kind: InvariantKind) -> bool:
    return any(inv.kind is kind for inv in spec.invariants)


def _risk_ceiling(spec: IntentSpec) -> int | None:
    for inv in spec.invariants:
        if inv.kind is InvariantKind.MAX_RISK_LEVEL and inv.limit is not None:
            return inv.limit
    return None


def _mandate_is_ratified(spec: IntentSpec, mandate) -> bool:
    """
    True when the customer's envelope specifically anticipated this mandate.

    Requires a MANDATE_CAP invariant that the resulting cap respects. A
    standing authorisation to pull money, in an amount the customer read and
    signed, is not something to interrupt them about; one they never discussed
    is.
    """
    capped = False
    for inv in spec.invariants:
        if inv.kind is not InvariantKind.MANDATE_CAP:
            continue
        if inv.limit is None or mandate.cap_after > inv.limit:
            return False
        capped = True
    return capped
