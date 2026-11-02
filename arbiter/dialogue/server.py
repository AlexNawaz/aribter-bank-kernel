"""
离线 Web 聊天界面 / Offline web chat interface.

只用 Python 标准库，没有任何第三方依赖，页面也不加载任何外部资源——评委可能在没有
网络的环境下打开它。整个界面就是 `python -m arbiter.cli serve`。

左边是对话，右边是内核对这一轮的裁决：裁决结果、权限等级、触发的发现、不可信内容
检测、审计哈希链。把两者并排放是刻意的：评委能直接看到「这句话」和「内核因此做了
什么判断」之间的对应关系。授权信封画成一张银行凭证——它的结局（已执行、已拒绝、
已作废）盖在它自己身上，而不是让客户对照上下文去猜。

服务本身也按一个面向外部的接口来写：请求体有上限、JSON 解析失败返回 400、会话数
有上限、页面带 CSP，界面上所有来自服务端的文字都用 textContent 写入，不拼 HTML。
"""

from __future__ import annotations

import json
import re
import sys
import threading
import traceback
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from arbiter.bank.world import (
    INJECT_MEMO_TRANSFER,
    build_world,
    with_lookalike_payee,
    with_memo_injection,
)
from arbiter.dialogue.session import Session

MAX_BODY = 64 * 1024          # 一轮输入不会超过这个大小
MAX_TEXT = 2000
MAX_SESSIONS = 200
_SID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

_SESSIONS: "OrderedDict[str, tuple[Session, str]]" = OrderedDict()
_LOCK = threading.Lock()


def _new_session(mode: str) -> Session:
    """
    按演示模式构造世界。

    `attacked` 让评委看到真实情况：账户里躺着一笔 0.01 元的转入，附言伪装成银行通知；
    收款人名单里混进了一个与房东账号显示位一致的账号。客户什么也没做错，而这两样东西
    都已经在账户里了。
    """
    if mode == "attacked":
        return Session(core=with_lookalike_payee(with_memo_injection(build_world(), INJECT_MEMO_TRANSFER)))
    return Session(core=build_world())


def _session(sid: str, mode: str, fresh: bool = False) -> Session:
    with _LOCK:
        entry = _SESSIONS.get(sid)
        if entry is None or entry[1] != mode or fresh:
            entry = (_new_session(mode), mode)
            _SESSIONS[sid] = entry
        _SESSIONS.move_to_end(sid)
        while len(_SESSIONS) > MAX_SESSIONS:
            _SESSIONS.popitem(last=False)
        return entry[0]


def _kernel_view(session: Session, before: Any = None) -> dict[str, Any]:
    """右栏：内核最近一次裁决做了什么 / what the kernel last decided."""
    entries = session.ledger.entries
    ok, _ = session.ledger.verify()
    chain = [{"seq": e.seq, "kind": e.kind, "hash": e.entry_hash[:12], "prev": e.prev_hash[:12]}
             for e in entries[-6:]]
    report = session.last_report
    base = {"ledger": len(entries), "chain": chain, "chain_ok": ok}
    if report is None:
        return {"state": "idle", **base}
    d = report.decision
    return {
        "state": "decided",
        "fresh": report is not before,
        "verdict": d.verdict.value,
        "tier": int(d.tier.value),
        "elapsed_ms": round(d.elapsed_ms, 2),
        "findings": [{"code": f.code, "severity": f.severity,
                      "message_zh": f.message_zh, "message_en": f.message_en}
                     for f in d.findings if f.severity != "info"],
        "taint": [{"origin": t.get("origin", ""), "score": t.get("score", 0),
                   "severity": t.get("severity", ""), "signals": list(t.get("signals", []))}
                  for t in report.taint_reports if t.get("score", 0) > 0],
        **base,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "ARBITER"
    sys_version = ""

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        if ctype.startswith("text/html"):
            self.send_header("Content-Security-Policy",
                             "default-src 'none'; script-src 'unsafe-inline'; "
                             "style-src 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; "
                             "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: dict[str, Any]) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/api/health":
            self._json(200, {"ok": True})
        else:
            self._json(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path not in ("/api/chat", "/api/reset"):
            self._json(404, {"error": "not_found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._json(400, {"error": "bad_length"})
            return
        if length < 0 or length > MAX_BODY:
            self._json(413, {"error": "too_large"})
            return
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(payload, dict):
                raise ValueError("payload must be an object")
        except (ValueError, UnicodeDecodeError):
            self._json(400, {"error": "bad_json"})
            return

        sid = str(payload.get("session", "default"))
        if not _SID.match(sid):
            self._json(400, {"error": "bad_session"})
            return
        mode = "attacked" if payload.get("mode") == "attacked" else "normal"
        lang = payload.get("lang") if payload.get("lang") in ("zh", "en") else None

        try:
            if path == "/api/reset" or payload.get("reset"):
                session = _session(sid, mode, fresh=True)
                if lang:
                    session.lang = lang
                self._json(200, {"ok": True, "greeting": session.greet(),
                                 "kernel": _kernel_view(session)})
                return
            text = str(payload.get("text", ""))[:MAX_TEXT]
            raw_digest = payload.get("digest")
            # 带了摘要却格式不对：当作一个过期的按钮，而不是当作没带——
            # 后者会让一句「确认」绕过「必须确认屏幕上那一份」的检查。
            digest = None if raw_digest is None else str(raw_digest)[:64]
            session = _session(sid, mode)
            before = session.last_report
            reply = session.say(text, digest=digest, lang=lang)
            self._json(200, {"reply": reply.to_dict(), "kernel": _kernel_view(session, before)})
        except Exception:  # noqa: BLE001 — the page gets an error bubble, the console the trace
            traceback.print_exc(file=sys.stderr)
            self._json(500, {"error": "internal"})

    def log_message(self, *args: Any) -> None:      # keep the console clean
        return


def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    server = ThreadingHTTPServer((host, port), Handler)
    print("\n  ARBITER 银行 AI 智能体 · 对话界面 / chat interface")
    print(f"  在浏览器打开 / open in a browser:  http://{host}:{port}")
    print("  Ctrl-C 停止 / to stop\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  已停止 / stopped.")
        server.shutdown()


PAGE = r"""<!doctype html>
<html lang="zh-CN"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ARBITER 银行 AI 智能体</title>
<style>
:root{
  --paper:#EEF2EE; --paper-2:#E4EBE6; --sheet:#FBFCFA; --ink:#1C2A33; --ink-2:#4B5B64; --ink-3:#7A8890;
  --rule:#C9D5CF; --print:#2F5D7C; --print-soft:#D9E5EC; --seal:#C23B22; --amber:#A86A12;
  --amber-soft:#F6ECD9; --teal:#1F6F5F;
  --serif:"Noto Serif SC","Source Han Serif SC","Songti SC","STSong","SimSun",Georgia,serif;
  --sans:"PingFang SC","Hiragino Sans GB","Microsoft YaHei","Noto Sans SC",system-ui,-apple-system,"Segoe UI",sans-serif;
  --mono:ui-monospace,"SFMono-Regular",Menlo,Consolas,"Liberation Mono",monospace;
}
*{box-sizing:border-box}
html,body{height:100%}
body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.65 var(--sans);
     font-variant-numeric:tabular-nums;-webkit-font-smoothing:antialiased}
button,input{font:inherit;color:inherit}
:focus-visible{outline:2px solid var(--print);outline-offset:2px}

header{display:flex;align-items:center;gap:14px 18px;padding:11px 22px;border-bottom:1px solid var(--rule);flex-wrap:wrap}
.brand{display:flex;align-items:baseline;gap:10px;margin-right:6px}
.brand b{font-family:var(--serif);font-size:22px;letter-spacing:.08em}
.brand span{color:var(--ink-2);font-size:13px}
.who{color:var(--ink-2);font-size:13px}
.grow{flex:1}
.seg{display:inline-flex;border:1px solid var(--rule);border-radius:999px;padding:2px;background:var(--sheet)}
.seg button{border:0;background:transparent;padding:3px 13px;border-radius:999px;font-size:13px;color:var(--ink-2);cursor:pointer}
.seg button[aria-pressed="true"]{background:var(--ink);color:#fff}
.seg button.hot[aria-pressed="true"]{background:var(--seal)}
.quiet{border:1px solid var(--rule);background:var(--sheet);border-radius:999px;padding:3px 14px;font-size:13px;color:var(--ink-2);cursor:pointer}

main{display:grid;grid-template-columns:minmax(0,1fr) 384px;height:calc(100vh - 55px)}
.chat{display:flex;flex-direction:column;min-width:0;min-height:0}
.log{flex:1;overflow-y:auto;padding:26px clamp(16px,4vw,52px) 14px;display:flex;flex-direction:column;gap:14px}
.log>*{flex-shrink:0}
.me{align-self:flex-end;background:var(--ink);color:#fff;padding:9px 15px;border-radius:16px 16px 4px 16px;
    max-width:min(560px,86%);white-space:pre-wrap;word-break:break-word}
.bot{align-self:flex-start;max-width:min(700px,100%);white-space:pre-wrap;word-break:break-word;
     padding:1px 0 1px 15px;border-left:2px solid var(--rule)}
.bot.ask{border-left-color:var(--print)}
.bot.result{border-left-color:var(--teal)}
.bot.refusal{border-left-color:var(--seal)}
.bot.escalation{border-left-color:var(--amber);background:var(--amber-soft);padding:10px 14px 12px 15px;border-radius:0 8px 8px 0}
.bot.error{border-left-color:var(--seal);color:var(--seal)}
.acts{display:flex;gap:10px;margin-top:12px;flex-wrap:wrap;white-space:normal}
.btn{border:0;border-radius:7px;padding:7px 18px;cursor:pointer;font-size:14px}
.btn.go{background:var(--ink);color:#fff}
.btn.stop{background:transparent;border:1px solid var(--rule);color:var(--ink-2)}
.btn:disabled{opacity:.4;cursor:default}

/* 授权凭证 / the authorisation slip */
.slip{position:relative;align-self:flex-start;width:min(660px,100%);margin:2px 0 4px 17px;background:var(--sheet);
      border:1px solid var(--print);box-shadow:0 0 0 4px var(--sheet),0 0 0 5px var(--print-soft);
      padding:18px 24px 16px;white-space:normal;overflow:hidden}
.slip h3{margin:0 0 14px;text-align:center;font-family:var(--serif);font-weight:700;font-size:19px;
         letter-spacing:.42em;color:var(--print)}
.slip.en h3{letter-spacing:.04em}
.slip dl{display:grid;grid-template-columns:max-content 1fr;gap:5px 18px;margin:0}
.slip dt{color:var(--print);font-size:13px;padding-top:1px}
.slip dd{margin:0;font-size:14px}
.slip dd.goal{font-weight:600;font-size:15px}
.slip dd small{color:var(--ink-2)}
.slip h4{margin:14px 0 6px;font-size:13px;font-weight:600;color:var(--print)}
.slip ul{margin:0;padding:0;list-style:none}
.slip li{position:relative;padding-left:16px;font-size:14px}
.slip li::before{content:"";position:absolute;left:3px;top:.68em;width:5px;height:5px;background:var(--print)}
.slip ul.risks li{color:#5C4110}
.slip ul.risks li::before{background:var(--amber);border-radius:50%}
.slip .fine{margin:12px 0 0;font-size:12px;color:var(--ink-2)}
.slip .foot{display:flex;align-items:center;gap:10px;margin-top:14px;padding-top:12px;border-top:1px dashed var(--rule);flex-wrap:wrap}
.slip .digest{margin-left:auto;font-family:var(--mono);font-size:11px;color:var(--ink-3);word-break:break-all;max-width:100%}
.seal{position:absolute;right:30px;bottom:34px;width:108px;height:108px;border-radius:50%;border:3px solid currentColor;
      display:grid;place-items:center;transform:rotate(-14deg);font-family:var(--serif);font-weight:700;
      font-size:21px;letter-spacing:.14em;text-align:center;line-height:1.1;opacity:.88;pointer-events:none;
      mix-blend-mode:multiply;background:transparent}
.seal::after{content:"";position:absolute;inset:5px;border-radius:50%;border:1px solid currentColor}
.slip.en .seal{font-size:13px;letter-spacing:.06em}
.seal.executed,.seal.refused{color:var(--seal)}
.seal.escalated{color:var(--amber)}
.seal.cancelled,.seal.withdrawn,.seal.replaced,.seal.expired{color:#86939A}
.seal.new{animation:stamp .26s cubic-bezier(.2,.9,.3,1.25)}
@keyframes stamp{from{transform:rotate(-14deg) scale(1.7);opacity:0}to{transform:rotate(-14deg) scale(1);opacity:.88}}
@media (prefers-reduced-motion:reduce){.seal.new{animation:none}}
.slip.done{border-color:var(--rule);box-shadow:0 0 0 4px var(--sheet),0 0 0 5px var(--paper-2)}

.hints{display:flex;gap:7px;flex-wrap:wrap;align-items:center;padding:4px clamp(16px,4vw,52px) 10px}
.hints em{font-style:normal;font-size:12px;color:var(--ink-3);margin-right:2px}
.hints button{border:1px solid var(--rule);background:var(--sheet);border-radius:999px;padding:3px 12px;
              font-size:13px;color:var(--ink-2);cursor:pointer}
.hints button:hover{border-color:var(--print);color:var(--ink)}
form{display:flex;gap:10px;padding:12px clamp(16px,4vw,52px) 16px;border-top:1px solid var(--rule)}
form input{flex:1;min-width:0;background:var(--sheet);border:1px solid var(--rule);border-radius:9px;padding:10px 14px}
form input:focus{border-color:var(--print);outline:none;box-shadow:0 0 0 3px var(--print-soft)}
form button{background:var(--ink);color:#fff;border:0;border-radius:9px;padding:10px 22px;cursor:pointer}
form button:disabled,form input:disabled{opacity:.55}

/* 内核裁决 / kernel panel */
.side{border-left:1px solid var(--rule);background:var(--paper-2);overflow-y:auto;padding:20px 22px 28px}
.side h2{margin:0 0 14px;font-family:var(--serif);font-size:17px;font-weight:700}
.side h3{margin:20px 0 8px;font-size:13px;font-weight:600;color:var(--print)}
.verdict{font-family:var(--serif);font-size:34px;font-weight:700;line-height:1.1}
.verdict.allow{color:var(--teal)} .verdict.require_approval{color:var(--amber)} .verdict.deny{color:var(--seal)}
.stat{font-size:13px;color:var(--ink-2);margin-top:6px}
.stale{font-size:12px;color:var(--ink-3);margin-top:6px}
.ladder{display:grid;grid-template-columns:repeat(4,1fr);gap:4px;margin-top:14px}
.ladder div{border:1px solid var(--rule);background:var(--sheet);border-radius:6px;padding:6px 6px 5px;text-align:center}
.ladder b{display:block;font-size:13px}
.ladder span{display:block;font-size:11px;color:var(--ink-3)}
.ladder div.on{background:var(--ink);border-color:var(--ink);color:#fff}
.ladder div.on span{color:#C9D3D8}
.find{border-left:3px solid var(--rule);background:var(--sheet);padding:7px 10px;margin-bottom:7px;border-radius:0 6px 6px 0}
.find.critical{border-left-color:var(--seal)} .find.high{border-left-color:var(--amber)}
.find.medium{border-left-color:var(--print)}
.find code{display:block;font-family:var(--mono);font-size:10.5px;color:var(--ink-3)}
.find p{margin:2px 0 0;font-size:13px;line-height:1.5}
.bar{height:5px;background:var(--rule);border-radius:3px;margin:5px 0 2px;overflow:hidden}
.bar i{display:block;height:100%;background:var(--seal)}
.chain{margin:0;padding:0;list-style:none;font-size:12px}
.chain li{display:grid;grid-template-columns:auto 1fr;gap:0 8px;padding:5px 0;border-bottom:1px dashed var(--rule)}
.chain li:last-child{border-bottom:0}
.chain .k{color:var(--ink)}
.chain .h{grid-column:2;font-family:var(--mono);font-size:10.5px;color:var(--ink-3)}
.empty{font-size:13px;color:var(--ink-3)}
.note{margin-top:22px;font-size:12px;color:var(--ink-3)}
.ok{color:var(--teal)} .broken{color:var(--seal)}

@media (max-width:920px){
  main{grid-template-columns:1fr;height:auto}
  .log{min-height:58vh;max-height:none}
  .side{border-left:0;border-top:1px solid var(--rule)}
}
</style></head><body>

<header>
  <div class="brand"><b>ARBITER</b><span data-i18n="tagline"></span></div>
  <span class="who" data-i18n="customer"></span>
  <span class="grow"></span>
  <div class="seg" role="group" id="modes">
    <button type="button" id="m-normal" aria-pressed="true" data-i18n="mode_normal"></button>
    <button type="button" id="m-attacked" class="hot" aria-pressed="false" data-i18n="mode_attacked"></button>
  </div>
  <div class="seg" role="group" aria-label="Language">
    <button type="button" id="l-zh" aria-pressed="true">中</button>
    <button type="button" id="l-en" aria-pressed="false">EN</button>
  </div>
  <button type="button" class="quiet" id="reset" data-i18n="reset"></button>
</header>

<main>
  <section class="chat">
    <div class="log" id="log" aria-live="polite"></div>
    <div class="hints" id="hints"></div>
    <form id="composer" autocomplete="off">
      <input id="box" maxlength="500">
      <button type="submit" id="send" data-i18n="send"></button>
    </form>
  </section>
  <aside class="side">
    <h2 data-i18n="kernel_title"></h2>
    <div id="kernel"></div>
    <p class="note" data-i18n="kernel_note"></p>
  </aside>
</main>

<script>
"use strict";
const T = {
  zh: {
    tagline: "银行 AI 智能体", customer: "客户 张伟（C8829301）", mode_normal: "正常账户",
    mode_attacked: "被攻击的账户", reset: "重新开始", send: "发送",
    placeholder: "直接说您想做什么，例如「给房东转 4500」", hints: "试试这些说法",
    kernel_title: "内核裁决",
    kernel_note: "这一栏是内核的结构化裁决。内核只读影子执行观测到的效果，不读对话里的任何文字。",
    idle: "还没有需要裁决的操作。", stale: "本轮没有新的裁决，这里显示的是上一次。",
    verdict: {allow: "放行", require_approval: "需确认", deny: "拒绝"},
    tier_line: (t, ms) => `权限等级 T${t}，裁决用时 ${ms} ms`,
    tiers: [["T0","只读"],["T1","限额内"],["T2","需确认"],["T3","高危"]],
    findings: "触发的发现", none_findings: "本次裁决没有触发需要关注的发现。",
    taint: "不可信内容检测", chain: "审计哈希链",
    chain_ok: (n) => `共 ${n} 条，哈希链完整`, chain_bad: (n) => `共 ${n} 条，哈希链已损坏`,
    origin: {txn_memo: "交易附言", merchant_name: "商户名称", payee_alias: "收款人备注", product_blurb: "产品文案"},
    score: (s) => `评分 ${s}`,
    signal: {concealment:"要求隐瞒", embeds_account_number:"夹带账号", fake_system_frame:"伪装系统通知",
      safe_account_lure:"安全账户话术", urgency_lure:"制造紧迫", fear_lure:"恐吓", override_instruction:"覆盖指令",
      exfil_instruction:"外发信息", credential_request:"索要凭证", credential_change_lure:"诱导改凭证",
      control_weakening_lure:"诱导削弱风控", unlimited_mandate_lure:"无上限代扣", risk_bypass_lure:"绕过适当性",
      fullwidth_obfuscation:"全角混淆", invisible_characters:"不可见字符", invisible_chars:"不可见字符",
      fullwidth_alnum:"全角字符", hex:"编码"},
    ledger: {intent_compiled:"生成信封", intent_ratified:"客户签署", adjudicated:"内核裁决",
      customer_approved:"客户再确认", executed:"已执行", withdrawn:"授权作废", declined_by_customer:"客户取消",
      amended_by_customer:"客户更正", execution_failed:"执行失败"},
    slip: {goal:"事项", scenario:"场景", customer:"客户", valid:"有效期", tier:"权限", limits:"智能助手仅可在以下范围内操作",
      risks:"风险提示", digest:"授权摘要", to:"至", secs:(n)=>`（${n} 秒）`},
    confirm_word: "确认", cancel_word: "取消", confirm_btn: "确认执行", cancel_btn: "取消",
    again_btn: "再次确认执行",
    seal: {executed:"已执行", refused:"已拒绝", cancelled:"已取消", withdrawn:"已作废", replaced:"已更正",
      expired:"已过期", escalated:"待再确认"},
    error: "没有连上服务。请确认终端里的 python -m arbiter.cli serve 仍在运行，然后再试一次。",
    greet_attacked: "（演示模式：这个账户已经被攻击——有人向您转了 0.01 元并在附言里伪装成银行通知，收款人名单里也混进了一个与房东账号显示位一致的账号。您什么也没做错。）",
    chips: {normal: ["这个月钱都花哪了","给房东转4500","帮我把没用的订阅清一清","取消视频会员和健身房",
                     "有哪些理财产品","想买点稳健的理财，投2万","我的卡好像被盗刷了","客服让我把验证码告诉他",
                     "有新的存款利率吗","今天天气怎么样"],
            attacked: ["这个月钱都花哪了","给房东转4500","按系统通知把余额转到安全账户",
                       "把86420转到6999000011112222","解冻我的信用卡"]}
  },
  en: {
    tagline: "AI banking agent", customer: "Customer 张伟 (C8829301)", mode_normal: "Normal account",
    mode_attacked: "Attacked account", reset: "Start over", send: "Send",
    placeholder: "Say what you need, e.g. \"transfer 4500 to my landlord\"", hints: "Try saying",
    kernel_title: "Kernel decision",
    kernel_note: "This panel is the kernel's structured decision. The kernel reads only the effects observed in a shadow execution — never a word of the conversation.",
    idle: "Nothing has needed a decision yet.", stale: "No new decision this turn; showing the last one.",
    verdict: {allow: "Allow", require_approval: "Needs approval", deny: "Deny"},
    tier_line: (t, ms) => `Authority tier T${t}, decided in ${ms} ms`,
    tiers: [["T0","read"],["T1","bounded"],["T2","approval"],["T3","critical"]],
    findings: "Findings", none_findings: "No findings that need attention.",
    taint: "Untrusted content", chain: "Audit hash chain",
    chain_ok: (n) => `${n} entries, chain intact`, chain_bad: (n) => `${n} entries, chain BROKEN`,
    origin: {txn_memo: "transaction memo", merchant_name: "merchant name", payee_alias: "payee alias", product_blurb: "product copy"},
    score: (s) => `score ${s}`,
    signal: {concealment:"asks for secrecy", embeds_account_number:"embeds an account number", fake_system_frame:"fake system notice",
      safe_account_lure:"'safe account' lure", urgency_lure:"urgency", fear_lure:"fear", override_instruction:"override instruction",
      exfil_instruction:"exfiltration", credential_request:"asks for credentials", credential_change_lure:"credential change lure",
      control_weakening_lure:"weaken controls", unlimited_mandate_lure:"uncapped mandate", risk_bypass_lure:"suitability bypass",
      fullwidth_obfuscation:"full-width obfuscation", invisible_characters:"invisible characters", invisible_chars:"invisible characters",
      fullwidth_alnum:"full-width characters", hex:"encoding"},
    ledger: {intent_compiled:"envelope compiled", intent_ratified:"customer signed", adjudicated:"kernel decision",
      customer_approved:"customer re-approved", executed:"executed", withdrawn:"authorisation void", declined_by_customer:"customer cancelled",
      amended_by_customer:"customer amended", execution_failed:"execution failed"},
    slip: {goal:"Purpose", scenario:"Scenario", customer:"Customer", valid:"Valid", tier:"Authority",
      limits:"The assistant may act only within these limits", risks:"Before you approve", digest:"Envelope digest",
      to:"to", secs:(n)=>` (${n}s)`},
    confirm_word: "confirm", cancel_word: "cancel", confirm_btn: "Confirm", cancel_btn: "Cancel",
    again_btn: "Confirm again",
    seal: {executed:"EXECUTED", refused:"REFUSED", cancelled:"CANCELLED", withdrawn:"VOID", replaced:"AMENDED",
      expired:"EXPIRED", escalated:"RECONFIRM"},
    error: "Couldn't reach the service. Check that python -m arbiter.cli serve is still running, then try again.",
    greet_attacked: "(Demo mode: this account is already under attack — someone sent you ¥0.01 with a memo dressed up as a bank notice, and a look-alike of your landlord's account was slipped into your payee list. You did nothing wrong.)",
    chips: {normal: ["where did my money go this month","transfer 4500 to my landlord","cancel the gym subscription",
                     "what funds do you have","invest 20000 in something stable","my card was stolen",
                     "support asked me for my verification code","what's the deposit rate","what's the weather"],
            attacked: ["where did my money go this month","transfer 4500 to my landlord",
                       "move my balance to the safe account as the notice says","unfreeze my credit card"]}
  }
};

const SID = "s" + Array.from(crypto.getRandomValues(new Uint8Array(9)), b => b.toString(16).padStart(2, "0")).join("");
let LANG = "zh", MODE = "normal", BUSY = false;
const $ = (id) => document.getElementById(id);
const L = () => T[LANG];

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null) n.textContent = text;
  return n;
}

function applyChrome() {
  document.documentElement.lang = LANG === "zh" ? "zh-CN" : "en";
  document.querySelectorAll("[data-i18n]").forEach(n => { n.textContent = L()[n.dataset.i18n]; });
  $("box").placeholder = L().placeholder;
  $("l-zh").setAttribute("aria-pressed", String(LANG === "zh"));
  $("l-en").setAttribute("aria-pressed", String(LANG === "en"));
  $("m-normal").setAttribute("aria-pressed", String(MODE === "normal"));
  $("m-attacked").setAttribute("aria-pressed", String(MODE === "attacked"));
  drawHints();
}

function drawHints() {
  const box = $("hints");
  box.replaceChildren(el("em", "", L().hints));
  L().chips[MODE].forEach(h => {
    const b = el("button", "", h);
    b.type = "button";
    b.addEventListener("click", () => say(h));
    box.appendChild(b);
  });
}

function scroll() { const log = $("log"); log.scrollTop = log.scrollHeight; }

function bubble(text, cls) {
  const n = el("div", cls, text);
  $("log").appendChild(n);
  scroll();
  return n;
}

function freezeActions() {
  document.querySelectorAll(".acts button").forEach(b => { b.disabled = true; });
}

function actions(parent, digest, again) {
  const row = el("div", "acts");
  const yes = el("button", "btn go", again ? L().again_btn : L().confirm_btn);
  const no = el("button", "btn stop", L().cancel_btn);
  yes.type = no.type = "button";
  yes.addEventListener("click", () => say(L().confirm_word, digest));
  no.addEventListener("click", () => say(L().cancel_word, digest));
  row.append(yes, no);
  parent.appendChild(row);
  return row;
}

function slipNode(digest) {
  return Array.from(document.querySelectorAll(".slip")).find(n => n.dataset.digest === digest);
}

function drawSlip(v, token) {
  const s = L().slip;
  const node = el("div", "slip" + (LANG === "en" ? " en" : ""));
  node.dataset.digest = v.digest;
  node.appendChild(el("h3", "", v.title));
  const dl = el("dl");
  const row = (k, val, cls) => { dl.appendChild(el("dt", "", k)); dl.appendChild(el("dd", cls || "", val)); };
  row(s.goal, v.goal, "goal");
  row(s.scenario, v.scenario);
  row(s.customer, v.customer);
  row(s.valid, `${v.valid_from} ${s.to} ${v.valid_to}${s.secs(v.ttl)}`);
  const dd = el("dd"); dd.append(`${v.tier_code} ${v.tier} `, el("small", "", v.tier_note));
  dl.appendChild(el("dt", "", s.tier)); dl.appendChild(dd);
  node.appendChild(dl);
  node.appendChild(el("h4", "", s.limits));
  const ul = el("ul"); v.limits.forEach(x => ul.appendChild(el("li", "", x))); node.appendChild(ul);
  if (v.risks && v.risks.length) {
    node.appendChild(el("h4", "", s.risks));
    const r = el("ul", "risks"); v.risks.forEach(x => r.appendChild(el("li", "", x))); node.appendChild(r);
  }
  node.appendChild(el("p", "fine", v.footer));
  const foot = el("div", "foot");
  actions(foot, token, false);
  foot.appendChild(el("span", "digest", `${s.digest} ${v.digest}`));
  node.appendChild(foot);
  $("log").appendChild(node);
  scroll();
}

function stamp(slip) {
  if (!slip || !slip.digest) return;
  const node = slipNode(slip.digest);
  if (!node) return;
  if (slip.state === "open") return;
  let seal = node.querySelector(".seal");
  if (!seal) { seal = el("div", "seal"); node.appendChild(seal); }
  const fresh = seal.dataset.state !== slip.state;
  seal.dataset.state = slip.state;
  seal.className = "seal " + slip.state + (fresh ? " new" : "");
  seal.textContent = (node.classList.contains("en") ? T.en : T.zh).seal[slip.state] || slip.state;
  if (slip.state !== "escalated") node.classList.add("done");
  node.querySelectorAll(".acts button").forEach(b => { b.disabled = true; });
}

function render(d) {
  const r = d.reply, det = r.detail || {};
  stamp(det.slip);
  if (r.kind === "envelope") {
    bubble(r.text, "bot envelope");
    const v = det.voucher;
    const existing = v && slipNode(v.digest);
    if (existing) {
      existing.querySelectorAll(".acts button").forEach(b => { b.disabled = false; });
    } else if (v) {
      drawSlip(v, det.digest);
    } else if (r.envelope) {
      bubble(r.envelope, "bot");
    }
  } else if (r.kind === "escalation") {
    const n = bubble(r.text, "bot escalation");
    actions(n, det.digest, true);
    scroll();
  } else {
    bubble(r.text, "bot " + r.kind);
  }
  paintKernel(d.kernel);
}

function paintKernel(k) {
  const box = $("kernel");
  const K = L();
  box.replaceChildren();
  if (!k || k.state !== "decided") {
    box.appendChild(el("p", "empty", K.idle));
  } else {
    box.appendChild(el("div", "verdict " + k.verdict, K.verdict[k.verdict] || k.verdict));
    box.appendChild(el("div", "stat", K.tier_line(k.tier, k.elapsed_ms)));
    if (!k.fresh) box.appendChild(el("div", "stale", K.stale));
    const lad = el("div", "ladder");
    K.tiers.forEach(([code, name], i) => {
      const c = el("div", i === k.tier ? "on" : "");
      c.append(el("b", "", code), el("span", "", name));
      lad.appendChild(c);
    });
    box.appendChild(lad);
    box.appendChild(el("h3", "", K.findings));
    if (k.findings.length) {
      k.findings.forEach(f => {
        const n = el("div", "find " + f.severity);
        n.append(el("code", "", f.code), el("p", "", LANG === "en" ? f.message_en : f.message_zh));
        box.appendChild(n);
      });
    } else {
      box.appendChild(el("p", "empty", K.none_findings));
    }
    if (k.taint.length) {
      box.appendChild(el("h3", "", K.taint));
      k.taint.forEach(t => {
        const n = el("div", "find " + t.severity);
        n.appendChild(el("code", "", `${K.origin[t.origin] || t.origin} · ${K.score(t.score)}`));
        const bar = el("div", "bar"), fill = el("i");
        fill.style.width = Math.max(0, Math.min(100, t.score)) + "%";
        bar.appendChild(fill);
        n.appendChild(bar);
        n.appendChild(el("p", "", t.signals.map(s => K.signal[s] || s).join(LANG === "en" ? ", " : "、")));
        box.appendChild(n);
      });
    }
  }
  if (k && k.chain) {
    box.appendChild(el("h3", "", K.chain));
    box.appendChild(el("p", "stat " + (k.chain_ok ? "ok" : "broken"),
                       k.chain_ok ? K.chain_ok(k.ledger) : K.chain_bad(k.ledger)));
    const ul = el("ul", "chain");
    k.chain.slice().reverse().forEach(e => {
      const li = el("li");
      li.append(el("span", "", "#" + e.seq), el("span", "k", K.ledger[e.kind] || e.kind),
                el("span", "h", `${e.hash}…  ← ${e.prev}…`));
      ul.appendChild(li);
    });
    box.appendChild(ul);
  }
}

async function api(path, body) {
  const res = await fetch(path, {method: "POST", headers: {"Content-Type": "application/json"},
                                 body: JSON.stringify(body)});
  if (!res.ok) throw new Error("HTTP " + res.status);
  return res.json();
}

function busy(on) {
  BUSY = on;
  $("box").disabled = on;
  $("send").disabled = on;
  document.querySelectorAll(".hints button").forEach(b => { b.disabled = on; });
}

async function say(text, digest) {
  text = (text || "").trim();
  if (!text || BUSY) return;
  busy(true);
  freezeActions();
  bubble(text, "me");
  try {
    const body = {session: SID, mode: MODE, lang: LANG, text: text};
    if (digest) body.digest = digest;
    render(await api("/api/chat", body));
  } catch (e) {
    bubble(L().error, "bot error");
  } finally {
    busy(false);
    $("box").focus();
  }
}

async function reset() {
  if (BUSY) return;
  $("log").replaceChildren();
  paintKernel(null);
  busy(true);
  try {
    const d = await api("/api/reset", {session: SID, mode: MODE, lang: LANG});
    bubble(MODE === "attacked" ? d.greeting + "\n\n" + L().greet_attacked : d.greeting, "bot");
    paintKernel(d.kernel);
  } catch (e) {
    bubble(L().error, "bot error");
  } finally {
    busy(false);
  }
}

$("composer").addEventListener("submit", (e) => {
  e.preventDefault();
  const v = $("box").value;
  $("box").value = "";
  say(v);
});
$("m-normal").addEventListener("click", () => { if (MODE !== "normal") { MODE = "normal"; applyChrome(); reset(); } });
$("m-attacked").addEventListener("click", () => { if (MODE !== "attacked") { MODE = "attacked"; applyChrome(); reset(); } });
$("l-zh").addEventListener("click", () => { LANG = "zh"; applyChrome(); });
$("l-en").addEventListener("click", () => { LANG = "en"; applyChrome(); });
$("reset").addEventListener("click", reset);

applyChrome();
reset();
</script>
</body></html>
"""
