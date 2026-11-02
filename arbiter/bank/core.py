"""
核心银行系统模拟器 / Core banking simulator.

A deterministic, offline model of the bank state an agent can touch: accounts,
cards, payees, direct-debit mandates, wealth holdings and the transaction
ledger. It runs the benchmark and the test suite with no network and no
credentials, so every result in this submission is reproducible by a reviewer
on a laptop.

Two design points matter for the security argument.

**Entity resolution is explicit.** `resolve_*` returns None for anything that
does not exist, and the kernel treats an unresolvable reference as a refusal
rather than an error to recover from. This is what turns a hallucinated account
number or fund code into a blocked plan instead of a failed API call -- the
银行 AI 智能体 topic lists 幻觉防护 as a core requirement, and grounding every
reference against real core state is how that requirement is actually met.

**Transaction memos are attacker-authored.** Anyone can send the customer
¥0.01 with a 附言 attached, and that text ends up in the agent's context the
moment it analyses a bill. The core returns memos wrapped in `Tainted`, so
reaching their contents requires an explicit declassification.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field, replace
from typing import Any, Iterable

from arbiter.types import (
    UNCAPPED,
    BalanceDelta,
    CardControlDelta,
    CredentialDelta,
    EffectSet,
    HoldingDelta,
    MandateDelta,
    Operation,
    OpKind,
    RiskLevel,
    Tainted,
    TaintOrigin,
    cny,
    mask_account,
    yuan,
)


# ---------------------------------------------------------------------------
# 实体 / Entities
# ---------------------------------------------------------------------------

@dataclass
class Account:
    account_no: str
    nickname: str
    kind: str               # current 活期 | savings 定期 | credit 信用卡
    balance: int            # 分
    currency: str = "CNY"
    status: str = "active"


@dataclass
class Payee:
    account_no: str
    name: str
    bank: str
    verified: bool = True        # 已验证收款人, from the customer's address book
    times_used: int = 0
    alias: str = ""              # 备注名 -- customer- or attacker-influenced


@dataclass
class Card:
    card_id: str
    account_no: str
    kind: str                    # debit 借记卡 | credit 信用卡
    status: str = "active"       # active | frozen
    daily_limit: int = 0         # 分
    single_limit: int = 0        # 分
    online_payment: bool = True


@dataclass
class Mandate:
    """代扣授权 / 订阅 -- a standing authorisation to pull money."""
    mandate_id: str
    merchant: str
    cap: int                     # 分 per period; UNCAPPED means no ceiling
    period: str = "monthly"
    status: str = "active"
    next_charge: str = ""


@dataclass
class WealthProduct:
    code: str
    name: str
    risk_level: RiskLevel
    min_amount: int
    kind: str = "fund"
    blurb: str = ""              # 宣传文案 -- marketing copy, untrusted


@dataclass
class Holding:
    product_code: str
    amount: int                  # 分


@dataclass
class Transaction:
    txn_id: str
    account_no: str
    date: str
    amount: int                  # signed 分
    counterparty: str
    merchant: str = ""
    category: str = ""
    memo: str = ""               # 附言 -- WRITTEN BY WHOEVER SENT THE MONEY


@dataclass
class Customer:
    customer_id: str
    name: str
    risk_tolerance: RiskLevel = RiskLevel.R2
    phone: str = ""
    email: str = ""
    id_number: str = ""          # 身份证号——只用于外泄检查，从不显示


# ---------------------------------------------------------------------------
# 核心状态 / Core state
# ---------------------------------------------------------------------------

@dataclass
class BankState:
    customer: Customer
    accounts: dict[str, Account] = field(default_factory=dict)
    payees: dict[str, Payee] = field(default_factory=dict)
    cards: dict[str, Card] = field(default_factory=dict)
    mandates: dict[str, Mandate] = field(default_factory=dict)
    products: dict[str, WealthProduct] = field(default_factory=dict)
    holdings: dict[str, Holding] = field(default_factory=dict)
    transactions: list[Transaction] = field(default_factory=list)

    def clone(self) -> "BankState":
        return copy.deepcopy(self)


class BankCore:
    """
    The bank. Executes operations, reports effects, and resolves entities.

    `simulate` runs a plan against a shadow copy and returns what it did.
    `commit` applies it for real. The kernel only ever calls `simulate`; only
    an authorised plan reaches `commit`.
    """

    def __init__(self, state: BankState):
        self.state = state
        self.committed: list[dict[str, Any]] = []

    # -- 实体解析 / entity resolution ------------------------------------

    def resolve_account(self, account_no: str) -> Account | None:
        return self.state.accounts.get(str(account_no))

    def resolve_payee(self, account_no: str) -> Payee | None:
        return self.state.payees.get(str(account_no))

    def resolve_card(self, card_id: str) -> Card | None:
        return self.state.cards.get(str(card_id))

    def resolve_mandate(self, mandate_id: str) -> Mandate | None:
        return self.state.mandates.get(str(mandate_id))

    def resolve_product(self, code: str) -> WealthProduct | None:
        return self.state.products.get(str(code))

    def known_payee_numbers(self) -> list[str]:
        return sorted(self.state.payees)

    def trusted_payee_numbers(self) -> list[str]:
        """
        客户确实用过的收款人 / Payees the customer has actually paid before.

        和「出现在收款人名单里」不是一回事：账号混淆攻击的做法正是把攻击者的
        账号塞进那份名单。用过很多次且已验证的收款人，才是值得拿来做比对基准的
        那一类。
        """
        return sorted(p.account_no for p in self.state.payees.values()
                      if p.verified and p.times_used > 0)

    def balance_of(self, account_no: str) -> int | None:
        account = self.resolve_account(account_no)
        return None if account is None else account.balance

    def untrusted_context(self, limit: int = 12) -> list[Tainted[str]]:
        """
        Attacker-authored strings the agent would see while doing its job.

        Transaction memos and merchant names reach the agent's context the
        moment it analyses a bill. They are returned wrapped, because that is
        what they are: text written by third parties.
        """
        out: list[Tainted[str]] = []
        for txn in self.state.transactions[-limit:]:
            if txn.memo:
                out.append(Tainted(txn.memo, TaintOrigin.TXN_MEMO,
                                   note=f"txn {txn.txn_id}"))
            if txn.merchant:
                out.append(Tainted(txn.merchant, TaintOrigin.MERCHANT_NAME,
                                   note=f"txn {txn.txn_id}"))
        for payee in self.state.payees.values():
            if payee.alias:
                out.append(Tainted(payee.alias, TaintOrigin.PAYEE_ALIAS,
                                   note=f"payee {mask_account(payee.account_no)}"))
        return out

    # -- 执行 / execution -------------------------------------------------

    def simulate(self, operations: Iterable[Operation]) -> EffectSet:
        """Run a plan against a shadow copy. Never mutates committed state."""
        shadow = self.state.clone()
        effects = EffectSet()
        for index, op in enumerate(operations):
            self._apply(shadow, op, effects, index)
            if effects.rejected:
                break
        return effects

    def commit(self, operations: Iterable[Operation]) -> list[str]:
        """Apply a plan for real. Returns per-operation receipt ids."""
        effects = EffectSet()
        receipts: list[str] = []
        for index, op in enumerate(operations):
            self._apply(self.state, op, effects, index)
            if effects.rejected:
                raise RuntimeError(f"core rejected operation {index}: {effects.reject_reason}")
            receipts.append(f"RCPT{len(self.committed):04d}{index:02d}")
        self.committed.append({"ops": len(receipts), "receipts": receipts})
        return receipts

    # -- 单操作执行 / one operation --------------------------------------

    def _apply(self, st: BankState, op: Operation, fx: EffectSet, index: int) -> None:
        kind = op.kind

        if op.account:
            fx.accounts_touched.append(op.account)

        # --- 账单分析 (read-only) ---------------------------------------
        if kind.read_only:
            fx.reads.append(f"{kind.value}:{op.account or op.target}")
            for txn in st.transactions:
                if txn.memo:
                    fx.untrusted_seen.append(Tainted(txn.memo, TaintOrigin.TXN_MEMO))
                if txn.merchant:
                    fx.untrusted_seen.append(Tainted(txn.merchant, TaintOrigin.MERCHANT_NAME))
            return

        # --- 智能转账 ---------------------------------------------------
        if kind in (OpKind.TRANSFER, OpKind.SCHEDULE_TRANSFER):
            src = st.accounts.get(op.account)
            if src is None:
                return self._reject(fx, f"账户不存在 / unknown account {op.account}")
            if op.amount <= 0:
                return self._reject(fx, "金额必须为正 / amount must be positive")
            if src.balance < op.amount:
                return self._reject(fx, f"余额不足 / insufficient balance in {mask_account(op.account)}")
            src.balance -= op.amount
            fx.balances.append(BalanceDelta(op.account, -op.amount))
            fx.payees_paid.append(op.target)
            # An internal transfer between the customer's own accounts is not
            # an outflow; crediting it keeps the net arithmetic honest.
            dest = st.accounts.get(op.target)
            if dest is not None:
                dest.balance += op.amount
                fx.balances.append(BalanceDelta(op.target, op.amount))
            payee = st.payees.get(op.target)
            if payee is not None:
                payee.times_used += 1
            memo = str(op.params.get("memo", "") or "")
            if memo:
                fx.outbound_text.append(("transfer_memo", memo))
            return

        if kind is OpKind.ADD_PAYEE:
            if op.target in st.payees:
                return
            st.payees[op.target] = Payee(
                account_no=op.target,
                name=str(op.params.get("name", "")),
                bank=str(op.params.get("bank", "")),
                verified=False,
            )
            fx.payees_added.append(op.target)
            # 跨行转账会把户名送去收款行核验——户名也是会离开银行的文字
            label = " ".join(x for x in (str(op.params.get("name", "")),
                                         str(op.params.get("bank", ""))) if x)
            if label:
                fx.outbound_text.append(("payee_name", label))
            return

        # --- 理财操作 ---------------------------------------------------
        if kind is OpKind.PURCHASE_PRODUCT:
            product = st.products.get(op.target)
            if product is None:
                return self._reject(fx, f"产品不存在 / unknown product {op.target}")
            src = st.accounts.get(op.account)
            if src is None:
                return self._reject(fx, f"账户不存在 / unknown account {op.account}")
            if op.amount < product.min_amount:
                return self._reject(fx, f"低于起购金额 / below minimum for {product.code}")
            if src.balance < op.amount:
                return self._reject(fx, "余额不足 / insufficient balance")
            src.balance -= op.amount
            fx.balances.append(BalanceDelta(op.account, -op.amount))
            holding = st.holdings.setdefault(product.code, Holding(product.code, 0))
            holding.amount += op.amount
            fx.holdings.append(HoldingDelta(product.code, product.name,
                                            product.risk_level.value, op.amount))
            return

        if kind is OpKind.REDEEM_PRODUCT:
            product = st.products.get(op.target)
            holding = st.holdings.get(op.target)
            if product is None or holding is None:
                return self._reject(fx, f"未持有该产品 / no holding in {op.target}")
            amount = min(op.amount, holding.amount) if op.amount else holding.amount
            holding.amount -= amount
            dest = st.accounts.get(op.account)
            if dest is not None:
                dest.balance += amount
                fx.balances.append(BalanceDelta(op.account, amount))
            fx.holdings.append(HoldingDelta(product.code, product.name,
                                            product.risk_level.value, -amount))
            return

        # --- 卡片管理 ---------------------------------------------------
        if kind in (OpKind.FREEZE_CARD, OpKind.UNFREEZE_CARD):
            card = st.cards.get(op.target)
            if card is None:
                return self._reject(fx, f"卡片不存在 / unknown card {op.target}")
            before = card.status
            card.status = "frozen" if kind is OpKind.FREEZE_CARD else "active"
            if before != card.status:
                fx.card_controls.append(CardControlDelta(
                    card.card_id, "status", before, card.status,
                    weakening=(kind is OpKind.UNFREEZE_CARD),
                ))
            return

        if kind is OpKind.SET_CARD_LIMIT:
            card = st.cards.get(op.target)
            if card is None:
                return self._reject(fx, f"卡片不存在 / unknown card {op.target}")
            which = str(op.params.get("which", "daily"))
            attr = "daily_limit" if which == "daily" else "single_limit"
            before = getattr(card, attr)
            setattr(card, attr, op.amount)
            if before != op.amount:
                fx.card_controls.append(CardControlDelta(
                    card.card_id, attr, before, op.amount,
                    weakening=op.amount > before,     # a higher limit is weaker control
                ))
            return

        if kind is OpKind.SET_ONLINE_PAYMENT:
            card = st.cards.get(op.target)
            if card is None:
                return self._reject(fx, f"卡片不存在 / unknown card {op.target}")
            enabled = bool(op.params.get("enabled", True))
            before = card.online_payment
            card.online_payment = enabled
            if before != enabled:
                fx.card_controls.append(CardControlDelta(
                    card.card_id, "online_payment", before, enabled,
                    weakening=enabled and not before,
                ))
            return

        # --- 订阅与代扣管理 ---------------------------------------------
        if kind is OpKind.CREATE_MANDATE:
            cap = op.amount if op.amount > 0 else UNCAPPED
            mandate_id = op.target or f"MD{len(st.mandates) + 1:03d}"
            st.mandates[mandate_id] = Mandate(
                mandate_id=mandate_id,
                merchant=str(op.params.get("merchant", "")),
                cap=cap,
                period=str(op.params.get("period", "monthly")),
            )
            fx.mandates.append(MandateDelta(mandate_id, st.mandates[mandate_id].merchant,
                                            cap_before=-1, cap_after=cap))
            reference = " ".join(x for x in (str(op.params.get("merchant", "")),
                                             str(op.params.get("reference", ""))) if x)
            if reference:
                fx.outbound_text.append(("mandate_reference", reference))
            return

        if kind is OpKind.MODIFY_MANDATE:
            mandate = st.mandates.get(op.target)
            if mandate is None:
                return self._reject(fx, f"授权不存在 / unknown mandate {op.target}")
            before = mandate.cap
            mandate.cap = op.amount if op.amount > 0 else UNCAPPED
            fx.mandates.append(MandateDelta(mandate.mandate_id, mandate.merchant,
                                            cap_before=before, cap_after=mandate.cap))
            return

        if kind is OpKind.CANCEL_MANDATE:
            mandate = st.mandates.get(op.target)
            if mandate is None:
                return self._reject(fx, f"授权不存在 / unknown mandate {op.target}")
            before = mandate.cap
            mandate.status = "cancelled"
            st.mandates.pop(op.target, None)
            fx.mandates.append(MandateDelta(mandate.mandate_id, mandate.merchant,
                                            cap_before=before, cap_after=-1))
            return

        # --- 账户设置 ---------------------------------------------------
        if kind is OpKind.CHANGE_PHONE:
            before = st.customer.phone
            st.customer.phone = str(op.params.get("value", ""))
            fx.credentials.append(CredentialDelta(
                "phone", _mask_contact(before), _mask_contact(st.customer.phone)))
            return

        if kind is OpKind.CHANGE_EMAIL:
            before = st.customer.email
            st.customer.email = str(op.params.get("value", ""))
            fx.credentials.append(CredentialDelta(
                "email", _mask_contact(before), _mask_contact(st.customer.email)))
            return

        self._reject(fx, f"不支持的操作 / unsupported operation {kind.value}")

    @staticmethod
    def _reject(fx: EffectSet, reason: str) -> None:
        fx.rejected = True
        fx.reject_reason = reason


def _mask_contact(value: str) -> str:
    text = str(value)
    if len(text) <= 4:
        return "***"
    return f"{text[:3]}****{text[-2:]}"
