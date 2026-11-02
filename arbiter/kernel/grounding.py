"""
幻觉防护：实体接地与事实核验 / Hallucination defence: grounding and fact-checking.

The 银行 AI 智能体 brief names 幻觉防护 as a core requirement, and it is the
one most easily waved at. The usual answers -- lower the temperature, add
"do not make things up" to the prompt, ask a second model to check -- are all
probabilistic, and a wrong account number is not a quality problem. It is a
payment to a stranger.

This module takes a different position: **every entity a plan names must
resolve against real core banking state, and every factual claim the agent
makes to the customer must match what the core actually returns.** A fund code
that does not exist, an account the customer does not own, a mandate id the
agent invented, a balance it stated from memory -- each is a refusal, decided
by lookup rather than by judgement.

Three checks:

  实体接地 entity grounding   does every referenced entity exist, and does it
                              belong to this customer?
  事实核验 claim verification  do the agent's stated facts match the core?
  数额一致 amount consistency  does the amount it told the customer match the
                              amount it is about to move?

None of them involve a model, and none can be argued with.
"""

from __future__ import annotations

from typing import Any

from arbiter.bank.core import BankCore
from arbiter.types import (
    Finding,
    IntentSpec,
    OpKind,
    OpPlan,
    cny,
    is_account_no,
    mask_account,
)

# Operations and the entity each one's `target` must resolve to.
_TARGET_RESOLVER = {
    OpKind.PURCHASE_PRODUCT: "product",
    OpKind.REDEEM_PRODUCT: "product",
    OpKind.FREEZE_CARD: "card",
    OpKind.UNFREEZE_CARD: "card",
    OpKind.SET_CARD_LIMIT: "card",
    OpKind.SET_ONLINE_PAYMENT: "card",
    OpKind.MODIFY_MANDATE: "mandate",
    OpKind.CANCEL_MANDATE: "mandate",
}

_RESOLVER_LABEL_ZH = {
    "product": "理财产品", "card": "卡片", "mandate": "代扣授权",
    "account": "账户", "payee": "收款人",
}
_RESOLVER_LABEL_EN = {
    "product": "wealth product", "card": "card", "mandate": "mandate",
    "account": "account", "payee": "payee",
}


def check_entities(core: BankCore, plan: OpPlan, spec: IntentSpec) -> list[Finding]:
    """
    实体接地 / Every entity the plan names must exist in core banking state.

    This is the check that turns a hallucinated reference into a blocked plan
    rather than a failed call halfway through a sequence -- which matters,
    because a sequence that fails halfway has already done the first half.
    """
    findings: list[Finding] = []

    for index, op in enumerate(plan.operations):
        # --- the source account -----------------------------------------
        if op.account:
            account = core.resolve_account(op.account)
            if account is None:
                findings.append(Finding(
                    "GRD_UNKNOWN_ACCOUNT", "critical",
                    f"操作 {index} 引用的账户 {mask_account(op.account)} 不存在于客户名下，"
                    "属于模型虚构的实体",
                    f"operation {index} names account {mask_account(op.account)}, which this "
                    "customer does not hold; the model invented it",
                    {"index": index, "account": mask_account(op.account),
                     "operation": op.kind.value},
                ))

        # --- the target entity ------------------------------------------
        resolver = _TARGET_RESOLVER.get(op.kind)
        if resolver and op.target:
            resolved = {
                "product": core.resolve_product,
                "card": core.resolve_card,
                "mandate": core.resolve_mandate,
            }[resolver](op.target)
            if resolved is None:
                findings.append(Finding(
                    "GRD_UNKNOWN_ENTITY", "critical",
                    f"操作 {index} 引用的{_RESOLVER_LABEL_ZH[resolver]} {op.target} 不存在，"
                    "模型虚构了该实体",
                    f"operation {index} names {_RESOLVER_LABEL_EN[resolver]} {op.target}, "
                    "which does not exist; the model invented it",
                    {"index": index, "entity_type": resolver, "reference": op.target,
                     "operation": op.kind.value},
                ))

        # --- transfer destinations --------------------------------------
        if op.kind in (OpKind.TRANSFER, OpKind.SCHEDULE_TRANSFER):
            if not is_account_no(op.target):
                findings.append(Finding(
                    "GRD_MALFORMED_ACCOUNT", "critical",
                    f"操作 {index} 的收款账号 {op.target} 格式非法",
                    f"operation {index} has a malformed destination account {op.target}",
                    {"index": index, "target": str(op.target)},
                ))
            elif core.resolve_payee(op.target) is None and core.resolve_account(op.target) is None:
                # Not a hallucination on its own -- a customer may genuinely pay
                # someone new -- so this escalates for confirmation rather than
                # refusing. The allowlist invariant is what refuses, when the
                # envelope declared one.
                findings.append(Finding(
                    "GRD_UNKNOWN_PAYEE", "medium",
                    f"操作 {index} 的收款人 {mask_account(op.target)} 不在客户收款人名单中",
                    f"operation {index} pays {mask_account(op.target)}, who is not in the "
                    "customer's payee book",
                    {"index": index, "payee": mask_account(op.target)},
                ))

    return findings


def check_claims(core: BankCore, plan: OpPlan) -> list[Finding]:
    """
    事实核验 / The agent's stated facts must match the core.

    `claimed_facts` is what the agent intends to tell the customer -- balances,
    counts, totals. Each is re-read from the core and compared. An agent that
    reports a balance from memory, or rounds one, is corrected before the
    customer ever sees the number.
    """
    findings: list[Finding] = []
    facts: dict[str, Any] = plan.claimed_facts or {}

    for key, claimed in facts.items():
        if key.startswith("balance:"):
            account_no = key.split(":", 1)[1]
            actual = core.balance_of(account_no)
            if actual is None:
                findings.append(Finding(
                    "GRD_CLAIM_UNKNOWN_ACCOUNT", "critical",
                    f"智能体声称账户 {mask_account(account_no)} 的余额，但该账户不存在",
                    f"the agent stated a balance for {mask_account(account_no)}, "
                    "which does not exist",
                    {"account": mask_account(account_no), "claimed": claimed},
                ))
            elif int(claimed) != actual:
                # Critical rather than escalated. There is no sense in asking a
                # customer to approve a false statement about their own
                # account: the whole reason the number is wrong is that they
                # cannot see what it should be. The response is withheld and
                # the agent re-reads the core.
                findings.append(Finding(
                    "GRD_CLAIM_MISMATCH", "critical",
                    f"智能体声称余额为 {cny(int(claimed))}，核心系统实际为 {cny(actual)}",
                    f"the agent stated a balance of {cny(int(claimed))}; the core says "
                    f"{cny(actual)}",
                    {"account": mask_account(account_no),
                     "claimed_fen": int(claimed), "actual_fen": actual,
                     "difference_fen": int(claimed) - actual},
                ))

        elif key == "mandate_count":
            actual_count = len(core.state.mandates)
            if int(claimed) != actual_count:
                findings.append(Finding(
                    "GRD_CLAIM_MISMATCH", "critical",
                    f"智能体声称有 {claimed} 项代扣授权，实际为 {actual_count} 项",
                    f"the agent stated {claimed} mandates; the core has {actual_count}",
                    {"claimed": int(claimed), "actual": actual_count},
                ))

        elif key == "holding_total":
            actual_total = sum(h.amount for h in core.state.holdings.values())
            if int(claimed) != actual_total:
                findings.append(Finding(
                    "GRD_CLAIM_MISMATCH", "critical",
                    f"智能体声称持仓合计 {cny(int(claimed))}，实际为 {cny(actual_total)}",
                    f"the agent stated holdings of {cny(int(claimed))}; the core says "
                    f"{cny(actual_total)}",
                    {"claimed_fen": int(claimed), "actual_fen": actual_total},
                ))

    return findings


def check_amount_consistency(plan: OpPlan) -> list[Finding]:
    """
    数额一致 / What the agent says it will move must equal what it moves.

    A plan that tells the customer "转账 500 元" while executing 5,000 is not a
    rounding error, and it is not caught by any limit that happens to sit above
    5,000. The stated figure travels in `claimed_facts["transfer_total"]` and is
    compared against the operations themselves.
    """
    claimed = (plan.claimed_facts or {}).get("transfer_total")
    if claimed is None:
        return []

    planned = sum(
        op.amount for op in plan.operations
        if op.kind in (OpKind.TRANSFER, OpKind.SCHEDULE_TRANSFER)
    )
    if int(claimed) == planned:
        return []

    return [Finding(
        "GRD_AMOUNT_MISMATCH", "critical",
        f"智能体向客户声称转账 {cny(int(claimed))}，实际计划转出 {cny(planned)}",
        f"the agent told the customer it would transfer {cny(int(claimed))} while "
        f"planning to move {cny(planned)}",
        {"claimed_fen": int(claimed), "planned_fen": planned,
         "difference_fen": planned - int(claimed)},
    )]


def check_all(core: BankCore, plan: OpPlan, spec: IntentSpec) -> list[Finding]:
    return (
        check_entities(core, plan, spec)
        + check_claims(core, plan)
        + check_amount_consistency(plan)
    )
