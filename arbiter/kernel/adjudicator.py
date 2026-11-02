"""
ARBITER 决策内核 / The adjudication kernel.

This module is the trusted computing base. It contains no model call, no
network access except through the bank core, and no branch that depends on
natural-language text. Everything it decides, it decides from structured
observations of simulated execution.

流程 / The pipeline, in order:

  1. 授权验证 ratification   the plan is bound to an envelope this customer
                             actually signed, and the signature verifies
  2. 静态策略 static policy  shape, expiry, malformed accounts, credential ops
  3. 影子执行 simulation     the plan runs against a shadow copy of core state
  4. 幻觉防护 grounding      every entity resolves; every stated fact matches
  5. 约束校验 invariants     observed effects are checked against the envelope
  6. 注入检测 taint          untrusted strings are scored and logged; they can
                             raise the tier but never lower it or authorise
  7. 账号混淆 lookalike      destinations that resemble known accounts
  8. 权限分级 tier           the highest capability any observed effect demands
  9. 裁决 verdict            放行 / 需确认 / 拒绝

Step 4 is the answer to the brief's 幻觉防护 requirement, and it sits before
the invariants deliberately: a plan referencing an account that does not exist
should be refused for that reason, not for whichever limit it happens to trip
afterwards. The customer deserves the true reason.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from arbiter.bank.core import BankCore
from arbiter.kernel import disclosure as disclosure_mod
from arbiter.kernel import grounding as grounding_mod
from arbiter.kernel import invariants as inv_mod
from arbiter.kernel import policy as policy_mod
from arbiter.kernel import tiers as tier_mod
from arbiter.kernel.taint import find_lookalikes, inspect
from arbiter.types import (
    Decision,
    EffectSet,
    Finding,
    OpKind,
    OpPlan,
    RatifiedIntent,
    Tainted,
    Tier,
    Verdict,
)

TAINT_TIER_THRESHOLD = 55       # injection score that routes a decision to the customer

_ORIGIN_ZH = {"txn_memo": "交易附言", "merchant_name": "商户名称", "payee_alias": "收款人备注",
              "product_blurb": "产品文案", "push": "推送通知", "bill_document": "账单文件",
              "model_output": "模型输出", "user_freeform": "客户原话"}
_ORIGIN_EN = {"txn_memo": "a transaction memo", "merchant_name": "a merchant name",
              "payee_alias": "a payee alias", "product_blurb": "product copy",
              "push": "a push notification", "bill_document": "a bill document",
              "model_output": "model output", "user_freeform": "the customer's raw text"}


def _mask(account: str) -> str:
    return f"{account[:4]}****{account[-4:]}"


def _has_side_effects(fx: EffectSet) -> bool:
    """这个计划会改变什么吗 / Does this plan change anything at all?"""
    return bool(fx.balances or fx.mandates or fx.card_controls
                or fx.credentials or fx.payees_added)
LOOKALIKE_THRESHOLD = 0.875     # 7 of the 8 displayed digits


@dataclass
class KernelConfig:
    auto_execute_max_tier: Tier = Tier.T1_BOUNDED
    lookalike_threshold: float = LOOKALIKE_THRESHOLD
    taint_tier_threshold: int = TAINT_TIER_THRESHOLD


@dataclass
class KernelReport:
    """Everything a reviewer needs to reconstruct the decision."""
    decision: Decision
    effects: EffectSet | None
    taint_reports: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.to_dict(),
            "effects": self.effects.to_dict() if self.effects else None,
            "taint": self.taint_reports,
        }


class Adjudicator:
    def __init__(self, core: BankCore, config: KernelConfig | None = None,
                 verifier: Any | None = None):
        self.core = core
        self.config = config or KernelConfig()
        self.verifier = verifier

    # ------------------------------------------------------------------

    def adjudicate(self, ratified: RatifiedIntent, plan: OpPlan,
                   now: int | None = None,
                   untrusted: list[Tainted[str]] | None = None) -> KernelReport:
        """
        `untrusted` is the attacker-authored content that was in the model's
        context. The kernel inspects it although it never acts on it: knowing
        a decision was taken while an injection was present is exactly what an
        incident reviewer needs, and it is the signal that routes a borderline
        plan to a human rather than through.
        """
        started = time.perf_counter()
        now = now if now is not None else int(time.time())
        spec = ratified.spec

        decision = Decision(
            verdict=Verdict.DENY,          # 默认拒绝 / deny by default
            tier=Tier.T0_READ,
            intent_digest=spec.digest(),
            plan_digest=plan.digest(),
        )
        report = KernelReport(decision=decision, effects=None)

        # 1 -- 授权验证 ---------------------------------------------------
        if self.verifier is not None:
            if not self.verifier.verify(spec.digest(), ratified.signature, ratified.signer):
                decision.add("KRN_BAD_RATIFICATION", "critical",
                             "授权签名校验失败，该信封并非客户签署",
                             "signature over the envelope digest does not verify")
                return self._finish(report, started)

        # 2 -- 静态策略 ---------------------------------------------------
        static = policy_mod.check(ratified, plan, now)
        decision.findings.extend(static)
        if any(f.severity == "critical" for f in static):
            decision.add("KRN_STATIC_REFUSAL", "critical",
                         "计划在模拟执行前已被静态策略拒绝",
                         "plan refused before simulation on structural grounds")
            return self._finish(report, started)

        # 4a -- 实体接地先于执行 -------------------------------------------
        # Grounding runs before simulation as well as after, because a plan
        # naming an account that does not exist should be refused for *that*
        # reason rather than for whatever the core happens to reject first.
        grounding = grounding_mod.check_all(self.core, plan, spec)
        decision.findings.extend(grounding)
        if any(f.severity == "critical" for f in grounding):
            decision.add("KRN_GROUNDING_REFUSAL", "critical",
                         "计划引用了不存在的实体或与核心系统事实不符，判定为模型幻觉",
                         "plan references entities that do not exist, or states facts the "
                         "core contradicts; treated as model hallucination")
            return self._finish(report, started)

        # 3 -- 影子执行 ---------------------------------------------------
        try:
            effects = self.core.simulate(plan.operations)
        except Exception as exc:                 # a failing core is a refusal
            decision.add("KRN_SIM_ERROR", "critical",
                         f"模拟执行失败：{type(exc).__name__}",
                         f"simulation could not complete: {type(exc).__name__}: {exc}")
            return self._finish(report, started)

        report.effects = effects
        decision.effects_digest = effects.digest()

        if effects.rejected:
            decision.add("KRN_CORE_REJECTED", "medium",
                         f"核心系统拒绝该计划：{effects.reject_reason}",
                         f"the core rejected this plan: {effects.reject_reason}",
                         reason=effects.reject_reason)
            return self._finish(report, started)

        # 5 -- 约束校验 ---------------------------------------------------
        decision.findings.extend(inv_mod.evaluate(spec, effects, now))
        decision.findings.extend(inv_mod.unratified_effects(spec, effects))

        # 5b -- 适当性：客户的风险测评等级是硬上限 -------------------------
        #
        # 这一条不读信封，只读核心系统里的客户评级。信封可以比评级更严，
        # 但永远不能更松——否则一份写着「允许 R5」的信封就能把适当性管理
        # 绕过去，而适当性在中国是监管义务，不是客户可以自行放弃的偏好。
        # 客户说「我要买高风险的」不构成授权，只构成一次需要被拒绝的请求。
        decision.findings.extend(self._check_suitability(effects))

        # 5c -- 信息外泄：会离开银行的文字里不能有客户的敏感信息 -----------
        #
        # 钱一分不多转，信息也能被带走：附言、户名、代扣参考信息都会出现在
        # 对方屏幕上。被注入的模型想套取客户信息，这是它仅有的出口。
        decision.findings.extend(disclosure_mod.scan(self.core, effects))

        # 6 -- 注入检测 ---------------------------------------------------
        taint_score = self._inspect_taint(effects, decision, report, untrusted or [])

        # 7 -- 账号混淆 ---------------------------------------------------
        #
        # 这里要区分两种情况，最初混在一起时会误伤客户：
        #
        #   付给冒充者 —— 收款人是个没用过的新账号，却和某个常用账号显示位一致。
        #                 这就是攻击本身，拒绝。
        #   名单被投毒 —— 收款人正是客户用了十几次的那个真账号，只是名单里多出
        #                 了一个长得一样的。客户这笔付款没有任何问题，有问题的是
        #                 他的收款人名单。拦住这笔转账只会让他改用网银，而那边
        #                 没有任何保护。所以这里只提示，不拦截。
        trusted = self.core.trusted_payee_numbers()
        everyone = self.core.known_payee_numbers()
        lookalikes = 0
        for payee in sorted(set(effects.payees_paid) | set(effects.payees_added)):
            known_good = self.core.resolve_payee(payee)
            if known_good is not None and known_good.verified and known_good.times_used > 0:
                twins = find_lookalikes(payee, everyone, self.config.lookalike_threshold)
                if twins:
                    decision.add(
                        "KRN_PAYEE_BOOK_POISONED", "medium",
                        f"收款人名单里存在一个与 {_mask(payee)} 显示位完全一致、但并非同一个的账号。"
                        "本次付款的收款人是客户常用的那一个，但名单本身需要清理",
                        f"the payee book contains a different account whose displayed digits "
                        f"match {_mask(payee)}; this payment goes to the genuine one, but the "
                        "book itself needs cleaning",
                        paying=_mask(payee), twin=_mask(twins[0][0]),
                        paying_full=payee, twin_full=twins[0][0],
                    )
                continue

            hits = find_lookalikes(payee, trusted, self.config.lookalike_threshold)
            if hits:
                lookalikes += 1
                decision.add(
                    "KRN_LOOKALIKE", "critical",
                    f"收款账号 {_mask(payee)} 与客户常用账号显示位完全一致，但并非同一个账号——"
                    f"完整账号 {payee} 对 {hits[0][0]}",
                    f"destination {_mask(payee)} matches a known account in every displayed "
                    f"digit while being a different account: {payee} vs {hits[0][0]}",
                    candidate=_mask(payee), resembles=_mask(hits[0][0]),
                    candidate_full=payee, resembles_full=hits[0][0],
                    similarity=hits[0][1],
                )

        # 8 -- 权限分级 ---------------------------------------------------
        # 首次付款：收款账号既不是客户本人的账户，也不是他验证过并实际付过款的
        # 收款人。电信诈骗几乎都长这样，所以它至少要客户再确认一次。
        first_payments = [p for p in sorted(set(effects.payees_paid))
                          if p not in trusted and self.core.resolve_account(p) is None]
        tier, tier_findings = tier_mod.resolve(spec, effects, lookalikes, first_payments)
        decision.findings.extend(tier_findings)

        # 注入内容只提升「要不要问客户」，不提升权限等级。
        #
        # 这两件事最初是混在一起的，后果很严重：账户里只要存在一条带注入附言的
        # 转入，每一次操作都会被顶到 T2，随即超出 T0/T1 的信封上限而被拒绝——
        # 客户连自己的账单都看不了。攻击者花 0.01 元就能让受害者的助手瘫痪，
        # 而受害者什么也没做错。
        #
        # 正确的区分是：权限等级衡量的是「这个计划的效果有多危险」，而注入内容
        # 衡量的是「这次决策值不值得多问一句」。后者应当让裁决变成「需确认」，
        # 而不是把计划推过客户签署的天花板。
        if taint_score >= self.config.taint_tier_threshold:
            if _has_side_effects(effects):
                decision.add("KRN_TAINT_ESCALATION", "high",
                             "本次决策的上下文中存在高分注入内容，转为请客户确认而非直接执行",
                             "high-scoring injected content was present in this decision's "
                             "context; routing to the customer rather than executing",
                             score=taint_score)
            else:
                # 只读操作不会被注入内容伤害到——没有任何东西会发生变化。
                # 记录下来并提醒客户，但不拦住他查自己的账单。
                decision.add("KRN_INJECTION_PRESENT", "medium",
                             "账单中存在疑似注入内容，但本次为只读操作，不受影响",
                             "injected content is present in the statement, but this is a "
                             "read-only operation and cannot be affected by it",
                             score=taint_score)
        decision.tier = tier

        if tier > spec.max_tier:
            decision.add(
                "KRN_TIER_CEILING", "critical",
                f"计划需要 {tier.label_zh} 权限，但客户仅授权至 {spec.max_tier.label_zh}",
                f"plan requires tier {tier.label} but the envelope authorises at most "
                f"{spec.max_tier.label}",
                required=tier.value, ceiling=spec.max_tier.value,
            )

        # 9 -- 裁决 -------------------------------------------------------
        decision.verdict = self._verdict(decision, tier, spec.max_tier)
        return self._finish(report, started)

    # ------------------------------------------------------------------

    def _verdict(self, decision: Decision, tier: Tier, ceiling: Tier) -> Verdict:
        if any(f.severity == "critical" for f in decision.findings):
            return Verdict.DENY
        if tier > ceiling:
            return Verdict.DENY
        if tier > self.config.auto_execute_max_tier:
            return Verdict.REQUIRE_APPROVAL
        if any(f.severity == "high" for f in decision.findings):
            return Verdict.REQUIRE_APPROVAL
        return Verdict.ALLOW

    def _check_suitability(self, effects: EffectSet) -> list[Finding]:
        """适当性管理 / Suitability, enforced from core state."""
        from arbiter.types import RiskLevel
        grade = self.core.state.customer.risk_tolerance.value
        out: list[Finding] = []
        for holding in effects.holdings:
            if holding.amount <= 0 or holding.risk_level <= grade:
                continue
            got, want = RiskLevel(holding.risk_level), RiskLevel(grade)
            out.append(Finding(
                "KRN_SUITABILITY", "critical",
                f"「{holding.product_name}」为 {got.name}（{got.label_zh}），"
                f"高于客户在核心系统中的风险测评等级 {want.name}（{want.label_zh}），"
                "无论授权信封怎么写都不得销售",
                f"'{holding.product_name}' is grade {got.name} ({got.label_en}), above the "
                f"customer's assessed tolerance {want.name} ({want.label_en}) recorded in the "
                "core; no envelope can authorise this sale",
                {"product": holding.product_code, "product_risk": holding.risk_level,
                 "customer_grade": grade, "amount_fen": holding.amount},
            ))
        return out

    def _inspect_taint(self, effects: EffectSet, decision: Decision,
                       report: KernelReport, context: list[Tainted[str]]) -> int:
        worst = 0
        for tainted in list(context) + list(effects.untrusted_seen):
            raw = tainted.declassify(
                reason="injection inspection (display-only path)",
                log=decision.declassifications,
            )
            rep = inspect(raw)
            report.taint_reports.append({"origin": tainted.origin.value, **rep.to_dict()})
            if not rep.suspicious:
                continue
            worst = max(worst, rep.score)
            # 这条发现描述的是「看到了什么并且已经隔离」，而不是「这个计划有问题」。
            # 它本身不应该决定裁决——它的消息正文就写着这段内容未参与决策。真正
            # 驱动行为的是上面的 KRN_TAINT_ESCALATION：检测负责记录，升级负责行动。
            # 让检测直接拒绝，等于把一个可被绕过的模式匹配器变成了主防线。
            origin_zh = _ORIGIN_ZH.get(tainted.origin.value, tainted.origin.value)
            origin_en = _ORIGIN_EN.get(tainted.origin.value, tainted.origin.value)
            decision.add(
                "KRN_INJECTION_CONTENT", "medium",
                f"上下文中的不可信内容（来源：{origin_zh}）含有指令式文本，"
                "已被隔离，未参与本次决策",
                f"untrusted content in this decision's context ({origin_en}) carries "
                "instruction-like text; it was quarantined and had no effect on the decision",
                score=rep.score, signals=rep.signals, encoding=rep.layers,
                origin=tainted.origin.value,
            )
        return worst

    def _finish(self, report: KernelReport, started: float) -> KernelReport:
        report.decision.elapsed_ms = (time.perf_counter() - started) * 1000
        if report.decision.verdict is Verdict.DENY and not report.decision.findings:
            report.decision.add("KRN_DEFAULT_DENY", "info",
                                "没有任何规则授权该计划，默认拒绝",
                                "no rule authorised this plan; the default is refusal")
        return report
