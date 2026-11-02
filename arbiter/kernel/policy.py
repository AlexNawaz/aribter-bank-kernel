"""
静态策略检查 / Structural checks that run before simulation.

Cheap, structural checks on the plan as proposed: well-formedness, the
envelope binding, expiry, and shape limits. Running them first means a
malformed or obviously out-of-scope plan never reaches the core simulator, and
the reasons a plan was refused read in the order a reviewer would ask them.

Everything here is a necessary condition, never a sufficient one. Passing
static policy earns a plan nothing except the right to be simulated.
"""

from __future__ import annotations

from arbiter.types import (
    Finding,
    IntentSpec,
    OpKind,
    OpPlan,
    RatifiedIntent,
    is_account_no,
)

MAX_OPERATIONS = 12
MAX_RATIONALE_CHARS = 500


def check(ratified: RatifiedIntent, plan: OpPlan, now: int) -> list[Finding]:
    findings: list[Finding] = []
    spec: IntentSpec = ratified.spec

    # The plan must name the exact envelope the customer signed. Without this,
    # a plan built for a permissive envelope could be replayed against a
    # restrictive one.
    if plan.intent_digest != spec.digest():
        findings.append(Finding(
            "POL_INTENT_MISMATCH", "critical",
            "计划绑定的授权信封与客户实际签署的不一致",
            "plan is bound to a different envelope than the one ratified",
            {"plan_binds": plan.intent_digest[:16], "ratified": spec.digest()[:16]},
        ))

    if now > spec.expires_at:
        findings.append(Finding(
            "POL_EXPIRED", "high",
            f"授权已于 {spec.expires_at} 过期，当前 {now}",
            f"envelope expired at {spec.expires_at}; now {now}",
            {"expires_at": spec.expires_at, "now": now},
        ))

    if ratified.signer != spec.customer_id:
        findings.append(Finding(
            "POL_SIGNER_MISMATCH", "critical",
            "授权签署人与账户持有人不一致",
            "the envelope was ratified by someone other than the account holder",
            {"signer": ratified.signer, "customer": spec.customer_id},
        ))

    if not plan.operations:
        findings.append(Finding(
            "POL_EMPTY_PLAN", "low", "计划不包含任何操作", "plan contains no operations", {},
        ))

    if len(plan.operations) > MAX_OPERATIONS:
        findings.append(Finding(
            "POL_TOO_MANY_OPS", "high",
            f"计划包含 {len(plan.operations)} 个操作，超出结构上限 {MAX_OPERATIONS}",
            f"plan has {len(plan.operations)} operations, over the structural limit of "
            f"{MAX_OPERATIONS}",
            {"count": len(plan.operations), "limit": MAX_OPERATIONS},
        ))

    for index, op in enumerate(plan.operations):
        if op.amount < 0:
            findings.append(Finding(
                "POL_NEGATIVE_AMOUNT", "critical",
                f"操作 {index} 的金额为负数",
                f"operation {index} carries a negative amount",
                {"index": index, "amount_fen": op.amount},
            ))

        if op.kind in (OpKind.TRANSFER, OpKind.SCHEDULE_TRANSFER):
            if not is_account_no(op.target):
                findings.append(Finding(
                    "POL_BAD_ACCOUNT", "critical",
                    f"操作 {index} 的收款账号格式非法",
                    f"operation {index} has a malformed destination account",
                    {"index": index, "target": str(op.target)[:24]},
                ))
            if op.amount <= 0:
                findings.append(Finding(
                    "POL_ZERO_TRANSFER", "medium",
                    f"操作 {index} 为零金额转账",
                    f"operation {index} is a zero-amount transfer",
                    {"index": index},
                ))

        if op.kind in (OpKind.CHANGE_PHONE, OpKind.CHANGE_EMAIL):
            # Credential changes are refused structurally rather than
            # case-by-case. There is no banking task in the six scenarios that
            # legitimately requires an agent to move a customer's phone number,
            # and every account takeover begins with exactly this step.
            findings.append(Finding(
                "POL_CREDENTIAL_OP", "critical",
                f"操作 {index} 试图变更账户凭证；智能体无权执行凭证变更",
                f"operation {index} attempts a credential change; the agent has no "
                "authority to perform one under any envelope",
                {"index": index, "operation": op.kind.value},
            ))

        if len(op.rationale) > MAX_RATIONALE_CHARS:
            findings.append(Finding(
                "POL_RATIONALE_SIZE", "low",
                f"操作 {index} 的说明文本过长",
                f"operation {index} has an oversized rationale",
                {"index": index, "chars": len(op.rationale)},
            ))

    return findings
