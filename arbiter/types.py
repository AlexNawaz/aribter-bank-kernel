"""
核心数据模型 / Core data model.

Three rules govern this module.

1. 金额一律用整数「分」表示 (Money is integers, in fen).
   Every amount is an integer number of 分. Floats never touch a balance, a
   limit, or a comparison -- 0.1 + 0.2 != 0.3 in binary floating point, and a
   bank that rounds in the wrong direction is a bank with a defect report.
   Conversion to a human string happens only at display edges.

2. 不可信数据在类型上就与可信数据不同 (Untrusted data is structurally distinct).
   `Tainted` is not a naming convention. Reading the value requires an explicit,
   logged declassification. Transaction memos, merchant names and push titles
   are all attacker-authored -- anyone can send you ¥0.01 with a message
   attached -- so they arrive wrapped and cannot be used by accident.

3. 每个影响决策的对象都有稳定摘要 (Everything hashes to a stable digest).
   The audit ledger chains those digests, so a reviewer can replay a decision
   and get identical bytes back.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Generic, Literal, TypeVar

T = TypeVar("T")

# ---------------------------------------------------------------------------
# 金额 / Money
# ---------------------------------------------------------------------------

FEN = 1
YUAN = 100 * FEN


def yuan(amount: str | int | float) -> int:
    """
    Convert a yuan amount to integer 分.

    Accepts a string ("1234.56"), an int, or a float. The float path exists
    because callers will pass one eventually; it rounds half-up at the 分 and
    is the only place in the system where a float is tolerated at all.
    """
    if isinstance(amount, float):
        return int(round(amount * 100))
    text = str(amount).strip()
    negative = text.startswith("-")
    if negative:
        text = text[1:]
    if "." in text:
        whole, frac = text.split(".", 1)
    else:
        whole, frac = text, ""
    frac = (frac + "00")[:2]
    total = int(whole or "0") * 100 + int(frac or "0")
    return -total if negative else total


def fen_to_yuan(amount: int) -> str:
    """Render 分 as a yuan string. Display only -- never fed back into logic."""
    sign = "-" if amount < 0 else ""
    amount = abs(amount)
    return f"{sign}{amount // 100}.{amount % 100:02d}"


def cny(amount: int) -> str:
    return f"¥{fen_to_yuan(amount)}"


# ---------------------------------------------------------------------------
# 标识符 / Identifiers
# ---------------------------------------------------------------------------

_ACCOUNT_RE = re.compile(r"^\d{12,19}$")

AccountNo = str


def is_account_no(value: str) -> bool:
    return bool(_ACCOUNT_RE.match(str(value)))


def mask_account(value: str) -> str:
    """
    Mask an account number the way a bank UI does.

    This masking is exactly what makes account-number confusion attacks work,
    which is why `kernel.grounding` compares the *displayed window* rather than
    the whole string when looking for lookalikes.
    """
    text = str(value)
    if len(text) <= 8:
        return text
    return f"{text[:4]}****{text[-4:]}"


def stable_digest(obj: Any) -> str:
    """Deterministic SHA-256 over a canonical JSON projection."""
    payload = json.dumps(obj, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, default=_json_default)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _json_default(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Tainted):
        # Attacker-authored content never contributes its *content* to a digest
        # that binds a decision -- only the fact that it was present.
        return {"__tainted__": True, "origin": value.origin.value}
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    raise TypeError(f"not serializable: {type(value).__name__}")


# ---------------------------------------------------------------------------
# 污点追踪 / Taint tracking
# ---------------------------------------------------------------------------

class TaintOrigin(str, Enum):
    """不可信数据的来源 / Where untrusted data entered the system."""
    TXN_MEMO = "txn_memo"               # 交易附言 -- written by whoever paid you
    MERCHANT_NAME = "merchant_name"     # 商户名称 -- merchant-controlled
    PAYEE_ALIAS = "payee_alias"         # 收款人备注名
    PUSH_NOTIFICATION = "push"          # 推送通知 / SMS content
    BILL_DOCUMENT = "bill_document"     # 账单 PDF / 对账单
    PRODUCT_BLURB = "product_blurb"     # 理财产品宣传文案
    MODEL_OUTPUT = "model_output"       # anything the LLM produced
    USER_FREEFORM = "user_freeform"     # raw user text, before ratification


class DeclassificationError(RuntimeError):
    """Raised when tainted data is used where only trusted data is permitted."""


@dataclass(frozen=True)
class Tainted(Generic[T]):
    """
    外部来源数据的包装器 / A value that originated outside the trust boundary.

    The wrapper makes passing untrusted data into a trusted sink a type error
    rather than a judgement call. `__str__` raises, so an accidental f-string
    into a prompt or a policy comparison crashes instead of quietly working.
    """
    value: T
    origin: TaintOrigin
    note: str = ""

    def declassify(self, reason: str, log: list[dict[str, Any]] | None = None) -> T:
        if log is not None:
            log.append({
                "origin": self.origin.value,
                "reason": reason,
                "preview": _preview(self.value),
                "ts": time.time(),
            })
        return self.value

    def render_quarantined(self) -> str:
        from arbiter.kernel.taint import quarantine
        return quarantine(str(self.value), self.origin)

    def __str__(self) -> str:  # pragma: no cover - guard rail
        raise DeclassificationError(
            f"implicit str() on tainted {self.origin.value}; "
            "call declassify(reason=...) or render_quarantined()"
        )


def _preview(value: Any, limit: int = 140) -> str:
    text = repr(value)
    return text if len(text) <= limit else text[:limit] + "…"


# ---------------------------------------------------------------------------
# 风险等级 / Product risk grading
# ---------------------------------------------------------------------------

class RiskLevel(int, Enum):
    """
    理财产品风险等级 R1-R5 / Product risk grade.

    Chinese retail wealth products are graded R1 (谨慎型) to R5 (激进型), and
    selling above a customer's assessed tolerance is a regulated mis-selling
    offence, not merely a bad outcome. The kernel treats the customer's
    assessed grade as a ceiling rather than a suggestion.
    """
    R1 = 1   # 谨慎型 cautious
    R2 = 2   # 稳健型 steady
    R3 = 3   # 平衡型 balanced
    R4 = 4   # 进取型 growth
    R5 = 5   # 激进型 aggressive

    @property
    def label_zh(self) -> str:
        return {1: "谨慎型", 2: "稳健型", 3: "平衡型", 4: "进取型", 5: "激进型"}[self.value]

    @property
    def label_en(self) -> str:
        return {1: "cautious", 2: "steady", 3: "balanced", 4: "growth", 5: "aggressive"}[self.value]


# ---------------------------------------------------------------------------
# 权限分级 / Capability tiers
# ---------------------------------------------------------------------------

class Tier(int, Enum):
    """
    分级权限机制 / Capability tiers.

    A plan executes at the highest tier any of its observed effects requires;
    the ratified intent sets the ceiling.
    """
    T0_READ = 0        # 只读查询, no state change
    T1_BOUNDED = 1     # 额度内资金操作, within the ratified envelope
    T2_ELEVATED = 2    # 新增收款人、授权、卡片额度调整
    T3_CRITICAL = 3    # 凭证变更、风控削弱、无上限授权、超风险等级

    @property
    def label(self) -> str:
        return {0: "READ", 1: "BOUNDED", 2: "ELEVATED", 3: "CRITICAL"}[self.value]

    @property
    def label_zh(self) -> str:
        return {0: "只读", 1: "限额内", 2: "需确认", 3: "高危"}[self.value]

    @property
    def label_en(self) -> str:
        """给客户看的英文名，与网页侧栏一致 / customer-facing name, as in the web panel."""
        return {0: "read", 1: "bounded", 2: "approval", 3: "critical"}[self.value]


class Verdict(str, Enum):
    ALLOW = "allow"                        # 放行
    REQUIRE_APPROVAL = "require_approval"  # 需用户二次确认
    DENY = "deny"                          # 拒绝执行

    @property
    def label_zh(self) -> str:
        return {"allow": "放行", "require_approval": "需确认", "deny": "拒绝"}[self.value]


# ---------------------------------------------------------------------------
# 约束条件 / Invariants
# ---------------------------------------------------------------------------

def _count(n: int, one: str, many: str) -> str:
    """英文单复数 / English singular or plural: 1 named payee, 2 named payees."""
    return f"{n} {one if n == 1 else many}"


class InvariantKind(str, Enum):
    MAX_TOTAL_OUT = "max_total_out"              # 累计出账上限
    MAX_SINGLE_OUT = "max_single_out"            # 单笔出账上限
    PAYEE_ALLOWLIST = "payee_allowlist"          # 仅限指定收款人
    NO_NEW_PAYEE = "no_new_payee"                # 不得新增收款人
    NO_NEW_MANDATE = "no_new_mandate"            # 不得新建代扣授权
    MANDATE_CAP = "mandate_cap"                  # 代扣授权金额上限
    MAX_RISK_LEVEL = "max_risk_level"            # 理财产品风险等级上限
    NO_CARD_CONTROL_WEAKENING = "no_card_control_weakening"   # 不得削弱卡片风控
    CARD_CONTROL_SCOPE = "card_control_scope"                 # 仅可调整指定卡片的风控
    NO_CREDENTIAL_CHANGE = "no_credential_change"             # 不得变更账户凭证
    ACCOUNT_SCOPE = "account_scope"              # 仅限指定账户
    EXPIRY = "expiry"                            # 授权有效期


@dataclass(frozen=True)
class Invariant:
    """
    A machine-checkable assertion over the *simulated effects* of a plan.

    Never evaluated against the model's description of what it intends to do.
    """
    kind: InvariantKind
    limit: int | None = None            # 分, risk grade, or unix seconds
    accounts: tuple[str, ...] = ()
    label: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "limit": self.limit,
            "accounts": list(self.accounts),
            "label": self.label,
        }

    def describe_zh(self) -> str:
        k, lim = self.kind, self.limit
        if k is InvariantKind.MAX_TOTAL_OUT:
            return f"累计支出不超过 {cny(lim or 0)}"
        if k is InvariantKind.MAX_SINGLE_OUT:
            return f"单笔支出不超过 {cny(lim or 0)}"
        if k is InvariantKind.PAYEE_ALLOWLIST:
            return f"仅可向 {len(self.accounts)} 个指定收款人付款"
        if k is InvariantKind.NO_NEW_PAYEE:
            return "不得新增收款人"
        if k is InvariantKind.NO_NEW_MANDATE:
            return "不得新建任何代扣或订阅授权"
        if k is InvariantKind.MANDATE_CAP:
            return f"代扣授权单期不超过 {cny(lim or 0)}，且不得无上限"
        if k is InvariantKind.MAX_RISK_LEVEL:
            grade = RiskLevel(lim or 1)
            return f"仅可购买 {grade.name}（{grade.label_zh}）及以下风险等级产品"
        if k is InvariantKind.CARD_CONTROL_SCOPE:
            return f"仅可调整 {len(self.accounts)} 张指定卡片的风控设置"
        if k is InvariantKind.NO_CARD_CONTROL_WEAKENING:
            return "不得提高卡片限额、解冻卡片或开启线上支付"
        if k is InvariantKind.NO_CREDENTIAL_CHANGE:
            return "不得变更手机号、邮箱或密码"
        if k is InvariantKind.ACCOUNT_SCOPE:
            return f"仅可操作 {len(self.accounts)} 个指定账户"
        if k is InvariantKind.EXPIRY:
            return f"授权在 {time.strftime('%H:%M', time.gmtime(lim or 0))} 前有效"
        return k.value

    def describe_en(self) -> str:
        k, lim = self.kind, self.limit
        if k is InvariantKind.MAX_TOTAL_OUT:
            return f"pay out at most {cny(lim or 0)} in total"
        if k is InvariantKind.MAX_SINGLE_OUT:
            return f"no single payment above {cny(lim or 0)}"
        if k is InvariantKind.PAYEE_ALLOWLIST:
            return f"pay only {_count(len(self.accounts), 'named payee', 'named payees')}"
        if k is InvariantKind.NO_NEW_PAYEE:
            return "add no new payees"
        if k is InvariantKind.NO_NEW_MANDATE:
            return "create no direct-debit or subscription mandates"
        if k is InvariantKind.MANDATE_CAP:
            return f"any mandate capped at {cny(lim or 0)}, never uncapped"
        if k is InvariantKind.MAX_RISK_LEVEL:
            grade = RiskLevel(lim or 1)
            return f"buy nothing above risk grade {grade.name} ({grade.label_en})"
        if k is InvariantKind.CARD_CONTROL_SCOPE:
            return f"may change card controls on {_count(len(self.accounts), 'named card', 'named cards')} only"
        if k is InvariantKind.NO_CARD_CONTROL_WEAKENING:
            return "never raise a card limit, unfreeze a card, or enable online payment"
        if k is InvariantKind.NO_CREDENTIAL_CHANGE:
            return "change no phone number, email, or password"
        if k is InvariantKind.ACCOUNT_SCOPE:
            return f"operate only on {_count(len(self.accounts), 'named account', 'named accounts')}"
        if k is InvariantKind.EXPIRY:
            return f"act only before unix time {lim}"
        return k.value


# ---------------------------------------------------------------------------
# 意图 / Intent: what the customer ratified
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class IntentSpec:
    """
    客户签署的授权信封 / The envelope the customer signs.

    The trust anchor. A customer cannot meaningfully review an API payload, so
    they do not: they approve a bounded statement of purpose, rendered in plain
    language, and every later step is checked against it.
    """
    goal_zh: str
    goal_en: str
    invariants: tuple[Invariant, ...]
    max_tier: Tier
    customer_id: str
    created_at: int
    expires_at: int
    nonce: str
    scenario: str = ""      # which of the six task areas this envelope serves
    # 风险提示 / risk disclosures. Part of what the customer signs, so part of the digest:
    # change one word of a disclosure and the signature no longer matches.
    risk_zh: tuple[str, ...] = ()
    risk_en: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        out = {
            "goal_zh": self.goal_zh,
            "goal_en": self.goal_en,
            "invariants": [i.to_dict() for i in self.invariants],
            "max_tier": self.max_tier.value,
            "customer_id": self.customer_id,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "nonce": self.nonce,
            "scenario": self.scenario,
        }
        if self.risk_zh or self.risk_en:
            out["risk_zh"] = list(self.risk_zh)
            out["risk_en"] = list(self.risk_en)
        return out

    def digest(self) -> str:
        return stable_digest(self.to_dict())


@dataclass(frozen=True)
class RatifiedIntent:
    """An IntentSpec plus the customer's signature over its digest."""
    spec: IntentSpec
    signature: str
    signer: str
    ratified_at: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec": self.spec.to_dict(),
            "spec_digest": self.spec.digest(),
            "signature": self.signature,
            "signer": self.signer,
            "ratified_at": self.ratified_at,
        }


# ---------------------------------------------------------------------------
# 计划 / Plans: what the (untrusted) model proposes
# ---------------------------------------------------------------------------

class OpKind(str, Enum):
    """
    六大任务场景对应的操作类型 / Operations across the six task scenarios.
    """
    # 智能转账 smart transfer
    TRANSFER = "transfer"
    SCHEDULE_TRANSFER = "schedule_transfer"
    ADD_PAYEE = "add_payee"
    # 账单分析 bill analysis (read-only)
    QUERY_TRANSACTIONS = "query_transactions"
    QUERY_BALANCE = "query_balance"
    CATEGORISE_SPENDING = "categorise_spending"
    # 理财操作 wealth management
    PURCHASE_PRODUCT = "purchase_product"
    REDEEM_PRODUCT = "redeem_product"
    # 卡片管理 card management
    FREEZE_CARD = "freeze_card"
    UNFREEZE_CARD = "unfreeze_card"
    SET_CARD_LIMIT = "set_card_limit"
    SET_ONLINE_PAYMENT = "set_online_payment"
    # 订阅与代扣管理 subscription / direct debit
    CREATE_MANDATE = "create_mandate"
    MODIFY_MANDATE = "modify_mandate"
    CANCEL_MANDATE = "cancel_mandate"
    # 账户设置 account settings
    CHANGE_PHONE = "change_phone"
    CHANGE_EMAIL = "change_email"

    @property
    def scenario(self) -> str:
        return _OP_SCENARIO[self]

    @property
    def read_only(self) -> bool:
        return self in (OpKind.QUERY_TRANSACTIONS, OpKind.QUERY_BALANCE,
                        OpKind.CATEGORISE_SPENDING)


_OP_SCENARIO = {
    OpKind.TRANSFER: "智能转账",
    OpKind.SCHEDULE_TRANSFER: "智能转账",
    OpKind.ADD_PAYEE: "智能转账",
    OpKind.QUERY_TRANSACTIONS: "账单分析",
    OpKind.QUERY_BALANCE: "账单分析",
    OpKind.CATEGORISE_SPENDING: "账单分析",
    OpKind.PURCHASE_PRODUCT: "理财操作",
    OpKind.REDEEM_PRODUCT: "理财操作",
    OpKind.FREEZE_CARD: "卡片管理",
    OpKind.UNFREEZE_CARD: "卡片管理",
    OpKind.SET_CARD_LIMIT: "卡片管理",
    OpKind.SET_ONLINE_PAYMENT: "卡片管理",
    OpKind.CREATE_MANDATE: "订阅与代扣管理",
    OpKind.MODIFY_MANDATE: "订阅与代扣管理",
    OpKind.CANCEL_MANDATE: "订阅与代扣管理",
    OpKind.CHANGE_PHONE: "账户设置",
    OpKind.CHANGE_EMAIL: "账户设置",
}


@dataclass(frozen=True)
class Operation:
    """
    One banking operation proposed by the planner.

    Carries no authority. `rationale` is the model's explanation and is for
    display only -- the kernel never reads it.
    """
    kind: OpKind
    account: str = ""
    target: str = ""              # payee account, product code, card id, mandate id
    amount: int = 0               # 分
    params: dict[str, Any] = field(default_factory=dict)
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "account": self.account,
            "target": self.target,
            "amount": self.amount,
            "params": {k: v for k, v in sorted(self.params.items())},
        }


@dataclass(frozen=True)
class OpPlan:
    """
    A sequence of operations proposed by the model.

    A request for the kernel to evaluate, never an instruction. Nothing in it
    -- including `claimed_effects` and every `rationale` -- is treated as true.
    """
    operations: tuple[Operation, ...]
    intent_digest: str
    claimed_effects: str = ""
    claimed_facts: dict[str, Any] = field(default_factory=dict)
    model: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "operations": [o.to_dict() for o in self.operations],
            "intent_digest": self.intent_digest,
        }

    def digest(self) -> str:
        return stable_digest(self.to_dict())


# ---------------------------------------------------------------------------
# 观测效果 / Observed effects
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BalanceDelta:
    account: str
    delta: int                 # signed 分


@dataclass(frozen=True)
class MandateDelta:
    mandate_id: str
    merchant: str
    cap_before: int            # -1 denotes "did not exist"
    cap_after: int             # -1 denotes cancelled
    period: str = "monthly"

    @property
    def created(self) -> bool:
        return self.cap_before < 0 and self.cap_after >= 0

    @property
    def cancelled(self) -> bool:
        return self.cap_after < 0 and self.cap_before >= 0

    @property
    def uncapped(self) -> bool:
        return self.cap_after >= UNCAPPED

    @property
    def widened(self) -> bool:
        return self.cap_before >= 0 <= self.cap_after and self.cap_after > self.cap_before


@dataclass(frozen=True)
class CardControlDelta:
    card_id: str
    control: str               # status | daily_limit | single_limit | online_payment
    before: Any
    after: Any
    weakening: bool            # True when the change reduces protection


@dataclass(frozen=True)
class HoldingDelta:
    product_code: str
    product_name: str
    risk_level: int
    amount: int                # signed 分: positive is a purchase


@dataclass(frozen=True)
class CredentialDelta:
    field_name: str            # phone | email | password
    before_masked: str
    after_masked: str


# Sentinel for an uncapped mandate. Real systems express this as a null cap;
# a large integer keeps every comparison in one numeric domain.
UNCAPPED = 1 << 62


@dataclass
class EffectSet:
    """
    模拟执行后观测到的状态变化 / The ground truth a decision is made on.

    Produced by executing the plan against a shadow copy of core banking state.
    Every invariant is evaluated here and nowhere else.
    """
    balances: list[BalanceDelta] = field(default_factory=list)
    mandates: list[MandateDelta] = field(default_factory=list)
    card_controls: list[CardControlDelta] = field(default_factory=list)
    holdings: list[HoldingDelta] = field(default_factory=list)
    credentials: list[CredentialDelta] = field(default_factory=list)
    payees_paid: list[str] = field(default_factory=list)
    payees_added: list[str] = field(default_factory=list)
    accounts_touched: list[str] = field(default_factory=list)
    reads: list[str] = field(default_factory=list)
    rejected: bool = False
    reject_reason: str = ""
    untrusted_seen: list[Tainted[str]] = field(default_factory=list)
    # 会随操作离开银行的文字：转账附言、收款人户名、代扣参考信息。
    # (channel, text) —— 只供外泄检查读取；序列化时只记录摘要，不记录原文。
    outbound_text: list[tuple[str, str]] = field(default_factory=list)

    def net(self, account: str) -> int:
        return sum(b.delta for b in self.balances if b.account == account)

    def total_outflow(self) -> int:
        """
        Net money that left the customer's cash accounts.

        Netting is what makes this figure mean anything. The core records a
        debit on the source and a matching credit on the destination whenever
        the destination is also the customer's, so a transfer between their own
        accounts nets to zero here and does not consume their spending cap. A
        gross sum of debits would charge them for moving their own money from
        savings to current.
        """
        net = sum(b.delta for b in self.balances)
        return -net if net < 0 else 0

    def to_dict(self) -> dict[str, Any]:
        out = {
            "balances": [asdict(b) for b in self.balances],
            "mandates": [asdict(m) for m in self.mandates],
            "card_controls": [asdict(c) for c in self.card_controls],
            "holdings": [asdict(h) for h in self.holdings],
            "credentials": [asdict(c) for c in self.credentials],
            "payees_paid": sorted(set(self.payees_paid)),
            "payees_added": sorted(set(self.payees_added)),
            "accounts_touched": sorted(set(self.accounts_touched)),
            "reads": sorted(set(self.reads)),
            "rejected": self.rejected,
        }
        if self.outbound_text:
            # 审计记录里只放摘要：拦下来的外泄内容不能因为被记录而泄露第二次
            out["outbound"] = [{"channel": c, "chars": len(t), "sha256": stable_digest(t)[:16]}
                               for c, t in self.outbound_text]
        return out

    def digest(self) -> str:
        return stable_digest(self.to_dict())


# ---------------------------------------------------------------------------
# 决策 / Decisions
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Finding:
    code: str
    severity: Literal["info", "low", "medium", "high", "critical"]
    message_zh: str
    message_en: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message_zh": self.message_zh,
            "message_en": self.message_en,
            "evidence": self.evidence,
        }


@dataclass
class Decision:
    verdict: Verdict
    tier: Tier
    findings: list[Finding] = field(default_factory=list)
    intent_digest: str = ""
    plan_digest: str = ""
    effects_digest: str = ""
    declassifications: list[dict[str, Any]] = field(default_factory=list)
    elapsed_ms: float = 0.0

    @property
    def blocked(self) -> bool:
        return self.verdict is not Verdict.ALLOW

    def add(self, code: str, severity: str, zh: str, en: str, **evidence: Any) -> "Decision":
        self.findings.append(Finding(code, severity, zh, en, evidence))  # type: ignore[arg-type]
        return self

    def blocking(self) -> list[Finding]:
        return [f for f in self.findings if f.severity in ("high", "critical")]

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "verdict_zh": self.verdict.label_zh,
            "tier": self.tier.value,
            "tier_label": self.tier.label,
            "tier_label_zh": self.tier.label_zh,
            "findings": [f.to_dict() for f in self.findings],
            "intent_digest": self.intent_digest,
            "plan_digest": self.plan_digest,
            "effects_digest": self.effects_digest,
            "declassification_count": len(self.declassifications),
            "elapsed_ms": round(self.elapsed_ms, 3),
        }
