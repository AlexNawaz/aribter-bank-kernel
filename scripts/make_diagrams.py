"""
架构图 / Architecture diagrams.

    python scripts/make_diagrams.py          写出 docs/figures/*.svg
    node scripts/render_svg.js               （可选）把 SVG 渲染成 PNG

三张图，各有中英两版：
    architecture_{zh,en}   系统架构与信任边界
    kernel_{zh,en}         内核的裁决流程与裁决规则
    dialogue_{zh,en}       一轮对话怎么走

图是手写坐标的 SVG，不依赖任何绘图库——这样改一个字只需要改这个文件。
"""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

OUT = Path(__file__).resolve().parent.parent / "docs" / "figures"

PAPER = "#EEF2EE"
SHEET = "#FBFCFA"
INK = "#1C2A33"
INK2 = "#4B5B64"
INK3 = "#7A8890"
RULE = "#C9D5CF"
PRINT = "#2F5D7C"
PRINT_SOFT = "#E3EDF3"
SEAL = "#C23B22"
AMBER = "#A86A12"
AMBER_SOFT = "#FBF3E4"
TEAL = "#1F6F5F"

SANS = "'Noto Sans CJK SC','Noto Sans SC','PingFang SC','Microsoft YaHei',sans-serif"
SERIF = "'Noto Serif CJK SC','Noto Serif SC','Songti SC','SimSun',serif"


class Canvas:
    def __init__(self, w: int, h: int):
        self.w, self.h = w, h
        self.parts: list[str] = []

    def add(self, s: str) -> None:
        self.parts.append(s)

    def rect(self, x, y, w, h, fill=SHEET, stroke=RULE, sw=1.5, rx=10, dash=None):
        d = f' stroke-dasharray="{dash}"' if dash else ""
        self.add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" '
                 f'stroke="{stroke}" stroke-width="{sw}"{d}/>')

    def text(self, x, y, s, size=16, fill=INK, weight=400, anchor="start", font=SANS, italic=False):
        style = ' font-style="italic"' if italic else ""
        self.add(f'<text x="{x}" y="{y}" font-family="{font}" font-size="{size}" fill="{fill}" '
                 f'font-weight="{weight}" text-anchor="{anchor}"{style}>{escape(s)}</text>')

    def lines(self, x, y, rows, size=15, fill=INK2, gap=None, anchor="start", weight=400):
        gap = gap or int(size * 1.5)
        for i, row in enumerate(rows):
            self.text(x, y + i * gap, row, size=size, fill=fill, anchor=anchor, weight=weight)

    def arrow(self, d, color=INK, sw=2, dash=None, label=None, lx=0, ly=0, lsize=14,
              lanchor="middle", lcolor=None, lweight=500):
        mid = {INK: "a-ink", PRINT: "a-print", AMBER: "a-amber", TEAL: "a-teal", SEAL: "a-seal",
               INK3: "a-ink3"}[color]
        dd = f' stroke-dasharray="{dash}"' if dash else ""
        self.add(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{sw}"{dd} '
                 f'marker-end="url(#{mid})"/>')
        if label:
            for i, row in enumerate(label if isinstance(label, list) else [label]):
                self.text(lx, ly + i * int(lsize * 1.4), row, size=lsize, fill=lcolor or color,
                          anchor=lanchor, weight=lweight)

    def svg(self) -> str:
        markers = "".join(
            f'<marker id="{mid}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" '
            f'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{c}"/></marker>'
            for mid, c in (("a-ink", INK), ("a-print", PRINT), ("a-amber", AMBER), ("a-teal", TEAL),
                           ("a-seal", SEAL), ("a-ink3", INK3)))
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" '
                f'viewBox="0 0 {self.w} {self.h}"><defs>{markers}</defs>'
                f'<rect width="100%" height="100%" fill="{PAPER}"/>' + "".join(self.parts) + "</svg>")


# ---------------------------------------------------------------------------
# 1 · 系统架构 / Architecture
# ---------------------------------------------------------------------------

ARCH = {
    "zh": {
        "title": "ARBITER 系统架构：模型在可信计算基之外",
        "sub": "左边可以被欺骗；右边只读结构化的执行效果。一个被完全攻陷的模型，最多只能提出一份会被拒绝的计划。",
        "customer": ["客户", "说话、读凭证、签署"],
        "untrusted": "不可信侧：可以被欺骗，也假定会被欺骗",
        "dialogue": "对话层  arbiter/dialogue",
        "d1": ["组合式意图理解", "东西 + 动作 + 否定", "+ 问句 / 祈使", "相似度只做兜底"],
        "d2": ["对话状态机", "待办只活一轮", "追问一次问清", "更正就地改信封"],
        "d3": ["计划与信封生成", "多件事签一次", "风险提示写进信封", "不做静默替换"],
        "llm": ["大模型（可选）", "实现同一个理解接口，", "整体替换，安全性质不变"],
        "taint": ["不可信内容", "交易附言、商户名、", "产品文案、收款人备注"],
        "note": "这一侧产生的任何文字，内核一个字都不读。",
        "env": ["授权信封  IntentSpec", "事项、限额、收款人、", "权限上限、风险提示、有效期", "由代码渲染 → 客户签署（Ed25519）"],
        "trusted": "可信计算基：不含模型，不读自然语言",
        "kernel": "裁决内核  arbiter/kernel",
        "steps": ["1  授权验证：签名、信封摘要", "2  静态策略：凭证变更、格式、时效", "3  实体接地：账号、产品、卡、代扣必须存在",
                  "4  影子执行：在核心状态副本上跑一遍", "5  约束校验：观测效果 vs 信封", "6  适当性：客户风险评级是硬上限",
                  "7  外泄检查：附言、户名里有没有敏感信息", "8  注入检测：只记录与升级，不放行",
                  "9  账号混淆：显示位相同的陌生账号", "10 权限分级 → 放行 / 需确认 / 拒绝"],
        "auth": ["执行授权器", "只为 ALLOW 且两份摘要", "都一致的计划签名"],
        "ledger": ["审计哈希链", "每条记录都承诺", "上一条的哈希，", "可校验、可重放"],
        "core": ["银行核心  BankCore", "影子副本：只模拟", "正式提交：只接受已授权的计划"],
        "a_nl": ["自然语言", "（中文 / 英文）"],
        "a_compile": "编译",
        "a_slip": "代码渲染的凭证",
        "a_sign": "签署",
        "a_signed": ["已签署", "的信封"],
        "a_plan": ["操作计划 OpPlan", "（只是请求，不带任何权限）"],
        "a_taint": ["只检测与记录", "不参与决策"],
        "a_ctx": "进入上下文",
        "a_allow": "放行",
        "a_exec": "执行授权",
        "a_sim": ["模拟", "观测效果"],
        "a_ask": "需确认：把理由告诉客户，再问一次；拒绝：说明原因并给出下一步",
        "a_log": "记录",
    },
    "en": {
        "title": "ARBITER architecture: the model sits outside the trusted computing base",
        "sub": "The left side can be fooled; the right side reads only structured execution effects. "
               "A fully compromised model can at most propose a plan that gets refused.",
        "customer": ["Customer", "speaks, reads the slip, signs"],
        "untrusted": "Untrusted side: can be fooled, and is assumed to be",
        "dialogue": "Dialogue layer  arbiter/dialogue",
        "d1": ["Compositional parser", "object + action + negation", "+ question / command", "similarity as fallback"],
        "d2": ["Dialogue state machine", "pending lives one turn", "ask all gaps at once", "corrections amend"],
        "d3": ["Plans and envelopes", "several things, one signature", "risk notes in the envelope", "no silent substitution"],
        "llm": ["LLM (optional)", "implements the same parser", "interface; swap it freely"],
        "taint": ["Untrusted content", "transaction memos, merchant names,", "product copy, payee aliases"],
        "note": "The kernel reads not one word of anything produced on this side.",
        "env": ["Authorisation envelope  IntentSpec", "purpose, limits, payees,", "tier ceiling, risk notes, expiry", "rendered by code → signed (Ed25519)"],
        "trusted": "Trusted computing base: no model, no prose",
        "kernel": "Adjudication kernel  arbiter/kernel",
        "steps": ["1  Ratification: signature, envelope digest", "2  Static policy: credentials, format, expiry",
                  "3  Grounding: accounts, products, cards exist", "4  Shadow execution on a copy of core state",
                  "5  Invariants: observed effects vs envelope", "6  Suitability: customer grade is a hard cap",
                  "7  Disclosure: sensitive data in memos, names", "8  Injection scan: log and escalate, never allow",
                  "9  Look-alike accounts in the payee book", "10 Tier → allow / needs approval / deny"],
        "auth": ["Execution authoriser", "signs only ALLOW plans", "whose digests both match"],
        "ledger": ["Audit hash chain", "each entry commits", "to the one before it;", "verifiable, replayable"],
        "core": ["Bank core  BankCore", "shadow copy: simulate only", "commit: authorised plans only"],
        "a_nl": ["natural language", "(Chinese / English)"],
        "a_compile": "compile",
        "a_slip": "slip rendered by code",
        "a_sign": "sign",
        "a_signed": ["signed", "envelope"],
        "a_plan": ["operation plan OpPlan", "(a request, carries no authority)"],
        "a_taint": ["inspected and logged,", "never decides"],
        "a_ctx": "into context",
        "a_allow": "allow",
        "a_exec": "authorisation",
        "a_sim": ["simulate", "observed effects"],
        "a_ask": "Needs approval: tell the customer why and ask again.  Deny: give the reason and a next step.",
        "a_log": "log",
    },
}


def architecture(lang: str) -> str:
    L = ARCH[lang]
    c = Canvas(1680, 1060)
    c.text(60, 70, L["title"], size=30, weight=700, font=SERIF)
    c.text(60, 106, L["sub"], size=17, fill=INK2)

    # 客户
    c.rect(60, 150, 250, 104, fill=SHEET, stroke=INK, sw=2)
    c.text(185, 194, L["customer"][0], size=22, weight=700, anchor="middle")
    c.text(185, 226, L["customer"][1], size=15, fill=INK2, anchor="middle")

    # 不可信侧
    c.rect(40, 330, 700, 690, fill=AMBER_SOFT, stroke=AMBER, sw=2, rx=16, dash="9 7")
    c.text(64, 366, L["untrusted"], size=18, fill=AMBER, weight=700)
    c.rect(66, 392, 648, 290, fill=SHEET, stroke=RULE)
    c.text(90, 428, L["dialogue"], size=19, weight=700)
    for i, key in enumerate(("d1", "d2", "d3")):
        x = 86 + i * 210
        c.rect(x, 452, 196, 206, fill=PAPER, stroke=RULE, rx=8)
        rows = L[key]
        c.text(x + 98, 488, rows[0], size=16, weight=700, anchor="middle")
        c.lines(x + 98, 522, rows[1:], size=14, anchor="middle", gap=26)
    c.rect(66, 706, 312, 132, fill=SHEET, stroke=RULE)
    c.text(222, 744, L["llm"][0], size=17, weight=700, anchor="middle")
    c.lines(222, 776, L["llm"][1:], size=14, anchor="middle", gap=24)
    c.rect(402, 706, 312, 132, fill="#F7E3DE", stroke=SEAL, sw=1.5)
    c.text(558, 744, L["taint"][0], size=17, weight=700, fill=SEAL, anchor="middle")
    c.lines(558, 776, L["taint"][1:], size=14, anchor="middle", gap=24)
    c.text(390, 990, L["note"], size=16, fill=AMBER, weight=600, anchor="middle")
    c.arrow("M558,706 L558,686", color=SEAL, label=L["a_ctx"], lx=572, ly=700, lsize=13, lanchor="start")

    # 授权信封
    c.rect(776, 168, 340, 190, fill=SHEET, stroke=PRINT, sw=2, rx=4)
    c.rect(784, 176, 324, 174, fill="none", stroke=PRINT_SOFT, sw=1.5, rx=2)
    c.text(946, 214, L["env"][0], size=18, weight=700, fill=PRINT, anchor="middle")
    c.lines(946, 250, L["env"][1:3], size=14, anchor="middle", gap=24)
    c.text(946, 322, L["env"][3], size=14, fill=PRINT, weight=600, anchor="middle")

    # 可信计算基
    c.rect(1190, 140, 460, 880, fill=PRINT_SOFT, stroke=PRINT, sw=2.5, rx=16)
    c.text(1214, 178, L["trusted"], size=18, fill=PRINT, weight=700)
    c.rect(1212, 198, 416, 456, fill=SHEET, stroke=PRINT, sw=1.5)
    c.text(1236, 234, L["kernel"], size=19, weight=700)
    c.lines(1236, 272, L["steps"], size=14, fill=INK, gap=37)
    c.rect(1212, 676, 200, 132, fill=SHEET, stroke=PRINT, sw=1.5)
    c.text(1312, 712, L["auth"][0], size=16, weight=700, anchor="middle")
    c.lines(1312, 742, L["auth"][1:], size=13, anchor="middle", gap=22)
    c.rect(1428, 676, 200, 132, fill=SHEET, stroke=PRINT, sw=1.5)
    c.text(1528, 712, L["ledger"][0], size=16, weight=700, anchor="middle")
    c.lines(1528, 740, L["ledger"][1:], size=13, anchor="middle", gap=21)
    c.rect(1212, 862, 416, 132, fill=SHEET, stroke=INK, sw=2)
    c.text(1420, 900, L["core"][0], size=17, weight=700, anchor="middle")
    c.lines(1420, 932, L["core"][1:], size=14, anchor="middle", gap=24)

    # 箭头
    c.arrow("M185,254 L185,388", label=L["a_nl"], lx=200, ly=300, lsize=14, lanchor="start")
    c.arrow("M714,470 C744,470 744,330 772,320", color=PRINT, label=L["a_compile"], lx=750, ly=414,
            lsize=14, lanchor="start")
    c.arrow("M776,206 L314,190", color=PRINT, label=L["a_slip"], lx=545, ly=184, lsize=14)
    c.arrow("M314,232 L776,240", color=INK, label=L["a_sign"], lx=545, ly=262, lsize=14)
    c.arrow("M1116,262 L1208,262", color=PRINT, label=L["a_signed"], lx=1153, ly=292, lsize=13)
    c.arrow("M714,600 C900,600 1000,520 1208,520", color=INK, label=L["a_plan"], lx=905, ly=640, lsize=14)
    c.arrow("M714,790 C950,790 1040,610 1208,600", color=SEAL, dash="7 6", label=L["a_taint"],
            lx=960, ly=764, lsize=13, lcolor=SEAL)
    c.arrow("M1312,654 L1312,672", color=TEAL, label=L["a_allow"], lx=1326, ly=668, lsize=13, lanchor="start")
    c.arrow("M1312,808 L1312,858", color=TEAL, label=L["a_exec"], lx=1326, ly=840, lsize=13, lanchor="start")
    c.arrow("M1412,742 L1424,742", color=INK3)
    c.arrow("M1212,236 C1000,128 560,128 314,176", color=AMBER, label=None)
    c.text(760, 132, L["a_ask"], size=14, fill=AMBER, weight=600, anchor="middle")
    return c.svg()


# ---------------------------------------------------------------------------
# 2 · 内核裁决流程 / Kernel pipeline
# ---------------------------------------------------------------------------

KERNEL = {
    "zh": {
        "title": "裁决内核：只看影子执行的实际效果，不看模型的说法",
        "sub": "每一步都只读结构化数据。任何一步出现 critical 级发现，就停在那里：默认拒绝，而不是默认放行。",
        "steps": [
            ("授权验证", "信封签名能否用客户的公钥验证；计划是否绑定到这份信封的摘要", "签名不符 → 拒绝"),
            ("静态策略", "凭证变更类操作、金额格式、有效期、计划结构", "凭证变更 → 拒绝"),
            ("实体接地（幻觉防护）", "计划里的账号、产品、卡片、代扣在核心系统里是否真的存在；模型陈述的余额与事实是否一致", "虚构实体 → 拒绝"),
            ("影子执行", "在核心状态的副本上把整个计划跑一遍，逐步记录每一个效果（不是只比较首尾快照）", "核心拒绝 → 拒绝"),
            ("约束校验", "观测到的效果对照信封：累计与单笔支出、收款人名单、账户范围、代扣、卡片风控、凭证", "越权 → 拒绝"),
            ("适当性", "买入产品的风险等级对照核心系统里客户的测评结果——信封只能更严，不能更松", "超出评级 → 拒绝"),
            ("外泄检查（信息套取）", "转账附言、收款人户名、代扣参考信息里有没有身份证号、账号、手机号、验证码", "夹带敏感信息 → 拒绝"),
            ("注入检测", "不可信内容（附言、商户名、产品文案）里有没有指令式文本；只提升「要不要问客户」", "高分且有副作用 → 需确认"),
            ("账号混淆", "收款账号与常用账号显示位（前 4 后 4）相同却不是同一个", "付给冒充者 → 拒绝"),
            ("权限分级", "按观测效果定级：T0 只读、T1 限额内、T2 需确认（首次付款、新代扣）、T3 高危（削弱风控）", "超过信封上限 → 拒绝"),
        ],
        "rule_title": "裁决规则",
        "rules": [("任何 critical 级发现", "拒绝", SEAL), ("所需权限 > 信封上限", "拒绝", SEAL),
                  ("所需权限 > T1，或有 high 级发现", "需确认", AMBER), ("其余", "放行", TEAL)],
        "tiers_title": "权限分级",
        "tiers": [("T0", "只读", "查余额、看账单：不打断客户"), ("T1", "限额内", "信封内的转账、申购、取消、冻结"),
                  ("T2", "需确认", "首次付款、未约定的代扣、未约定风险等级的申购"),
                  ("T3", "高危", "解冻、提额、开线上支付；没点名的直接拒绝")],
        "after": "放行之后：执行授权器再核对一次「内核批准的计划摘要 = 待执行的计划摘要 = 已签署的信封摘要」，三者一致才签发执行授权。",
    },
    "en": {
        "title": "The kernel decides on observed effects, never on what the model says",
        "sub": "Every step reads structured data only. A critical finding at any step stops the pipeline there: "
               "the default is refusal, not permission.",
        "steps": [
            ("Ratification", "Does the envelope signature verify with the customer's key; is the plan bound to its digest", "bad signature → deny"),
            ("Static policy", "Credential-change operations, amount format, expiry, plan shape", "credential change → deny"),
            ("Grounding (hallucination)", "Do the accounts, products, cards and mandates exist in core; do stated facts match", "invented entity → deny"),
            ("Shadow execution", "Run the whole plan on a copy of core state, recording every effect step by step", "core rejects → deny"),
            ("Invariants", "Observed effects vs the envelope: totals, single payments, payees, accounts, mandates, cards", "out of scope → deny"),
            ("Suitability", "Product risk grade vs the customer's assessment in core; an envelope can tighten it, never loosen it", "above grade → deny"),
            ("Disclosure (extraction)", "ID numbers, account numbers, phone, codes inside memos, payee names, debit references", "sensitive data → deny"),
            ("Injection scan", "Instruction-like text in memos, merchant names, product copy; raises 'ask the customer' only", "high score + side effects → approval"),
            ("Look-alike accounts", "A destination sharing the displayed digits (first 4, last 4) of a known account", "pays an impostor → deny"),
            ("Tier", "From observed effects: T0 read, T1 bounded, T2 approval (first payment, new mandate), T3 critical", "above the envelope → deny"),
        ],
        "rule_title": "Verdict rule",
        "rules": [("any critical finding", "deny", SEAL), ("required tier > envelope ceiling", "deny", SEAL),
                  ("tier > T1, or a high finding", "needs approval", AMBER), ("otherwise", "allow", TEAL)],
        "tiers_title": "Authority tiers",
        "tiers": [("T0", "read", "balances, statements: never interrupts"), ("T1", "bounded", "transfers, purchases, cancellations within the envelope"),
                  ("T2", "approval", "first payment, unbounded mandate, purchase with no risk ceiling"),
                  ("T3", "critical", "unfreeze, raise limits, online payments; unnamed → deny")],
        "after": "After ALLOW, the authoriser re-checks that the approved plan digest, the plan being executed and the "
                 "signed envelope all match before it signs anything.",
    },
}


def kernel(lang: str) -> str:
    L = KERNEL[lang]
    c = Canvas(1680, 1060)
    c.text(60, 70, L["title"], size=30, weight=700, font=SERIF)
    c.text(60, 106, L["sub"], size=17, fill=INK2)
    y0, h, gap = 140, 74, 10
    for i, (name, what, stop) in enumerate(L["steps"]):
        y = y0 + i * (h + gap)
        c.rect(60, y, 1040, h, fill=SHEET, stroke=RULE, rx=8)
        c.add(f'<circle cx="98" cy="{y + h / 2}" r="20" fill="{INK}"/>')
        c.text(98, y + h / 2 + 6, str(i + 1), size=17, fill="#FFFFFF", weight=700, anchor="middle")
        c.text(136, y + 31, name, size=18, weight=700)
        c.text(136, y + 57, what, size=14, fill=INK2)
        color = SEAL if ("拒绝" in stop or "deny" in stop) else AMBER
        c.text(1086, y + 31, stop, size=14, fill=color, weight=600, anchor="end")
        if i < len(L["steps"]) - 1:
            c.arrow(f"M98,{y + h} L98,{y + h + gap}", color=INK3, sw=1.5)
    # 右栏：规则与分级
    c.rect(1140, 140, 480, 330, fill=SHEET, stroke=PRINT, sw=2, rx=10)
    c.text(1164, 180, L["rule_title"], size=20, weight=700, fill=PRINT)
    for i, (cond, verdict, color) in enumerate(L["rules"]):
        y = 222 + i * 60
        c.text(1164, y, cond, size=15, fill=INK)
        c.rect(1450, y - 24, 146, 36, fill=color, stroke=color, rx=18)
        c.text(1523, y, verdict, size=15, fill="#FFFFFF", weight=700, anchor="middle")
    c.rect(1140, 494, 480, 404, fill=SHEET, stroke=PRINT, sw=2, rx=10)
    c.text(1164, 534, L["tiers_title"], size=20, weight=700, fill=PRINT)
    for i, (code, name, desc) in enumerate(L["tiers"]):
        y = 560 + i * 82
        fill = [TEAL, PRINT, AMBER, SEAL][i]
        c.rect(1164, y, 70, 64, fill=fill, stroke=fill, rx=8)
        c.text(1199, y + 28, code, size=18, fill="#FFFFFF", weight=700, anchor="middle")
        c.text(1199, y + 51, name, size=12, fill="#FFFFFF", anchor="middle")
        words = desc
        c.text(1250, y + 38, words, size=14, fill=INK)
    # 底部说明
    c.rect(60, 990, 1560, 50, fill=PRINT_SOFT, stroke=PRINT, sw=1, rx=8)
    c.text(84, 1022, L["after"], size=15, fill=PRINT, weight=600)
    return c.svg()


# ---------------------------------------------------------------------------
# 3 · 一轮对话 / One conversation turn
# ---------------------------------------------------------------------------

DIALOGUE = {
    "zh": {
        "title": "一轮对话怎么走：一句「好」只回答紧挨着的上一个问题",
        "sub": "上一轮留下的待办在这一轮开始时被取走；只有这一轮明确接着它说话，它才继续存在。其余任何输入都让它作废，并明确告诉客户。",
        "in": ["客户说一句话", "中文 / 英文、口语、带千分位、", "照着屏幕念回卡号或「第二个」"],
        "take": ["取走上一轮的待办", "待签署的信封、待回答的追问、", "待接受的建议"],
        "q": ["这一句是在接话吗？", "确认 / 拒绝 / 更正 / 回答追问"],
        "yes": "是",
        "no": "否",
        "cont": ["接着办", "确认 → 签署并交给内核", "拒绝 → 取消，什么都不执行",
                 "更正 → 就地改信封（换金额、换收款人）", "回答 → 填槽位（编号、卡号、金额）"],
        "void": ["待办作废，并说出来", "「刚才那份授权已作废，没有执行」"],
        "parse": ["理解这一句", "分句；每句判断「东西 + 动作 + 否定 + 问句」；", "抽出金额、收款人、卡、代扣、产品"],
        "read": ["查询类", "余额、账单、目录：", "经内核只读放行，当场回答"],
        "info": ["兜底与防护", "超范围、无数据、凭证变更、", "信息套取：说清楚，不猜"],
        "act": ["动作类", "缺什么一次问清并列出编号选项；", "齐了就落到核心系统里的具体对象"],
        "env": ["生成授权信封", "几件事合成一份，签一次；", "风险提示写在信封里，改一个字签名就对不上"],
        "kern": ["签署后交给内核裁决"],
        "ok": ["放行", "执行，回执写观测到的效果"],
        "ask": ["需确认", "说清为什么，再问一次"],
        "deny": ["拒绝", "说明原因，并给一条能走的路"],
    },
    "en": {
        "title": "One conversation turn: a 'yes' answers only the question right before it",
        "sub": "Whatever the last turn left pending is taken at the start of this one. It survives only if this turn "
               "speaks to it; any other input voids it, and the customer is told so.",
        "in": ["The customer says something", "Chinese or English, colloquial, '4,500',", "a card number read off the screen, 'the second one'"],
        "take": ["Take what the last turn left", "an envelope to sign, a question to answer,", "an offer to accept"],
        "q": ["Is this turn answering it?", "confirm / deny / correct / answer"],
        "yes": "yes",
        "no": "no",
        "cont": ["Carry on", "confirm → sign, hand to the kernel", "deny → cancel; nothing executes",
                 "correct → amend the envelope in place", "answer → fill the slot (ordinal, card, amount)"],
        "void": ["Void it, and say so", "'That authorisation was withdrawn; nothing executed'"],
        "parse": ["Understand the turn", "split clauses; object + action + negation + question;", "extract amount, payee, card, mandate, product"],
        "read": ["Reads", "balance, statement, catalogue:", "read-only, answered at once"],
        "info": ["Fallbacks and guards", "out of scope, no data,", "credentials, data extraction:", "say so plainly, never guess"],
        "act": ["Actions", "ask for every missing slot at once, with numbered", "options; resolve to real objects in core"],
        "env": ["Compile the envelope", "several things, one signature; risk notes", "inside it -- change a word and the signature breaks"],
        "kern": ["Signed, then adjudicated by the kernel"],
        "ok": ["Allow", "execute; receipt from observed effects"],
        "ask": ["Needs approval", "say why, ask once more"],
        "deny": ["Deny", "give the reason and a way forward"],
    },
}


def dialogue(lang: str) -> str:
    L = DIALOGUE[lang]
    c = Canvas(1680, 1060)
    c.text(60, 70, L["title"], size=30, weight=700, font=SERIF)
    c.text(60, 106, L["sub"], size=17, fill=INK2)

    def box(x, y, w, h, rows, fill=SHEET, stroke=RULE, title_color=INK, sw=1.5):
        c.rect(x, y, w, h, fill=fill, stroke=stroke, sw=sw)
        c.text(x + w / 2, y + 36, rows[0], size=18, weight=700, anchor="middle", fill=title_color)
        c.lines(x + w / 2, y + 66, rows[1:], size=14, anchor="middle", gap=24)

    box(60, 150, 420, 130, L["in"], stroke=INK, sw=2)
    box(60, 330, 420, 130, L["take"])
    # diamond
    cx, cy = 270, 600
    c.add(f'<path d="M{cx},{cy - 90} L{cx + 210},{cy} L{cx},{cy + 90} L{cx - 210},{cy} z" fill="{SHEET}" '
          f'stroke="{PRINT}" stroke-width="2"/>')
    c.text(cx, cy - 6, L["q"][0], size=18, weight=700, anchor="middle", fill=PRINT)
    c.text(cx, cy + 22, L["q"][1], size=13, anchor="middle", fill=INK2)
    box(60, 760, 420, 200, L["cont"], fill=PRINT_SOFT, stroke=PRINT, title_color=PRINT)
    box(560, 520, 420, 130, L["void"], fill=AMBER_SOFT, stroke=AMBER, title_color=AMBER)
    box(560, 710, 420, 130, L["parse"])
    box(1060, 150, 280, 150, L["read"])
    box(1360, 150, 280, 150, L["info"])
    box(1060, 340, 580, 130, L["act"])
    box(1060, 510, 580, 130, L["env"], stroke=PRINT, sw=2, title_color=PRINT)
    box(1060, 680, 580, 80, L["kern"], fill=INK, stroke=INK, title_color="#FFFFFF")
    for i, (key, color) in enumerate((("ok", TEAL), ("ask", AMBER), ("deny", SEAL))):
        x = 1060 + i * 200
        c.rect(x, 800, 180, 130, fill=SHEET, stroke=color, sw=2)
        c.text(x + 90, 840, L[key][0], size=19, weight=700, anchor="middle", fill=color)
        words = L[key][1]
        if lang == "en" and len(words) > 24:
            split = words.rfind(" ", 0, 24)
            c.lines(x + 90, 872, [words[:split], words[split + 1:]], size=13, anchor="middle", gap=22)
        else:
            c.text(x + 90, 874, words, size=13, anchor="middle", fill=INK2)

    c.arrow("M270,280 L270,326")
    c.arrow("M270,460 L270,506")
    c.arrow("M270,690 L270,756", color=PRINT, label=L["yes"], lx=284, ly=728, lsize=15, lanchor="start")
    c.arrow("M480,600 L556,590", color=AMBER, label=L["no"], lx=516, ly=578, lsize=15)
    c.arrow("M770,650 L770,706")
    c.add(f'<path d="M980,775 L1020,775 L1020,124 L1500,124" fill="none" stroke="{INK}" stroke-width="2"/>')
    c.arrow("M1020,225 L1056,225")
    c.arrow("M1020,405 L1056,405")
    c.arrow("M1500,124 L1500,146")
    c.arrow("M1350,470 L1350,506")
    c.arrow("M1350,640 L1350,676")
    c.arrow("M1150,760 L1150,796", color=TEAL)
    c.arrow("M1350,760 L1350,796", color=AMBER)
    c.arrow("M1550,760 L1550,796", color=SEAL)
    return c.svg()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for lang in ("zh", "en"):
        (OUT / f"architecture_{lang}.svg").write_text(architecture(lang), encoding="utf-8")
        (OUT / f"kernel_{lang}.svg").write_text(kernel(lang), encoding="utf-8")
        (OUT / f"dialogue_{lang}.svg").write_text(dialogue(lang), encoding="utf-8")
    print("written:", ", ".join(sorted(p.name for p in OUT.glob("*.svg"))))


if __name__ == "__main__":
    main()
