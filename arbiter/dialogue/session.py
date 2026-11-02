"""
多轮对话管理 / The multi-turn session.

一轮对话是这样走的：

    听懂 → 这一句是不是在接我上一轮的话（确认、拒绝、更正、回答追问）
         → 补全槽位（缺什么一次问清，并把选项列出来）
         → 生成授权信封（几件事合成一份、签一次；风险提示写在信封里）
         → 客户签署 → 内核裁决 → 执行 / 请客户再确认 / 解释拒绝并给出下一步

这一层全部位于不可信侧。它决定问什么、怎么说，决定不了放行什么——那是内核的事，
而且内核不读这里产生的任何文本。把对话层整个换成一个真正的大模型，安全性质一条都
不会变。

**一句「好」只回答紧挨着的上一个问题。** 这是自动审计抓到的最严重的一个问题：上一版
把「待签署的授权」存在会话里，客户中途问了别的事，过了几轮再说一句「好」，那份早已
不在屏幕上的授权就被执行了。现在每一轮开始时，上一轮留下的待办都会被取走；只有这一轮
明确接着它说话，它才继续存在。其余任何输入都会让它作废，并明确告诉客户「刚才那份授权
已作废，没有执行」。

**缺的槽位一次问清，并给出选项。** 「转多少、转给谁？」合成一句问；问「转给谁」时把
常用收款人编号列出来，客户回「第一个」也接得住。

**拒绝必须附带出路。** 只会说「不行」的助手，客户会绕开它去用网银，而被绕开的安全
控制保护不了任何人。
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from arbiter.audit.ledger import AuditLedger
from arbiter.authz.signer import Ed25519Keyring, ExecutionAuthoriser
from arbiter.bank.core import BankCore
from arbiter.bank.world import CUSTOMER_ID
from arbiter.dialogue import answers
from arbiter.dialogue.i18n import t
from arbiter.dialogue.nlu import (
    ACTION_ACTS,
    CATALOGUE_ACTS,
    READ_ACTS,
    Act,
    Frame,
    RuleBasedNLU,
    Utterance,
)
from arbiter.dialogue.plans import (  # noqa: F401  (resolvers re-exported for tests and tools)
    Step,
    _resolve_card,
    _resolve_holding,
    _resolve_mandate,
    _resolve_payee,
    _resolve_product,
    available,
    build_plan,
    compile_spec,
    read_plan,
    read_spec,
    resolve_frame,
)
from arbiter.dialogue.responder import describe_escalation, explain_refusal, fraud_warning
from arbiter.intent.ratify import ratify_with, render, structured
from arbiter.kernel.adjudicator import Adjudicator, KernelConfig
from arbiter.types import InvariantKind, OpKind, Verdict, cny, mask_account, yuan

MAX_TEXT = 1000          # 理解时只看前这么多字——银行请求不会更长，这也给延迟设了上限
HISTORY_LIMIT = 60

INFO_ACTS = frozenset({Act.NO_DATA, Act.OUT_OF_SCOPE, Act.CREDENTIAL, Act.SENSITIVE_INFO, Act.HELP})
TASK_ACTS = ACTION_ACTS | READ_ACTS

# 回答「要取消哪一项」时，只有明确说了「全部」才算全部——「都可以」不算。
_ALL_ANSWER = re.compile(r"(全部|所有|统统|全都|都(要|取消|退|停|冻|关|不要)|两个都|两张都|三个都"
                         r"|\ball\b|every|both)")

# 回答「您想做哪一样」时用到的关键词
_CHOICE = {
    Act.TRANSFER: re.compile(r"(转账|转给|转过去|汇款|打款|转钱|^转|transfer|send)"),
    Act.INVEST: re.compile(r"(理财|申购|投资|买|基金|invest|fund|buy)"),
    Act.CARD_UNFREEZE: re.compile(r"(解冻|解锁|恢复|取消冻结|unfreeze|unlock)"),
    Act.CARD_FREEZE: re.compile(r"((?<!解)冻结|(?<!解)锁|停用|挂失|^冻|freeze|lock|block)"),
    Act.CARD_LIMIT: re.compile(r"(额度|限额|上限|limit)"),
}

_GUESS = {Act.TRANSFER: "g_transfer", Act.INVEST: "g_invest", Act.REDEEM: "g_redeem",
          Act.CANCEL_MANDATE: "g_cancel", Act.CARD_FREEZE: "g_freeze",
          Act.CARD_UNFREEZE: "g_unfreeze", Act.CARD_LIMIT: "g_limit"}

# 填槽与更正时，哪些键要一起换 / keys that travel together
_GROUPS: dict[str, tuple[str, ...]] = {
    "amount": ("amount",),
    "payee": ("payee", "payee_account"),
    "mandate": ("mandate", "mandates", "scope_all"),
    "card": ("card", "cards", "scope_all"),
    "product": ("product",),
    "risk": ("risk",),
    "limit_which": ("limit_which",),
}
# 每一种动作用得到哪些槽位 / which slot groups each action takes
_USES: dict[Act, tuple[str, ...]] = {
    Act.TRANSFER: ("amount", "payee"),
    Act.INVEST: ("amount", "product", "risk"),
    Act.REDEEM: ("product", "amount"),
    Act.CANCEL_MANDATE: ("mandate",),
    Act.CARD_FREEZE: ("card",),
    Act.CARD_UNFREEZE: ("card",),
    Act.CARD_LIMIT: ("card", "amount", "limit_which"),
}


# ---------------------------------------------------------------------------
# 数据结构 / Data
# ---------------------------------------------------------------------------

@dataclass
class Reply:
    """智能体的一轮回复 / One turn of the agent's output."""
    text: str
    kind: str = "info"            # info | ask | envelope | result | refusal | escalation
    envelope: str | None = None   # 待签署的授权文本（由代码从结构化信封渲染）
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "kind": self.kind,
                "envelope": self.envelope, "detail": self.detail}


@dataclass
class Pending:
    """
    上一轮留下的、等着客户接话的事 / What the last turn left waiting for an answer.

        slots    在问缺的槽位            choose   在问「您想做哪一样」
        offer    在问「要这样吗」         ratify   授权信封等客户签署
        confirm  内核要求客户再确认一次   amend    客户说了「不对」，在等改成什么
    """
    kind: str
    frames: list[Frame] = field(default_factory=list)
    asking: int = 0
    missing: list[str] = field(default_factory=list)
    options: dict[str, list[tuple[str, Any]]] = field(default_factory=dict)
    choices: list[Act] = field(default_factory=list)
    held: dict[str, Any] = field(default_factory=dict)
    offer: tuple[Act, dict[str, Any]] | None = None
    steps: list[Step] = field(default_factory=list)
    spec: Any = None
    ratified: Any = None
    plan: Any = None
    report: Any = None
    question: str = ""
    envelope: str = ""
    reask: int = 0
    turn: int = 0

    @property
    def token(self) -> str:
        """界面按钮带回来的凭据：确认的必须是屏幕上那一份。"""
        if self.kind == "ratify" and self.spec is not None:
            return self.spec.digest()
        if self.kind == "confirm" and self.plan is not None:
            return self.plan.digest()
        return ""


def _copy_frame(f: Frame) -> Frame:
    return Frame(f.act, dict(f.slots), f.route, f.confidence, f.text, set(f.flags), list(f.missing))


# ---------------------------------------------------------------------------
# 会话 / The session
# ---------------------------------------------------------------------------

class Session:
    """
    一位客户的一次会话 / One customer's conversation.

    会话持有核心系统、内核与审计日志。它不持有任何「已获批准」的状态——每一个
    计划都要重新经过内核，哪怕上一轮刚刚批准过一个很像的。
    """

    def __init__(self, core: BankCore | None = None, customer_id: str = CUSTOMER_ID,
                 ledger_path: str | None = None):
        from arbiter.bank.world import build_world

        self.core = core or build_world()
        self.customer_id = customer_id
        self.keyring = Ed25519Keyring.generate(seed=customer_id.encode()).bind(customer_id)
        self.ledger = AuditLedger(ledger_path)
        self.kernel = Adjudicator(self.core, KernelConfig(), verifier=self.keyring)
        self.authoriser = ExecutionAuthoriser(self.keyring)
        self.nlu = RuleBasedNLU(self.core)

        self.history: list[dict[str, str]] = []
        self.lang = "zh"
        self.turn = 0
        self.pending: Pending | None = None
        self.last_report = None                 # 最近一次内核裁决，供界面右栏显示
        self._ui_lang: str | None = None
        self._warned_reads = False
        self._last_text = ""
        self._outcome: str | None = None        # 这一轮让上一份授权落了什么结局
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # 对外接口 / Public surface
    # ------------------------------------------------------------------

    def say(self, text: str, digest: str | None = None, lang: str | None = None) -> Reply:
        """
        处理一轮客户输入 / Handle one customer turn.

        `digest` 由界面上的「确认」「取消」按钮带回：它必须等于此刻待确认的那一份
        授权（或那一次再确认）的摘要，否则视为一个过期的按钮，不执行任何东西。
        `lang` 是界面上的语言开关；客户实际用哪种语言说话优先于它。
        """
        with self._lock:
            self.turn += 1
            text = (text or "").replace("\x00", "")
            if lang in ("zh", "en") and lang != self._ui_lang:
                self._ui_lang = lang
                self.lang = lang
            pending, self.pending = self.pending, None      # 上一轮的待办，这一轮取走
            self._outcome = None
            reply = self._turn(text, pending, digest)
            reply.detail.setdefault("lang", self.lang)
            self._report_slip(pending, reply)
            self.history.append({"role": "user", "text": text[:200]})
            self.history.append({"role": "agent", "text": reply.text[:200], "kind": reply.kind})
            del self.history[:-HISTORY_LIMIT]
            self._last_text = reply.text
            return reply

    def greet(self) -> str:
        return t("greeting", self.lang, name=self.core.state.customer.name)

    def _report_slip(self, p: Pending | None, reply: Reply) -> None:
        """
        告诉界面：上一轮摆出来的那张授权凭证，这一轮之后是什么状态。

        凭证在屏幕上一直留着，所以它的结局必须写在它自己身上——已执行、已拒绝、
        已取消、已作废、已被新的一份替换——而不是让客户去对照上下文猜。
        """
        if p is None or p.kind not in ("ratify", "confirm") or p.spec is None:
            return
        digest = p.spec.digest()
        now = self.pending
        open_now = (now is not None and now.kind in ("ratify", "confirm") and now.spec is not None
                    and now.spec.digest() == digest)
        if self._outcome:
            state = self._outcome
        elif open_now:
            state = "escalated" if now.kind == "confirm" else "open"
        elif reply.kind == "envelope":
            state = "replaced"
        else:
            state = "withdrawn"
        reply.detail["slip"] = {"digest": digest, "state": state}

    # 兼容旧接口与测试 / compatibility views ---------------------------------

    @property
    def pending_act(self) -> Act | None:
        p = self.pending
        if p is None or p.kind in ("offer", "choose") or not p.frames:
            return None
        return p.frames[p.asking if p.kind == "slots" else 0].act

    @property
    def offer(self) -> tuple[Act, dict[str, Any]] | None:
        p = self.pending
        return p.offer if p is not None and p.kind == "offer" else None

    @property
    def _reask_count(self) -> int:
        return self.pending.reask if self.pending is not None else 0

    @property
    def _report(self):
        return self.last_report

    def envelope_view(self) -> dict[str, Any]:
        """待签署信封的结构化视图（供审计脚本核对方向、目标与金额）。"""
        p = self.pending
        empty = {"act": None, "acts": [], "targets": [], "amounts": [], "slots": {},
                 "target": None, "amount": None, "out_cap": None}
        if p is None or p.kind not in ("ratify", "confirm") or p.spec is None:
            return empty
        out_cap = next((inv.limit for inv in p.spec.invariants
                        if inv.kind is InvariantKind.MAX_TOTAL_OUT), None)
        acts = [s.act.value for s in p.steps]
        targets = [s.target for s in p.steps]
        amounts = [s.amount for s in p.steps if s.amount]
        return {"act": acts[0] if acts else None, "acts": acts, "targets": targets,
                "amounts": amounts, "slots": {}, "target": targets[0] if targets else None,
                "amount": amounts[0] if amounts else None, "out_cap": out_cap}

    # ------------------------------------------------------------------
    # 一轮 / One turn
    # ------------------------------------------------------------------

    def _turn(self, text: str, p: Pending | None, digest: str | None) -> Reply:
        L = self.lang
        if digest is not None and (p is None or p.kind not in ("ratify", "confirm")
                                   or p.token != digest):
            # 过期的按钮：什么都不执行。如果此刻另有一份在等，就把那一份重新摆出来。
            if p is not None and p.kind in ("ratify", "confirm"):
                return self._reprompt(p, lead=t("stale_digest", L))
            return Reply(t("stale_digest", L), "info")

        u = self.nlu.understand(text[:MAX_TEXT], self.history)
        if u.lang_signal and u.act not in (Act.CONFIRM, Act.DENY):
            self.lang = u.lang
        L = self.lang

        # ---- 整句应答 / whole-utterance replies -------------------------
        if u.act is Act.CONFIRM:
            return self._on_confirm(p)
        if u.act is Act.DENY:
            return self._on_deny(p)
        if u.act is Act.CORRECTION:
            return self._on_correction(p, u, text)
        if u.act is Act.THANKS:
            if p is not None and p.kind in ("ratify", "confirm"):
                return self._reprompt(p)         # 「好的谢谢」不是确认：确认必须没有歧义
            return Reply(t("thanks", L), "info")
        if u.act in (Act.BYE, Act.GREETING, Act.HELP):
            key = {Act.BYE: "bye", Act.GREETING: "greeting", Act.HELP: "help"}[u.act]
            return Reply(self._join(self._withdrawn(p),
                                    t(key, L, name=self.core.state.customer.name)), "info")

        # ---- 是不是在接上一轮的话 / is this an answer to what was asked? ----
        if p is not None:
            handled = self._continue(p, u, text)
            if handled is not None:
                return handled

        lead = [self._withdrawn(p)]
        if "leading_deny" in u.flags and p is not None and p.kind not in ("ratify", "confirm"):
            lead = [t("denied_done", L)]
        return self._process(u, text, lead)

    # ------------------------------------------------------------------
    # 接话 / Continuing what the last turn left open
    # ------------------------------------------------------------------

    def _continue(self, p: Pending, u: Utterance, text: str) -> Reply | None:
        """返回 None 表示这一轮不是在接话：上一轮的待办作废，按新输入处理。"""
        if p.kind in ("ratify", "confirm"):
            if u.act is Act.UNKNOWN and not u.frames:
                if p.reask >= 1:
                    return None
                p.reask += 1
                return self._reprompt(p)
            return None

        if p.kind == "amend":
            slots = self._amendable(self._answer_slots(u, text))
            new = [f for f in u.frames if f.route == "rule" and f.act in TASK_ACTS]
            if slots and not any(f.act is not p.frames[0].act for f in new):
                return self._amend(p, slots, u, text)
            return None

        if p.kind == "offer":
            return self._continue_offer(p, u, text)

        if p.kind == "choose":
            return self._continue_choose(p, u, text)

        if p.kind == "slots":
            return self._fill(p, u, text)
        return None

    def _continue_offer(self, p: Pending, u: Utterance, text: str) -> Reply | None:
        assert p.offer is not None
        act, oslots = p.offer
        rule_tasks = [f for f in u.frames if f.route == "rule" and f.act in TASK_ACTS]
        if any(f.act is not act for f in rule_tasks):
            return None                                   # 客户明确说了别的事
        if act in READ_ACTS:
            return None                                   # 读一次，正常流程就能处理
        uslots = self._answer_slots(u, text)
        if act in (Act.INVEST, Act.TRANSFER, Act.CARD_LIMIT) and uslots.get("amount"):
            merged = dict(oslots)
            merged["amount"] = uslots["amount"]
            f = Frame(act, merged, "rule", 1.0, text)
            f.missing = self.nlu._missing(act, merged)
            return self._plan([f], [])
        same = [f for f in rule_tasks if f.act is act]
        if same:
            merged = {**oslots, **same[0].slots}
            f = Frame(act, merged, "rule", 1.0, text, set(same[0].flags))
            f.missing = self.nlu._missing(act, merged)
            return self._plan([f], [])
        return None

    def _continue_choose(self, p: Pending, u: Utterance, text: str) -> Reply | None:
        chosen: Act | None = None
        if u.ordinal is not None:
            idx = u.ordinal - 1 if u.ordinal > 0 else len(p.choices) - 1
            if 0 <= idx < len(p.choices):
                chosen = p.choices[idx]
        if chosen is None:
            low = text.lower()
            for act in sorted(p.choices, key=lambda a: a is not Act.CARD_UNFREEZE):
                if _CHOICE[act].search(low):
                    chosen = act
                    break
        if chosen is None:
            explicit = [f for f in u.frames if f.act in ACTION_ACTS and f.route != "confirm"]
            if explicit:
                chosen = explicit[0].act
        if chosen is not None:
            slots = self._answer_slots(u, text)
            slots = {k: v for k, v in slots.items()
                     if any(k in _GROUPS[g] for g in _USES.get(chosen, ()))}
            for key, value in p.held.items():
                group = next((g for g, keys in _GROUPS.items() if key in keys), key)
                if not self._present(slots, group):
                    slots[key] = value
            f = Frame(chosen, slots, "rule", 1.0, text)
            f.missing = self.nlu._missing(chosen, slots)
            return self._plan([f], [])
        if any(f.route == "rule" and f.act not in (Act.REFERENCE, Act.AMOUNT_ONLY) for f in u.frames):
            return None
        return self._reask(p)

    def _fill(self, p: Pending, u: Utterance, text: str) -> Reply | None:
        frames = [_copy_frame(f) for f in p.frames]
        frame = frames[p.asking]

        # 客户明确说了另一件事（规则命中、意图不同）：换话题，不硬塞进当前问题。
        rule_tasks = [f for f in u.frames if f.route == "rule" and f.act in TASK_ACTS]
        lookup_mandates = ("mandate" in p.missing
                           and all(f.act is Act.LIST_MANDATES for f in rule_tasks) and rule_tasks)
        if any(f.act is not frame.act for f in rule_tasks) and not lookup_mandates:
            return None

        uslots = self._answer_slots(u, text)
        useful = self._useful(uslots, p.missing, text)
        if u.ordinal is not None:
            picked = self._pick(p, u.ordinal, text)
            if picked:
                useful.update(picked)
        if useful:
            self._apply(frame, useful)
            need = self._need(frame)
            if need:
                return self._ask(frames, p.asking, need, [])
            return self._plan(frames, [])

        # 填到一半想先查点什么：答完，再把问题问一遍——不丢掉已经说过的话。
        side = [f for f in u.frames
                if f.act in CATALOGUE_ACTS or f.act in INFO_ACTS or f.act is Act.LIST_MANDATES]
        if side and len(side) == len(u.frames):
            blocks = [self._side_answer(f) for f in side]
            covered = {Act.LIST_PAYEES: "payee", Act.LIST_CARDS: "card", Act.LIST_MANDATES: "mandate"}
            show = not any(covered.get(f.act) in p.missing for f in side)
            q = self._question(frame.act, p.missing, p.options if show else {})
            self._hold(Pending("slots", frames=frames, asking=p.asking, missing=list(p.missing),
                               options=p.options, question=q, reask=p.reask))
            return Reply(self._join(*blocks, q), "ask", detail={"missing": list(p.missing)})

        return self._reask(p)

    # ------------------------------------------------------------------
    # 确认、拒绝、更正 / Confirm, deny, correct
    # ------------------------------------------------------------------

    def _on_confirm(self, p: Pending | None) -> Reply:
        L = self.lang
        if p is None:
            # 「还有什么需要我做的吗？」之后的一句「好」，是「有」，不是「确认」。
            if self._last_text.rstrip().endswith(("吗？", "吗?", "?", "？")):
                return Reply(t("go_ahead_what", L), "ask")
            return Reply(t("nothing_pending", L), "ask")
        if p.kind == "ratify":
            return self._ratify(p)
        if p.kind == "confirm":
            return self._approve(p)
        if p.kind == "offer" and p.offer is not None:
            act, slots = p.offer
            f = Frame(act, dict(slots), "rule", 1.0, "")
            f.missing = self.nlu._missing(act, f.slots)
            if act in READ_ACTS:
                body, _ = self._read(f)
                return Reply(body, "result")
            return self._plan([f], [])
        return self._reask(p)            # 一句「好」回答不了「转给谁」

    def _on_deny(self, p: Pending | None) -> Reply:
        L = self.lang
        if p is None:
            return Reply(t("denied_nothing", L), "info")
        if p.kind in ("ratify", "confirm"):
            self.ledger.append("declined_by_customer", {"digest": p.token, "kind": p.kind})
            self._outcome = "cancelled"
            return Reply(t("denied_done", L), "info")
        if p.kind == "offer":
            return Reply(t("denied_offer", L), "info")
        return Reply(t("denied_done", L), "info")

    def _on_correction(self, p: Pending | None, u: Utterance, text: str) -> Reply:
        L = self.lang
        base: list[Frame] = []
        if p is not None and p.kind in ("ratify", "confirm", "slots", "amend"):
            base = [_copy_frame(f) for f in p.frames]
        elif p is not None and p.kind == "offer" and p.offer is not None and p.offer[0] in ACTION_ACTS:
            base = [Frame(p.offer[0], dict(p.offer[1]))]
        slots = self._amendable(self._answer_slots(u, text))
        new = [f for f in u.frames if f.act in ACTION_ACTS and f.route == "rule"]
        if base:
            if new and all(f.act is not base[0].act for f in new):
                return self._process(u, text, [self._withdrawn(p)])   # 「不对，我是要冻结卡」
            if slots:
                return self._amend(Pending("amend", frames=base), slots, u, text, p)
            self._hold(Pending("amend", frames=base, question=t("correction_need", L)))
            return Reply(self._join(self._withdrawn(p), t("correction_need", L)), "ask")
        if u.frames:
            return self._process(u, text, [])
        return Reply(t("correction_none", L), "ask")

    def _amend(self, p: Pending, slots: dict[str, Any], u: Utterance, text: str,
               original: Pending | None = None) -> Reply:
        frames = [_copy_frame(f) for f in p.frames]
        changed = False
        for group in ("amount", "payee", "card", "mandate", "product", "risk", "limit_which"):
            if not self._present(slots, group):
                continue
            target = next((f for f in frames if group in _USES.get(f.act, ())), None)
            if target is None:
                continue
            for key in _GROUPS[group]:
                target.slots.pop(key, None)
            for key in _GROUPS[group]:
                if key in slots:
                    target.slots[key] = slots[key]
            if group == "amount":
                target.flags.discard("amount_invalid")
            target.missing = self.nlu._missing(target.act, target.slots)
            changed = True
        if not changed:
            return self._process(u, text, [self._withdrawn(original)])
        if original is not None and original.kind in ("ratify", "confirm"):
            self.ledger.append("amended_by_customer", {"digest": original.token})
            self._outcome = "replaced"
        return self._plan(frames, [t("amended", self.lang)])

    # ------------------------------------------------------------------
    # 新输入 / Fresh input
    # ------------------------------------------------------------------

    def _process(self, u: Utterance, text: str, lead: list[str]) -> Reply:
        L = self.lang
        frames = [f for f in u.frames if f.act not in (Act.GREETING, Act.THANKS, Act.UNKNOWN)]
        if not frames:
            return Reply(self._join(*lead, t("unknown", L)), "ask")

        blocks = list(lead)
        read_ran = False
        question = ""
        actions: list[Frame] = []
        refs: list[Frame] = []
        amounts: list[Frame] = []
        for f in frames:
            if f.act in READ_ACTS:
                body, q = self._read(f)
                blocks.append(body)
                read_ran = True
                question = question or q
            elif f.act in CATALOGUE_ACTS or f.act in INFO_ACTS:
                blocks.append(self._side_answer(f))
            elif f.act in ACTION_ACTS:
                actions.append(f)
            elif f.act is Act.REFERENCE:
                refs.append(f)
            elif f.act is Act.AMOUNT_ONLY:
                amounts.append(f)
        base_kind = "result" if read_ran else "info"

        if actions:
            sure = [f for f in actions if f.route != "confirm"]
            if sure:
                return self._plan(sure, blocks, base_kind)
            # 相似度只有几分把握：不猜，问一句。猜错一次就是让客户去读一份他没要的授权。
            guess = actions[0]
            q = t("confirm_guess", L, what=t(_GUESS[guess.act], L))
            self._hold(Pending("offer", offer=(guess.act, dict(guess.slots)), question=q))
            return Reply(self._join(*blocks, q), "ask")
        if refs:
            return self._reference(refs[0], blocks, base_kind)
        if amounts:
            return self._bare_amount(amounts[0], blocks)
        if question:
            # 问了就要能接住回答：「需要我帮您看看哪些可以取消吗？」之后的「好」
            self._hold(Pending("offer", offer=(Act.LIST_MANDATES, {}), question=question))
            blocks.append(question)
        return Reply(self._join(*blocks), base_kind)

    def _plan(self, frames: list[Frame], prefix: list[str], base_kind: str = "info") -> Reply:
        """把一件或几件要做的事落成一份信封；缺什么就问什么。"""
        L = self.lang
        steps: list[Step] = []
        notes: list[str] = []
        refusal = False
        offer: tuple[Act, dict[str, Any]] | None = None
        offer_note = ""
        for i, f in enumerate(frames):
            need = self._need(f)
            if need:
                return self._ask(frames, i, need, prefix + notes)
            res = resolve_frame(f, self.core)
            for zh, en in res.notes:
                note = en if L == "en" else zh
                if note not in notes:
                    notes.append(note)
            if res.ask is not None:
                slot, _opts = res.ask
                return self._ask(frames, i, [slot], prefix + notes)
            refusal = refusal or res.refusal
            if res.offer is not None and offer is None:
                act, oslots = res.offer
                oslots = dict(oslots)
                if act is Act.INVEST and f.slots.get("amount"):
                    oslots.setdefault("amount", f.slots["amount"])
                offer = (act, oslots)
                if res.offer_note:
                    offer_note = res.offer_note[1] if L == "en" else res.offer_note[0]
            steps.extend(res.steps)

        if steps:
            return self._envelope(frames, steps, prefix + notes)
        if offer is not None:
            self._hold(Pending("offer", offer=offer, question=offer_note))
            return Reply(self._join(*prefix, *notes, offer_note), "refusal" if refusal else "ask")
        kind = "refusal" if refusal else base_kind
        return Reply(self._join(*prefix, *notes) or t("unknown", L), kind)

    def _envelope(self, frames: list[Frame], steps: list[Step], blocks: list[str]) -> Reply:
        L = self.lang
        now = int(time.time())
        spec = compile_spec(steps, self.core, self.customer_id, now)
        self.ledger.append("intent_compiled", {"digest": spec.digest(), "scenario": spec.scenario,
                                               "steps": len(steps), "tier": spec.max_tier.value})
        if len(steps) == 1:
            intro = t("envelope_intro", L, goal=spec.goal_en if L == "en" else spec.goal_zh)
        else:
            lines = "\n".join(f"  {i}. {s.desc_en if L == 'en' else s.desc_zh}"
                              for i, s in enumerate(steps, 1))
            intro = t("envelope_intro_multi", L, n=len(steps), steps=lines)
        env = render(spec, L)
        p = Pending("ratify", frames=frames, steps=steps, spec=spec, envelope=env, question=intro)
        self._hold(p)
        return Reply(self._join(*blocks, intro), "envelope", envelope=env,
                     detail={"digest": p.token, "scenario": spec.scenario, "steps": len(steps),
                             "tier": spec.max_tier.label, "voucher": structured(spec, L)})

    # ------------------------------------------------------------------
    # 签署、裁决、执行 / Ratify, adjudicate, execute
    # ------------------------------------------------------------------

    def _ratify(self, p: Pending) -> Reply:
        L = self.lang
        now = int(time.time())
        if now > p.spec.expires_at:
            self._outcome = "expired"
            return self._plan(p.frames, [t("expired_renewed", L)])
        ratified = ratify_with(p.spec, self.keyring, now)
        self.ledger.append("intent_ratified", {"digest": p.spec.digest()})
        plan = build_plan(p.steps, p.spec)
        report = self.kernel.adjudicate(ratified, plan, now, self.core.untrusted_context())
        self.last_report = report
        self.ledger.append("adjudicated", report.to_dict())

        verdict = report.decision.verdict
        if verdict is Verdict.DENY:
            return self._refusal(report)
        if verdict is Verdict.REQUIRE_APPROVAL:
            q = describe_escalation(report.decision, L) + t("go_on", L)
            nxt = Pending("confirm", frames=p.frames, steps=p.steps, spec=p.spec,
                          ratified=ratified, plan=plan, report=report, question=q)
            self._hold(nxt)
            return Reply(q, "escalation", detail={"tier": report.decision.tier.label,
                                                  "digest": nxt.token,
                                                  "slip_digest": p.spec.digest()})
        return self._execute(ratified, plan, report)

    def _approve(self, p: Pending) -> Reply:
        """
        客户对内核的再确认说了「好」。

        这里不直接执行上一轮的裁决，而是**按同一份计划重新裁决一次**，并要求观测到的
        效果与客户看到的那一次完全一致。两轮之间账户状态如果变了，客户确认的就已经
        不是眼前这件事。
        """
        L = self.lang
        now = int(time.time())
        if now > p.spec.expires_at:
            self._outcome = "expired"
            return self._plan(p.frames, [t("expired_renewed", L)])
        report = self.kernel.adjudicate(p.ratified, p.plan, now, self.core.untrusted_context())
        self.last_report = report
        self.ledger.append("adjudicated", report.to_dict())
        if report.decision.verdict is Verdict.DENY:
            return self._refusal(report)
        if (report.decision.effects_digest != p.report.decision.effects_digest
                or report.decision.plan_digest != p.plan.digest()):
            self._outcome = "refused"
            return Reply(t("plan_changed", L), "refusal")
        self.ledger.append("customer_approved", {"plan_digest": p.plan.digest(),
                                                 "tier": report.decision.tier.value})
        report.decision.verdict = Verdict.ALLOW
        return self._execute(p.ratified, p.plan, report)

    def _execute(self, ratified, plan, report) -> Reply:
        L = self.lang
        try:
            self.authoriser.authorise(plan, report.decision, ratified)
            receipts = self.core.commit(plan.operations)
        except Exception as exc:  # noqa: BLE001 — a failed execution must never look like success
            self.ledger.append("execution_failed", {"error": f"{type(exc).__name__}"})
            self._outcome = "refused"
            why = str(exc)
            reason = t("exec_reason_balance" if ("余额不足" in why or "insufficient" in why)
                       else "exec_reason_core", L)
            return Reply(t("exec_error", L, err=reason), "refusal")
        self.ledger.append("executed", {"receipts": receipts, "plan_digest": plan.digest()})
        self._outcome = "executed"
        text = self._receipt(report, plan, receipts) + fraud_warning(report, L)
        return Reply(text, "result", detail={"receipts": receipts})

    def _refusal(self, report) -> Reply:
        L = self.lang
        self._outcome = "refused"
        rec = explain_refusal(report.decision, L)
        return Reply(t("refused", L, reason=rec.reason, suggestion=rec.suggestion), "refusal",
                     detail={"findings": [f.code for f in report.decision.blocking()]})

    def _receipt(self, report, plan, receipts: list[str]) -> str:
        """回执写的是观测到的效果，而不是「我本来打算做什么」。"""
        L = self.lang
        fx = report.effects
        lines: list[str] = []
        if fx is not None:
            paid = set(fx.payees_paid)
            for op in plan.operations:
                if op.kind is OpKind.TRANSFER and op.target in paid:
                    payee = self.core.resolve_payee(op.target)
                    if payee is not None:
                        name = (f"{payee.name} ({mask_account(op.target)})" if L == "en"
                                else f"{payee.name}（{mask_account(op.target)}）")
                    else:
                        name = (f"account ({mask_account(op.target)})" if L == "en"
                                else f"账号（{mask_account(op.target)}）")
                    lines.append(t("r_transfer", L, name=name, amount=cny(op.amount)))
            for m in fx.mandates:
                if m.cancelled:
                    lines.append(t("r_cancel", L, name=m.merchant))
                elif m.created:
                    lines.append(t("r_mandate_new", L, name=m.merchant, cap=cny(m.cap_after)))
            for c in fx.card_controls:
                if c.control == "status":
                    lines.append(t("r_frozen" if c.after == "frozen" else "r_unfrozen", L, card=c.card_id))
                else:
                    label = t(f"ctl_{c.control}", L) if f"ctl_{c.control}" in _CTL else c.control
                    value = cny(c.after) if isinstance(c.after, int) and not isinstance(c.after, bool) \
                        else str(c.after)
                    lines.append(t("r_limit", L, card=c.card_id, control=label, value=value))
            for h in fx.holdings:
                if h.amount > 0:
                    lines.append(t("r_buy", L, name=h.product_name, amount=cny(h.amount)))
                elif h.amount < 0:
                    lines.append(t("r_sell", L, name=h.product_name, amount=cny(-h.amount)))
            if any(b.delta for b in fx.balances):
                lines.append(t("r_balance", L, balance=cny(available(self.core))))
        body = (t("done", L) + "\n" + "\n".join(f"· {line}" for line in lines)) if lines \
            else t("done_nothing", L)
        if receipts:
            body += "\n" + t("r_receipt", L, ids=", ".join(receipts))
        return body

    # ------------------------------------------------------------------
    # 只读 / Reads
    # ------------------------------------------------------------------

    def _read(self, f: Frame) -> tuple[str, str]:
        """
        只读查询同样经过内核：注入内容在这里被检测、记录并提醒客户——但只读操作
        不可能被它伤害，所以不拦。答复的每一个数字都由代码从核心系统读出。
        """
        L = self.lang
        now = int(time.time())
        spec = read_spec(self.core, self.customer_id, now)
        ratified = ratify_with(spec, self.keyring, now)
        plan = read_plan(f.act, spec)
        report = self.kernel.adjudicate(ratified, plan, now, self.core.untrusted_context())
        self.last_report = report
        self.ledger.append("adjudicated", report.to_dict())
        if report.decision.verdict is not Verdict.ALLOW:
            rec = explain_refusal(report.decision, L)
            return t("refused", L, reason=rec.reason, suggestion=rec.suggestion), ""
        self.authoriser.authorise(plan, report.decision, ratified)
        self.core.commit(plan.operations)
        self.ledger.append("executed", {"read": f.act.value})

        question = ""
        if f.act is Act.BALANCE:
            body = answers.balance(self.core, L)
        elif f.act is Act.LIST_MANDATES:
            body = answers.mandates(self.core, L)
        else:
            body, question = answers.statement(self.core, f.slots, L)
        if not self._warned_reads:
            warning = fraud_warning(report, L)
            if warning:
                self._warned_reads = True
                body += warning
        return body, question

    def _side_answer(self, f: Frame) -> str:
        """目录、说明、兜底：直接回答，不开启任务、不经过信封。"""
        L = self.lang
        a = f.act
        if a is Act.LIST_PRODUCTS:
            return answers.products(self.core, L)
        if a is Act.LIST_PAYEES:
            return answers.payees(self.core, L)
        if a is Act.LIST_CARDS:
            return answers.cards(self.core, L)
        if a is Act.LIST_MANDATES:
            return answers.mandates(self.core, L)
        if a is Act.NO_DATA:
            return t("no_data", L)
        if a is Act.OUT_OF_SCOPE:
            return t("out_of_scope", L)
        if a is Act.CREDENTIAL:
            return t("credential", L)
        if a is Act.HELP:
            return t("help", L)
        if a is Act.SENSITIVE_INFO:
            # 信息套取：无论怎么问、以谁的名义问，都不给。这里分辨的只是「怎么解释」。
            if "otp" in f.flags:
                return t("sensitive_otp", L)
            if "exfil" in f.flags:
                return t("sensitive_exfil", L)
            if "third_party" in f.flags:
                return t("sensitive_third_party", L)
            return t("sensitive", L)
        return t("unknown", L)

    # ------------------------------------------------------------------
    # 只说了一个名字、一个数 / A bare name, a bare number
    # ------------------------------------------------------------------

    def _reference(self, f: Frame, blocks: list[str], base_kind: str) -> Reply:
        """
        客户只说了一个名字——卡号、商户、产品、收款人——没有动词。
        「不知道你要干什么」和「不知道你在说什么」是两回事：说清它是什么，再问要做什么。
        """
        L = self.lang
        en = L == "en"
        st = self.core.state
        slots = f.slots

        card = _resolve_card(self.core, slots.get("card", ""))
        if card in st.cards:
            c = st.cards[card]
            frozen = c.status == "frozen"
            text = t("ref_card", L, card=card,
                     kind=t("card_credit" if c.kind == "credit" else "card_debit", "zh"),
                     kind_en=t("card_credit" if c.kind == "credit" else "card_debit", "en"),
                     state=t("state_frozen" if frozen else "state_active", "zh"),
                     state_en=t("state_frozen" if frozen else "state_active", "en"),
                     action=t("action_unfreeze" if frozen else "action_freeze", "zh"),
                     action_en=t("action_unfreeze" if frozen else "action_freeze", "en"))
            choices = [Act.CARD_UNFREEZE if frozen else Act.CARD_FREEZE, Act.CARD_LIMIT]
            self._hold(Pending("choose", choices=choices, held={"card": card}, question=text))
            return Reply(self._join(*blocks, text), "ask")

        key = slots.get("mandate")
        if key:
            mid = _resolve_mandate(self.core, key)
            if mid in st.mandates:
                m = st.mandates[mid]
                text = t("ref_mandate", L, name=m.merchant, cap=cny(m.cap), next=m.next_charge)
                self._hold(Pending("offer", offer=(Act.CANCEL_MANDATE, {"mandate": mid}), question=text))
                return Reply(self._join(*blocks, text), "ask")
            from arbiter.dialogue.plans import mandate_items, mandate_label
            text = t("noop_mandate_gone", L, name=mandate_label(self.core, key),
                     items=mandate_items(self.core, L))
            return Reply(self._join(*blocks, text), "info")

        code = slots.get("product")
        if code in st.products:
            pr = st.products[code]
            grade = st.customer.risk_tolerance
            line = (f"{pr.name} ({code}) · {pr.risk_level.label_en} · minimum {cny(pr.min_amount)}."
                    if en else f"{pr.name}（{code}）·{pr.risk_level.label_zh}·起购 {cny(pr.min_amount)}。")
            if pr.risk_level.value > grade.value:
                # 不能买这只，但不把客户丢在死路上：在评级内挑一只，**问他要不要**。
                note = t("noop_over_grade", L, name=pr.name, code=code, risk=pr.risk_level.label_zh,
                         risk_en=pr.risk_level.label_en, grade=grade.label_zh, grade_en=grade.label_en)
                alt = _resolve_product(self.core, grade.value)
                if alt:
                    q = t("alt_offer", L, name=st.products[alt].name)
                    self._hold(Pending("offer", offer=(Act.INVEST, {"product": alt}), question=q))
                    return Reply(self._join(*blocks, line, note, q), "ask")
                return Reply(self._join(*blocks, line, note), "refusal")
            text = t("ref_product", L, name=pr.name, code=code, risk=pr.risk_level.label_zh,
                     risk_en=pr.risk_level.label_en, min=cny(pr.min_amount))
            frame = Frame(Act.INVEST, {"product": code}, "rule", 1.0, f.text)
            frame.missing = ["amount"]
            self._hold(Pending("slots", frames=[frame], asking=0, missing=["amount"], question=text))
            return Reply(self._join(*blocks, text), "ask", detail={"missing": ["amount"]})

        account = slots.get("payee_account")
        payee = self.core.resolve_payee(account) if account else None
        if payee is not None:
            text = t("ref_payee", L, name=payee.name, bank=payee.bank, acct=mask_account(payee.account_no))
            frame = Frame(Act.TRANSFER, {"payee": "literal", "payee_account": payee.account_no},
                          "rule", 1.0, f.text)
            frame.missing = ["amount"]
            self._hold(Pending("slots", frames=[frame], asking=0, missing=["amount"], question=text))
            return Reply(self._join(*blocks, text), "ask", detail={"missing": ["amount"]})

        return Reply(self._join(*blocks, t("unknown", L)), "ask")

    def _bare_amount(self, f: Frame, blocks: list[str]) -> Reply:
        """只报了一个数，没说拿它做什么：问一句，不默认成转账。"""
        L = self.lang
        amount = int(f.slots.get("amount") or 0)
        if amount <= 0:
            return Reply(self._join(*blocks, t("unknown", L)), "ask")
        q = t("bare_amount", L, money=cny(amount))
        self._hold(Pending("choose", choices=[Act.TRANSFER, Act.INVEST], held={"amount": amount},
                           question=q))
        return Reply(self._join(*blocks, q), "ask")

    # ------------------------------------------------------------------
    # 追问 / Clarifying questions
    # ------------------------------------------------------------------

    def _need(self, f: Frame) -> list[str]:
        """
        还缺哪些槽位。有几种情况要让解析器先说话，而不是先追问：
        诈骗话术（先提醒）、无效金额（先说明）、超出评级的理财（先拒绝，不要问完金额再拒）、
        根本没有可取消的代扣（不要问「取消哪一项」再给一个空清单）。
        """
        if "scam" in f.flags or "amount_invalid" in f.flags:
            return []
        st = self.core.state
        if f.act is Act.INVEST:
            grade = st.customer.risk_tolerance.value
            named = st.products.get(f.slots.get("product") or "")
            if (named is not None and named.risk_level.value > grade) or \
                    (named is None and int(f.slots.get("risk", grade)) > grade):
                return []
        if f.act is Act.CANCEL_MANDATE and not st.mandates:
            return []
        # 「卡找到了，帮我解开」：能解冻的只有冻结着的卡；只有一张时不必再问是哪张
        if f.act is Act.CARD_UNFREEZE and not (f.slots.get("card") or f.slots.get("cards")
                                               or f.slots.get("scope_all")):
            frozen = [cid for cid, c in st.cards.items() if c.status == "frozen"]
            if len(frozen) == 1:
                f.slots["card"] = frozen[0]
        return self.nlu._missing(f.act, f.slots)

    def _ask(self, frames: list[Frame], i: int, missing: list[str], prefix: list[str],
             reask: int = 0) -> Reply:
        opts = self._options(missing)
        notes_before = any(prefix)
        q = self._question(frames[i].act, missing, opts, plain=notes_before)
        self._hold(Pending("slots", frames=frames, asking=i, missing=list(missing), options=opts,
                           question=q, reask=reask))
        return Reply(self._join(*prefix, q), "ask", detail={"missing": list(missing)})

    def _question(self, act: Act, missing: list[str], opts: dict[str, list[tuple[str, Any]]],
                  plain: bool = False, again: bool = False) -> str:
        L = self.lang
        keys = []
        for slot in missing:
            if slot == "amount":
                keys.append({Act.INVEST: "q_invest_amount", Act.CARD_LIMIT: "q_limit_amount"}
                            .get(act, "q_amount"))
            else:
                keys.append({"payee": "q_payee", "mandate": "q_mandate", "card": "q_card",
                             "product": "q_redeem"}.get(slot, "q_amount"))
        questions = t("q_join", L).join(t(k, L) for k in keys)
        if plain or again:
            head = t("ask_plain", L, questions=questions)
            if L == "en":
                head = head[:1].upper() + head[1:]
            if again:
                head = t("reask_prefix", L) + head
        else:
            head = t("ask_prefix", L, questions=questions)
        block = self._options_text(opts)
        return head + ("\n" + block if block else "")

    def _options(self, missing: list[str]) -> dict[str, list[tuple[str, Any]]]:
        """追问时给出的选项——编号与界面显示一致，客户回「第二个」也接得住。"""
        en = self.lang == "en"
        st = self.core.state
        out: dict[str, list[tuple[str, Any]]] = {}
        for slot in missing:
            if slot == "payee":
                out["payee"] = [((f"{p.name} ({p.alias}) · {p.bank} {mask_account(p.account_no)}" if en
                                  else f"{p.name}（{p.alias}）·{p.bank} {mask_account(p.account_no)}")
                                 if p.alias else f"{p.name} {mask_account(p.account_no)}", p.account_no)
                                for p in st.payees.values()]
            elif slot == "mandate":
                out["mandate"] = [((f"{m.merchant} {cny(m.cap)}/month" if en
                                    else f"{m.merchant} {cny(m.cap)}/月"), m.mandate_id)
                                  for m in st.mandates.values()]
            elif slot == "card":
                rows = []
                for c in st.cards.values():
                    kind = t("card_credit" if c.kind == "credit" else "card_debit", self.lang)
                    state = t("state_frozen" if c.status == "frozen" else "state_active", self.lang)
                    rows.append(((f"{c.card_id} ({kind}, {state})" if en
                                  else f"{c.card_id}（{kind}，{state}）"), c.card_id))
                out["card"] = rows
            elif slot == "product":
                out["product"] = [((f"{st.products[k].name} ({k}) {cny(h.amount)}" if en
                                    else f"{st.products[k].name}（{k}）{cny(h.amount)}"), k)
                                  for k, h in st.holdings.items() if h.amount > 0 and k in st.products]
        return out

    def _options_text(self, opts: dict[str, list[tuple[str, Any]]]) -> str:
        L = self.lang
        lines: list[str] = []
        many = False
        for slot, items in opts.items():
            if not items:
                continue
            head = {"payee": "opt_payees", "mandate": "opt_mandates", "card": "opt_cards",
                    "product": "opt_holdings"}[slot]
            lines.append(t(head, L))
            lines += [f"  {i}. {label}" for i, (label, _) in enumerate(items, 1)]
            many = many or len(items) > 1
        if many:
            lines.append(t("opt_hint", L))
        return "\n".join(lines)

    def _reask(self, p: Pending) -> Reply:
        """
        没听懂答案：把问题和选项再给一次。第二次还不懂就放手——继续问就是把客户
        关在里面了。规则词表永远会漏，要紧的是漏掉之后客户有没有出口。
        """
        L = self.lang
        p.reask += 1
        if p.reask >= 2:
            return Reply(self._join(t("released", L), t("unknown", L)), "ask")
        if p.kind == "slots" and p.frames:
            q = self._question(p.frames[p.asking].act, p.missing, p.options, again=True)
        else:
            q = t("reask_prefix", L) + p.question
        self._hold(p)
        return Reply(q, "ask", detail={"missing": list(p.missing)})

    def _reprompt(self, p: Pending, lead: str = "") -> Reply:
        """授权还在等：再摆一次，并说清楚要怎么回答。"""
        L = self.lang
        self._hold(p)
        if p.kind == "ratify":
            return Reply(self._join(lead, t("reprompt", L)), "envelope", envelope=p.envelope,
                         detail={"digest": p.token, "voucher": structured(p.spec, L)})
        return Reply(self._join(lead, p.question), "escalation",
                     detail={"digest": p.token, "slip_digest": p.spec.digest()})

    def _withdrawn(self, p: Pending | None) -> str:
        """一份授权因为客户说了别的事而作废：说出来，免得他以为还在等。"""
        if p is None or p.kind not in ("ratify", "confirm") or p.spec is None:
            return ""
        self.ledger.append("withdrawn", {"digest": p.token, "kind": p.kind})
        self._outcome = "withdrawn"
        goal = (p.spec.goal_en if self.lang == "en" else p.spec.goal_zh).rstrip("。.")
        return t("withdrawn", self.lang, goal=goal)

    # ------------------------------------------------------------------
    # 槽位工具 / Slot helpers
    # ------------------------------------------------------------------

    def _answer_slots(self, u: Utterance, text: str) -> dict[str, Any]:
        """这一轮说出来的所有槽位值——不管解析器把这句话判成了什么意图。"""
        slots = dict(self.nlu._slots(text[:MAX_TEXT]))
        for f in u.frames:
            for k, v in f.slots.items():
                slots.setdefault(k, v)
        for k, v in u.slots.items():
            slots.setdefault(k, v)
        return slots

    @staticmethod
    def _present(slots: dict[str, Any], group: str) -> bool:
        if group == "payee":
            return bool(slots.get("payee"))
        if group == "mandate":
            return bool(slots.get("mandate") or slots.get("mandates") or slots.get("scope_all"))
        if group == "card":
            return bool(slots.get("card") or slots.get("cards") or slots.get("scope_all"))
        return bool(slots.get(group))

    def _useful(self, slots: dict[str, Any], missing: list[str], text: str) -> dict[str, Any]:
        out: dict[str, Any] = {}
        low = text.lower()
        for need in missing:
            if need == "amount" and slots.get("amount"):
                out["amount"] = slots["amount"]
            elif need == "payee" and slots.get("payee"):
                out["payee"] = slots["payee"]
                if slots.get("payee_account"):
                    out["payee_account"] = slots["payee_account"]
            elif need == "mandate":
                if slots.get("mandate"):
                    out["mandate"] = slots["mandate"]
                    if slots.get("mandates"):
                        out["mandates"] = slots["mandates"]
                elif _ALL_ANSWER.search(low):
                    out["scope_all"] = True
            elif need == "card":
                if slots.get("card"):
                    out["card"] = slots["card"]
                    if slots.get("cards"):
                        out["cards"] = slots["cards"]
                elif _ALL_ANSWER.search(low):
                    out["scope_all"] = True
            elif need == "product" and slots.get("product"):
                out["product"] = slots["product"]
        return out

    @staticmethod
    def _pick(p: Pending, ordinal: int, text: str) -> dict[str, Any] | None:
        for slot in p.missing:
            opts = p.options.get(slot) or []
            if not opts:
                continue
            idx = ordinal - 1 if ordinal > 0 else len(opts) - 1
            if not 0 <= idx < len(opts):
                return None
            value = opts[idx][1]
            return {"payee": {"payee": "literal", "payee_account": value},
                    "mandate": {"mandate": value}, "card": {"card": value},
                    "product": {"product": value}}.get(slot)
        if "amount" in p.missing and text.strip().isdigit():
            return {"amount": yuan(text.strip())}
        return None

    def _apply(self, frame: Frame, useful: dict[str, Any]) -> None:
        for group in _GROUPS:
            if self._present(useful, group):
                for key in _GROUPS[group]:
                    frame.slots.pop(key, None)
        frame.slots.update(useful)
        if "amount" in useful:
            frame.flags.discard("amount_invalid")
        frame.missing = self.nlu._missing(frame.act, frame.slots)

    @staticmethod
    def _amendable(slots: dict[str, Any]) -> dict[str, Any]:
        keep = {"amount", "payee", "payee_account", "card", "cards", "mandate", "mandates",
                "product", "risk", "limit_which"}
        return {k: v for k, v in slots.items() if k in keep and v}

    # ------------------------------------------------------------------

    def _hold(self, p: Pending) -> None:
        p.turn = self.turn
        self.pending = p

    @staticmethod
    def _join(*blocks: str) -> str:
        out: list[str] = []
        for b in blocks:
            b = (b or "").strip()
            if b and b not in out:
                out.append(b)
        return "\n\n".join(out)


_CTL = {"ctl_daily_limit", "ctl_single_limit", "ctl_online_payment"}
