"""
信息外泄检查 / The disclosure guard (信息套取防护).

钱不动，信息也可能被带出去。一笔完全正常的房租转账，附言里写上客户的身份证号；
一个额度内的代扣，商户参考信息里夹带手机号——这些计划在金额、收款人、权限上都挑
不出毛病，而转账附言、收款人户名、代扣参考信息恰恰是会离开银行、出现在对方屏幕上
的文字。被注入的模型想套取客户信息，这是它唯一的出口：智能体本身没有发邮件、发消息
的能力，能把文字送出银行的只有这几个字段。

这里检查的就是这些文字：计划在影子执行中产生的、会被发往外部的每一段文本，逐一和
核心系统里客户的敏感标识比对，并用通用规则识别证件号、卡号、验证码、安全码、密码。
比对在归一化之后做——全角转半角、去掉空格、短横线和零宽字符——所以
「６２２５ ８８１２-3456…」这样拆开写骗不过去。

两条原则：
  * 只读效果，不读说法。模型说「附言写的是房租」不算数，附言里实际写了什么才算数。
  * 发现不复述。审计日志里记录的是「哪个字段、哪一类信息、多长」，不是信息本身——
    否则拦截记录自己就成了一次泄露。
"""

from __future__ import annotations

import re
import unicodedata

from arbiter.bank.core import BankCore
from arbiter.types import EffectSet, Finding

_ZERO_WIDTH = re.compile(r"[​-‏⁠﻿]")
_SEPARATORS = re.compile(r"[\s\-_.·•,，、/\\|:：()（）\[\]【】]+")

_ID_RE = re.compile(r"(?<![0-9])[1-9]\d{16}[0-9x](?![0-9x])")
_CARD_RE = re.compile(r"(?<!\d)\d{16,19}(?!\d)")
_OTP_RE = re.compile(r"(验证码|动态码|校验码|短信码|动态密码|otp|verificationcode|code)\D{0,6}\d{4,8}")
_CVV_RE = re.compile(r"(cvv|cvc|cvn|安全码|卡背面|背面三位)\D{0,6}\d{3,4}(?!\d)")
_PASSWORD_RE = re.compile(r"(密码|口令|password|passwd|passcode|pin)\W{0,3}[0-9a-z]{4,}")

CHANNEL_ZH = {"transfer_memo": "转账附言", "payee_name": "收款人户名",
              "mandate_reference": "代扣商户信息"}
CHANNEL_EN = {"transfer_memo": "transfer memo", "payee_name": "payee name",
              "mandate_reference": "direct-debit reference"}
KIND_ZH = {"id_number": "身份证号", "phone": "手机号", "email": "邮箱",
           "account_number": "本人账号", "card_number": "银行卡号", "otp": "验证码",
           "cvv": "卡片安全码", "password": "密码"}
KIND_EN = {"id_number": "ID number", "phone": "phone number", "email": "e-mail address",
           "account_number": "own account number", "card_number": "card number",
           "otp": "verification code", "cvv": "card security code", "password": "password"}


def _compact(text: str) -> str:
    """全角转半角、小写、去掉零宽字符和常见分隔符。"""
    text = unicodedata.normalize("NFKC", text or "")
    text = _ZERO_WIDTH.sub("", text).lower()
    return _SEPARATORS.sub("", text)


def _digits(text: str) -> str:
    return "".join(ch for ch in _compact(text) if ch.isdigit())


def _luhn(number: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(number)):
        d = int(ch)
        if i % 2 == 1:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


def classify(text: str, core: BankCore) -> list[str]:
    """一段文字里含有哪几类敏感信息 / which kinds of sensitive data a text carries."""
    st = core.state
    compact = _compact(text)
    digits = _digits(text)
    kinds: list[str] = []

    def add(kind: str) -> None:
        if kind not in kinds:
            kinds.append(kind)

    cust = st.customer
    id_number = _compact(getattr(cust, "id_number", "") or "")
    phone = _digits(getattr(cust, "phone", "") or "")
    email = _compact(getattr(cust, "email", "") or "")
    if id_number and id_number in compact:
        add("id_number")
    if len(phone) >= 7 and phone in digits:
        add("phone")
    if email and email in compact:
        add("email")
    for account in st.accounts:
        if account and account in digits:
            add("account_number")
            break

    if "id_number" not in kinds and _ID_RE.search(compact):
        add("id_number")
    if "account_number" not in kinds:
        for m in _CARD_RE.finditer(compact):
            if _luhn(m.group(0)):
                add("card_number")
                break
    if _OTP_RE.search(compact):
        add("otp")
    if _CVV_RE.search(compact):
        add("cvv")
    if _PASSWORD_RE.search(compact):
        add("password")
    return kinds


def scan(core: BankCore, effects: EffectSet) -> list[Finding]:
    """
    逐条检查会离开银行的文字 / Check every piece of text that would leave the bank.

    一条命中就是 critical：客户的证件号、账号、验证码没有任何正当理由出现在一条
    转账附言里，也不存在「客户确认一下就可以发」的情形——套取信息的诈骗，靠的恰恰
    是让客户自己点头。
    """
    findings: list[Finding] = []
    for channel, text in effects.outbound_text:
        kinds = classify(text, core)
        if not kinds:
            continue
        zh_kinds = "、".join(KIND_ZH[k] for k in kinds)
        en_kinds = ", ".join(KIND_EN[k] for k in kinds)
        findings.append(Finding(
            "KRN_DISCLOSURE", "critical",
            f"计划会把客户的敏感信息（{zh_kinds}）写进{CHANNEL_ZH.get(channel, channel)}，"
            "随操作发往银行以外",
            f"the plan writes the customer's sensitive data ({en_kinds}) into the "
            f"{CHANNEL_EN.get(channel, channel)}, which leaves the bank with the operation",
            {"channel": channel, "kinds": kinds, "chars": len(text)},
        ))
    return findings
