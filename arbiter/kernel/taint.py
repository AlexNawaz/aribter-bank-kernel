"""
注入防御：隔离与检测 / Injection defence: quarantine and inspection.

A note on what this module is, and is not.

It is **not** the primary defence. Pattern detectors lose to an attacker who
can read the patterns, and every published filter has been bypassed, usually
within days. Treating a detector as the control would reproduce exactly the
failure this project exists to fix.

The primary defence is architectural and lives in `kernel.adjudicator`:
attacker-authored content can never reach a policy decision, a tier
assignment, or an authorisation. It can only be displayed. An injection that
completely convinces the language model still changes nothing, because the
model has no authority to begin with.

What this module provides is (a) an envelope that stops untrusted text
escaping into instruction context, (b) a decoding pass that strips the
obfuscation attackers use to smuggle instructions past a model's own
judgement, and (c) a risk score that raises the tier and lands in the audit
record, so a suspicious plan reaches a human rather than being waved through.
"""

from __future__ import annotations

import base64
import binascii
import re
import secrets
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from arbiter.types import TaintOrigin

# 每进程随机分隔符 / a fresh nonce per process. Fixed delimiters are guessable,
# and a payload that guesses the delimiter can close the envelope and continue
# in instruction context.
_NONCE = secrets.token_hex(8)


def quarantine(text: str, origin: TaintOrigin) -> str:
    """Wrap untrusted text so it cannot escape into instruction context."""
    cleaned = str(text).replace(_NONCE, "[nonce-stripped]")
    return (
        f"<<<不可信内容 UNTRUSTED:{origin.value}:{_NONCE}>>>\n"
        f"{cleaned}\n"
        f"<<<结束 END:{_NONCE}>>>"
    )


# ---------------------------------------------------------------------------
# 混淆层剥离 / Obfuscation layers
# ---------------------------------------------------------------------------

_ZERO_WIDTH = {"​", "‌", "‍", "⁠", "﻿", "­"}
_BIDI = {"‪", "‫", "‬", "‭", "‮", "⁦", "⁧", "⁨", "⁩"}


def _strip_invisible(text: str) -> tuple[str, int]:
    removed = sum(text.count(ch) for ch in _ZERO_WIDTH | _BIDI)
    for ch in _ZERO_WIDTH | _BIDI:
        text = text.replace(ch, "")
    return text, removed


def _try_base64(text: str) -> str | None:
    candidate = re.sub(r"\s+", "", text)
    if len(candidate) < 16 or len(candidate) % 4:
        return None
    if not re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", candidate):
        return None
    try:
        decoded = base64.b64decode(candidate, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    printable = sum(c.isprintable() for c in decoded)
    return decoded if decoded and printable / len(decoded) > 0.85 else None


def _try_hex(text: str) -> str | None:
    candidate = re.sub(r"\s+|^0x", "", text, flags=re.I)
    if len(candidate) < 16 or len(candidate) % 2:
        return None
    if not re.fullmatch(r"[0-9a-fA-F]+", candidate):
        return None
    try:
        decoded = bytes.fromhex(candidate).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    printable = sum(c.isprintable() for c in decoded)
    return decoded if decoded and printable / len(decoded) > 0.85 else None


def _has_fullwidth_alnum(text: str) -> bool:
    """True when the text disguises digits or Latin letters as full-width forms."""
    for ch in text:
        if "\uff10" <= ch <= "\uff19":        # ０-９
            return True
        if "\uff21" <= ch <= "\uff3a" or "\uff41" <= ch <= "\uff5a":   # Ａ-Ｚ ａ-ｚ
            return True
    return False


def deobfuscate(text: str, max_depth: int = 3) -> tuple[str, list[str]]:
    """
    Peel encoding layers until the text stops changing.

    NFKC normalisation matters more in Chinese than in English: full-width
    digits (６９９９) and full-width Latin render almost identically to their
    half-width forms, so an account number written in full-width characters
    reads normally to a person and evades a naive string match.
    """
    layers: list[str] = []
    current = str(text)
    for _ in range(max_depth):
        current, invisible = _strip_invisible(current)
        if invisible:
            layers.append(f"invisible_chars({invisible})")

        normalised = unicodedata.normalize("NFKC", current)
        if normalised != current:
            # Only *alphanumeric* full-width characters count as obfuscation.
            # NFKC also folds ordinary CJK punctuation -- ：，（） and the like
            # -- which appears in virtually every legitimate Chinese memo, so
            # flagging any NFKC change at all would mark normal Chinese text as
            # an attack and make the signal useless in the language the agent
            # mostly operates in.
            if _has_fullwidth_alnum(current):
                layers.append("fullwidth_alnum")
            current = normalised

        for name, decoder in (("base64", _try_base64), ("hex", _try_hex)):
            decoded = decoder(current)
            if decoded and decoded != current:
                layers.append(name)
                current = decoded
                break
        else:
            break
    return current, layers


# ---------------------------------------------------------------------------
# 注入特征 / Injection signals
# ---------------------------------------------------------------------------

# Bilingual patterns. A Chinese retail bank's agent will be attacked in
# Chinese; an English-only detector would be decorative.
_PATTERNS: tuple[tuple[str, str, int], ...] = (
    # 指令覆盖 / instruction override
    (r"忽略(之前|上述|以上|全部|所有).{0,6}(指令|提示|规则|要求)", "override_instruction", 45),
    (r"ignore\s+(all\s+)?(previous|prior|above|earlier)\s+(instruction|prompt|rule)", "override_instruction", 45),
    (r"(无视|不要理会|撤销).{0,8}(限制|规则|风控|约束)", "override_instruction", 40),
    # 伪造系统框架 / forged system framing
    (r"[【\[]?\s*(系统|官方|银行|风控|客服)\s*(通知|指令|提示|公告|中心)\s*[】\]]?\s*[:：]?", "fake_system_frame", 40),
    (r"\b(system|developer)\s*(prompt|message|instruction|notice)\s*[:：]", "fake_system_frame", 45),
    # 资金转移 / exfiltration
    (r"(转账|转移|划转|汇款|打款).{0,20}(至|到|给).{0,6}\d{12,19}", "exfil_instruction", 55),
    (r"\b(transfer|send|forward|sweep)\b.{0,30}\d{12,19}", "exfil_instruction", 55),
    (r"(安全账户|保护账户|监管账户|安全地址)", "safe_account_lure", 50),
    # 授权诱导 / mandate and approval lures
    (r"(授权|开通|签约).{0,16}(无限|不限|无上限|全额|免密)", "unlimited_mandate_lure", 50),
    (r"\b(authorise|authorize|approve)\b.{0,24}\b(unlimited|unbounded|no limit)\b", "unlimited_mandate_lure", 50),
    # 风控削弱 / weakening controls
    (r"(解冻|解锁|提高|调高|放开).{0,12}(卡片|额度|限额|支付|权限)", "control_weakening_lure", 45),
    # 凭证窃取 / credential theft
    (r"(验证码|短信码|动态码|密码|U盾|口令)", "credential_request", 55),
    (r"(更换|修改|变更|绑定).{0,8}(手机号|预留号码|邮箱)", "credential_change_lure", 50),
    # 隐瞒 / concealment
    (r"(无需|不要|不需|请勿).{0,8}(告知|通知|提醒|提示|告诉).{0,6}(客户|用户|本人|持卡人)", "concealment", 55),
    (r"\b(do\s+not|don.t|never)\s+(tell|inform|notify|mention)\b.{0,20}\b(user|customer)\b", "concealment", 55),
    (r"(请勿|不要).{0,8}(在摘要|在汇总|在报告).{0,8}(提及|显示|体现)", "concealment", 55),
    # 紧迫感 / urgency
    (r"(立即|马上|务必|限时|逾期).{0,16}(转账|操作|处理|授权|解冻)", "urgency_lure", 30),
    (r"(账户异常|资金风险|涉嫌|冻结风险|被盗刷)", "fear_lure", 35),
    # 风险豁免 / risk-grade bypass
    (r"(风险豁免|无需.{0,4}(评估|测评)|适合所有.{0,4}(客户|风险))", "risk_bypass_lure", 50),
)

_SEVERITY_BANDS = ((80, "critical"), (55, "high"), (30, "medium"), (10, "low"))


@dataclass
class InjectionReport:
    score: int = 0
    signals: list[str] = field(default_factory=list)
    layers: list[str] = field(default_factory=list)
    decoded: str = ""
    extracted_accounts: list[str] = field(default_factory=list)

    @property
    def suspicious(self) -> bool:
        return self.score >= 30

    @property
    def severity(self) -> str:
        for threshold, name in _SEVERITY_BANDS:
            if self.score >= threshold:
                return name
        return "info"

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "severity": self.severity,
            "signals": self.signals,
            "encoding_layers": self.layers,
            "extracted_accounts": self.extracted_accounts,
        }


def inspect(text: str) -> InjectionReport:
    """Score a piece of untrusted text for injection characteristics."""
    raw = str(text)
    decoded, layers = deobfuscate(raw)
    report = InjectionReport(decoded=decoded, layers=layers)

    haystack = f"{raw}\n{decoded}"
    for pattern, name, weight in _PATTERNS:
        if re.search(pattern, haystack, re.I | re.M):
            report.score += weight
            report.signals.append(name)

    # Obfuscation is a signal in its own right. A legitimate 附言 is not
    # base64, and does not hide zero-width characters between its glyphs.
    for layer in layers:
        if layer.startswith("invisible_chars"):
            report.score += 35
            report.signals.append("invisible_characters")
        elif layer == "fullwidth_alnum":
            report.score += 25
            report.signals.append("fullwidth_obfuscation")
        elif layer in ("base64", "hex"):
            report.score += 35
            report.signals.append(f"encoded_payload_{layer}")

    report.extracted_accounts = sorted(set(re.findall(r"\d{12,19}", decoded)))
    if report.extracted_accounts and report.score > 0:
        report.score += 15
        report.signals.append("embeds_account_number")

    report.score = min(report.score, 100)
    report.signals = sorted(set(report.signals))
    return report


# ---------------------------------------------------------------------------
# 账号混淆检测 / Account-number confusability
# ---------------------------------------------------------------------------

def account_similarity(a: str, b: str) -> float:
    """
    Similarity over the digits a banking UI actually displays.

    Apps abbreviate account numbers to the first four and last four digits.
    An attacker who matches that window is visually identical in the only view
    the customer ever sees, so comparing the full string would miss precisely
    the attack that works.
    """
    a, b = str(a), str(b)
    if a == b:
        return 1.0
    if len(a) < 8 or len(b) < 8:
        return 0.0
    head = sum(x == y for x, y in zip(a[:4], b[:4]))
    tail = sum(x == y for x, y in zip(a[-4:], b[-4:]))
    return (head + tail) / 8.0


def find_lookalikes(candidate: str, known: list[str], threshold: float = 0.875) -> list[tuple[str, float]]:
    """Known accounts the candidate could be mistaken for, but is not."""
    hits = []
    for account in known:
        if str(account) == str(candidate):
            continue
        score = account_similarity(candidate, account)
        if score >= threshold:
            hits.append((account, round(score, 3)))
    return sorted(hits, key=lambda h: -h[1])
