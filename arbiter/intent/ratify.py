"""
授权确认界面 / Rendering an envelope for a customer to approve.

This rendering is produced from the structured `IntentSpec` by code in this
module. The model never writes it, never sees it, and cannot influence a
character of it.

That property is the whole point. If the text a customer reads were
model-generated, a compromised model could describe tight limits while
encoding loose ones, and the signature would be worthless. Amounts render
through the same integer conversion the kernel enforces against, so the number
on screen is the number that binds.
"""

from __future__ import annotations

import time

from arbiter.types import IntentSpec, Invariant, RatifiedIntent, Tier, mask_account

_TIER_NOTE_ZH = {
    Tier.T0_READ: "仅可查询，不会发生任何资金或设置变动",
    Tier.T1_BOUNDED: "可在以下限额内直接执行，无需再次确认",
    Tier.T2_ELEVATED: "超出日常范围的操作将暂停并请您确认",
    Tier.T3_CRITICAL: "涉及高危操作，每一步都需要您确认",
}
_TIER_NOTE_EN = {
    Tier.T0_READ: "read only; nothing will move or change",
    Tier.T1_BOUNDED: "may act within the limits below without asking again",
    Tier.T2_ELEVATED: "anything beyond routine will pause for your confirmation",
    Tier.T3_CRITICAL: "high-risk operations; every step needs your confirmation",
}


def render_zh(spec: IntentSpec) -> str:
    lines = [
        "授 权 确 认",
        "",
        f"  事项    {spec.goal_zh}",
        f"  场景    {spec.scenario or '未分类'}",
        f"  客户    {spec.customer_id}",
        f"  有效期  {_fmt(spec.created_at)} 至 {_fmt(spec.expires_at)}"
        f"（{spec.expires_at - spec.created_at} 秒）",
        f"  权限    {spec.max_tier.label_zh} — {_TIER_NOTE_ZH[spec.max_tier]}",
        "",
        "  智能助手仅可在以下范围内操作：",
    ]
    lines += [f"    · {inv.describe_zh()}" for inv in spec.invariants]
    if spec.risk_zh:
        lines += ["", "  风险提示："]
        lines += [f"    ! {note}" for note in spec.risk_zh]
    lines += [
        "",
        "  超出上述范围的任何操作都会被自动拒绝，智能助手本身也无法绕过。",
        "  您确认的是「这些限制」，而不是某一笔具体交易。",
        "",
        f"  授权摘要  {spec.digest()}",
    ]
    return "\n".join(lines)


def render_en(spec: IntentSpec) -> str:
    lines = [
        "AUTHORISATION REQUEST",
        "",
        f"  Purpose   {spec.goal_en or spec.goal_zh}",
        f"  Scenario  {_SCENARIO_EN.get(spec.scenario, spec.scenario) or 'uncategorised'}",
        f"  Customer  {spec.customer_id}",
        f"  Valid     {_fmt(spec.created_at)} to {_fmt(spec.expires_at)}"
        f" ({spec.expires_at - spec.created_at}s)",
        f"  Authority {spec.max_tier.label_en} — {_TIER_NOTE_EN[spec.max_tier]}",
        "",
        "  The assistant may act only within these limits:",
    ]
    lines += [f"    - {inv.describe_en()}" for inv in spec.invariants]
    if spec.risk_en:
        lines += ["", "  Before you approve:"]
        lines += [f"    ! {note}" for note in spec.risk_en]
    lines += [
        "",
        "  Anything outside these limits is refused automatically, including by",
        "  the assistant itself. You are approving these limits, not a transaction.",
        "",
        f"  Envelope digest  {spec.digest()}",
    ]
    return "\n".join(lines)


_SCENARIO_EN = {"智能转账": "smart transfer", "账单分析": "statement analysis",
                "订阅与代扣管理": "subscriptions & direct debits", "理财操作": "wealth management",
                "卡片管理": "card management", "跨场景联动": "cross-scenario", "账户设置": "account settings"}


def render(spec: IntentSpec, lang: str = "zh") -> str:
    return render_en(spec) if lang == "en" else render_zh(spec)


def structured(spec: IntentSpec, lang: str = "zh") -> dict:
    """
    同一份信封的结构化形式，供网页把它画成一张凭证。

    每一个字段都直接取自已签署的结构体，由这里的代码生成——和纯文本渲染是同一个来源，
    模型碰不到其中任何一个字。
    """
    en = lang == "en"
    return {
        "title": "Authorisation request" if en else "授 权 确 认",
        "goal": (spec.goal_en or spec.goal_zh) if en else spec.goal_zh,
        "scenario": (_SCENARIO_EN.get(spec.scenario, spec.scenario) if en else spec.scenario) or "",
        "customer": spec.customer_id,
        "valid_from": _fmt(spec.created_at),
        "valid_to": _fmt(spec.expires_at),
        "ttl": spec.expires_at - spec.created_at,
        "tier": spec.max_tier.label_en if en else spec.max_tier.label_zh,
        "tier_code": f"T{spec.max_tier.value}",
        "tier_note": (_TIER_NOTE_EN if en else _TIER_NOTE_ZH)[spec.max_tier],
        "limits": [inv.describe_en() if en else inv.describe_zh() for inv in spec.invariants],
        "risks": list(spec.risk_en if en else spec.risk_zh),
        "digest": spec.digest(),
        "footer": ("Anything outside these limits is refused automatically, including by the "
                   "assistant itself. You are approving these limits, not a transaction."
                   if en else
                   "超出上述范围的任何操作都会被自动拒绝，智能助手本身也无法绕过。"
                   "您确认的是「这些限制」，而不是某一笔具体交易。"),
    }


def _fmt(ts: int) -> str:
    """
    Render a timestamp in the customer's own local time.

    A bank authorisation screen showing UTC would read as wrong to the person
    approving it -- they check the validity window against the clock on their
    wall, not against UTC. Display only; every comparison in the kernel is
    done on the integer epoch.
    """
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def ratify_with(spec: IntentSpec, keyring, now: int | None = None) -> RatifiedIntent:
    """Sign an envelope. In production this is the customer's device, not a keyring."""
    return RatifiedIntent(
        spec=spec,
        signature=keyring.sign(spec.digest()),
        signer=spec.customer_id,
        ratified_at=now if now is not None else int(time.time()),
    )
