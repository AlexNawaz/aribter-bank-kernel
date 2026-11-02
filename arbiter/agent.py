"""
银行智能体装配 / The assembled banking agent.

The only module holding references to both the untrusted side (compiler,
planner, model) and the trusted side (kernel, authoriser, ledger). It is
deliberately thin, because every line here is a line that could accidentally
create a path around the kernel.

The structural rule it enforces: `commit` is reachable only through a
`KernelReport` whose verdict is ALLOW, and the authoriser independently
re-checks that before issuing an authorisation.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from arbiter.audit.ledger import AuditLedger
from arbiter.authz.signer import AuthorisationRefused, ExecutionAuthoriser, Keyring
from arbiter.bank.core import BankCore
from arbiter.intent.compiler import IntentCompiler
from arbiter.intent.ratify import ratify_with, render_en, render_zh
from arbiter.kernel.adjudicator import Adjudicator, KernelConfig, KernelReport
from arbiter.llm.client import LLMClient, SchemaError
from arbiter.planner.planner import Planner
from arbiter.types import (
    IntentSpec,
    OpPlan,
    RatifiedIntent,
    Tainted,
    Verdict,
    cny,
    mask_account,
)


@dataclass
class Outcome:
    stage: str              # refused | pending | executed
    report: KernelReport | None = None
    plan: OpPlan | None = None
    spec: IntentSpec | None = None
    receipts: list[str] = field(default_factory=list)
    error: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def executed(self) -> bool:
        return self.stage == "executed"

    @property
    def verdict(self) -> str:
        return self.report.decision.verdict.value if self.report else "n/a"


class BankingAgent:
    def __init__(self, core: BankCore, model: LLMClient, keyring: Keyring,
                 ledger: AuditLedger | None = None, config: KernelConfig | None = None):
        self.core = core
        self.compiler = IntentCompiler(model, core)
        self.planner = Planner(model)
        self.kernel = Adjudicator(core, config, verifier=keyring)
        self.authoriser = ExecutionAuthoriser(keyring)
        self.keyring = keyring
        self.ledger = ledger or AuditLedger()

    # ------------------------------------------------------------------

    def propose(self, request: str, customer_id: str, now: int | None = None) -> IntentSpec:
        spec = self.compiler.compile(request, customer_id, now, self.account_context())
        self.ledger.append("intent_compiled", {
            "request": request, "digest": spec.digest(), "scenario": spec.scenario,
            "invariants": [i.to_dict() for i in spec.invariants],
            "max_tier": spec.max_tier.value,
        })
        return spec

    def render_for_customer(self, spec: IntentSpec, lang: str = "zh") -> str:
        return render_zh(spec) if lang == "zh" else render_en(spec)

    def ratify(self, spec: IntentSpec, now: int | None = None) -> RatifiedIntent:
        ratified = ratify_with(spec, self.keyring, now)
        self.ledger.append("intent_ratified", {
            "digest": spec.digest(), "signer": ratified.signer,
            "ratified_at": ratified.ratified_at,
        })
        return ratified

    def account_context(self) -> str:
        """Trusted account facts, read from the core rather than remembered."""
        st = self.core.state
        return json.dumps({
            "customer": st.customer.name,
            "risk_tolerance": st.customer.risk_tolerance.name,
            "accounts": [
                {"account_no": mask_account(a.account_no), "full": a.account_no,
                 "nickname": a.nickname, "balance": cny(a.balance)}
                for a in st.accounts.values()
            ],
            "payees": [
                {"account_no": p.account_no, "name": p.name, "times_used": p.times_used}
                for p in st.payees.values()
            ],
            "cards": [
                {"card_id": c.card_id, "status": c.status,
                 "daily_limit": cny(c.daily_limit)} for c in st.cards.values()
            ],
            "mandates": [
                {"id": m.mandate_id, "merchant": m.merchant, "cap": cny(m.cap)}
                for m in st.mandates.values()
            ],
            "products": [
                {"code": p.code, "name": p.name, "risk": p.risk_level.name}
                for p in st.products.values()
            ],
        }, ensure_ascii=False, indent=2)

    # ------------------------------------------------------------------

    def run(self, ratified: RatifiedIntent, untrusted: list[Tainted[str]] | None = None,
            now: int | None = None, execute: bool = True) -> Outcome:
        now = now if now is not None else int(time.time())
        untrusted = untrusted if untrusted is not None else self.core.untrusted_context()

        try:
            plan = self.planner.plan(ratified.spec, self.account_context(), untrusted)
        except SchemaError as exc:
            self.ledger.append("plan_rejected", {"reason": str(exc)})
            return Outcome(stage="refused", spec=ratified.spec,
                           error=f"规划器输出未通过模式校验 / planner output rejected: {exc}")

        self.ledger.append("plan_proposed", {
            "plan_digest": plan.digest(), "intent_digest": plan.intent_digest,
            "model": plan.model, "operation_count": len(plan.operations),
            "claimed_effects": plan.claimed_effects,
        })

        report = self.kernel.adjudicate(ratified, plan, now, untrusted)
        self.ledger.append("adjudicated", report.to_dict())

        outcome = Outcome(stage="pending", report=report, plan=plan, spec=ratified.spec)
        verdict = report.decision.verdict

        if verdict is Verdict.DENY:
            outcome.stage = "refused"
            outcome.notes.append("内核拒绝该计划，未产生任何授权 / kernel refused; nothing was authorised")
            return outcome

        if verdict is Verdict.REQUIRE_APPROVAL:
            outcome.notes.append(
                f"{report.decision.tier.label_zh} 权限需客户确认后方可执行 / "
                f"tier {report.decision.tier.label} requires confirmation"
            )
            return outcome

        if not execute:
            outcome.notes.append("裁决为放行，但调用方抑制了执行 / ALLOW, execution suppressed")
            return outcome

        return self._execute(ratified, outcome, via="auto")

    def approve_pending(self, ratified: RatifiedIntent, outcome: Outcome) -> Outcome:
        """
        Apply a customer confirmation to a plan the kernel escalated.

        The confirmation authorises *this* plan and no other: digests are
        re-checked, so a plan cannot be swapped between the moment the customer
        looks at it and the moment it executes.
        """
        if outcome.report is None or outcome.plan is None:
            return Outcome(stage="refused", error="没有待确认的计划 / nothing pending")
        decision = outcome.report.decision
        if decision.verdict is not Verdict.REQUIRE_APPROVAL:
            return Outcome(stage="refused",
                           error=f"该计划不处于待确认状态 / not awaiting approval "
                                 f"({decision.verdict.value})")
        if decision.plan_digest != outcome.plan.digest():
            return Outcome(stage="refused", error="计划在确认后发生变化 / plan changed after review")

        decision.verdict = Verdict.ALLOW
        self.ledger.append("customer_approved", {
            "plan_digest": decision.plan_digest, "tier": decision.tier.value,
        })
        return self._execute(ratified, outcome, via="customer_approval")

    # ------------------------------------------------------------------

    def _execute(self, ratified: RatifiedIntent, outcome: Outcome, via: str) -> Outcome:
        assert outcome.plan is not None and outcome.report is not None
        try:
            self.authoriser.authorise(outcome.plan, outcome.report.decision, ratified)
            receipts = self.core.commit(outcome.plan.operations)
        except (AuthorisationRefused, RuntimeError) as exc:
            self.ledger.append("execution_failed", {"reason": str(exc)})
            outcome.stage = "refused"
            outcome.error = str(exc)
            return outcome

        self.ledger.append("executed", {
            "plan_digest": outcome.plan.digest(), "receipts": receipts,
            "tier": outcome.report.decision.tier.value, "via": via,
        })
        outcome.stage = "executed"
        outcome.receipts = receipts
        return outcome
