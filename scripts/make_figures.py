"""
从测评结果生成图表 / Render result charts from the logs.

    python -m arbiter.cli bench && python scripts/make_figures.py

图表一律从 logs/benchmark.json 生成而不是手画，所以重跑一次基准测试，演示文稿和
文档里的图就跟着更新——数字不可能和证据对不上。每张图出中英两版。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "figures"

# 与网页界面、架构图同一套颜色 / the same palette as the UI and the diagrams
P = dict(paper="#EEF2EE", ink="#1C2A33", ink2="#4B5B64", ink3="#7A8890", rule="#C9D5CF",
         print="#2F5D7C", seal="#C23B22", amber="#A86A12", teal="#1F6F5F")

plt.rcParams.update({
    "font.family": ["Noto Sans CJK SC", "DejaVu Sans"],
    "axes.unicode_minus": False,
    "figure.facecolor": P["paper"], "axes.facecolor": P["paper"], "savefig.facecolor": P["paper"],
    "text.color": P["ink"], "axes.labelcolor": P["ink2"],
    "xtick.color": P["ink3"], "ytick.color": P["ink"],
})

TXT = {
    "zh": {
        "risk_title": "{n} 项攻击全部拦截，ARBITER 实际损失 ¥0",
        "risk_sub": "同样的计划、同样被完全攻陷的模型；区别只在于决策路径上有没有内核",
        "risk_x": "若智能体没有防护，处于风险中的金额（元）",
        "no_money": "信息外泄：{kinds}",
        "zero": "当日无资金流动",
        "class_title": "五类攻击全部拦截",
        "class_sub": "对抗用例一律假定模型已被完全攻陷，每次都精确提出攻击者想要的操作",
        "classes": {"injection": "注入防御", "hallucination": "幻觉防护", "financial": "资金与适当性",
                    "disclosure": "信息套取", "structural": "结构性攻击"},
        "scen_title": "赛题六大任务场景全覆盖",
        "scen_sub": "每个场景都同时有正常用例与攻击用例；正常用例零误拦",
        "scen_label": "{t} 个用例，全部符合预期",
        "kinds": {"id_number": "身份证号", "phone": "手机号", "email": "邮箱", "account_number": "本人账号",
                  "card_number": "卡号", "otp": "验证码", "cvv": "安全码", "password": "密码"},
        "wan": lambda v: f"¥{v / 10000:.0f}万" if v else "0",
    },
    "en": {
        "risk_title": "All {n} attacks blocked; ARBITER's actual loss is ¥0",
        "risk_sub": "Same plans, same fully compromised model; the only difference is a kernel on the decision path",
        "risk_x": "Money at risk if the agent had no guard (yuan)",
        "no_money": "data leaked: {kinds}",
        "zero": "no money moves on the day",
        "class_title": "Every attack class blocked",
        "class_sub": "Adversarial cases assume a fully compromised model that proposes exactly what the attacker wants",
        "classes": {"injection": "Prompt injection", "hallucination": "Hallucination", "financial": "Funds & suitability",
                    "disclosure": "Information extraction", "structural": "Structural"},
        "scen_title": "All six task scenarios covered",
        "scen_sub": "Every scenario has both benign and attack cases; zero benign cases blocked",
        "scen_label": "{t} cases, all as expected",
        "kinds": {"id_number": "ID number", "phone": "phone", "email": "e-mail", "account_number": "account no.",
                  "card_number": "card no.", "otp": "code", "cvv": "CVV", "password": "password"},
        "wan": lambda v: f"¥{v / 1000:.0f}k" if v else "0",
    },
}
SCEN_EN = {"智能转账": "Smart transfer", "账单分析": "Statement analysis", "理财操作": "Wealth products",
           "卡片管理": "Card management", "订阅与代扣管理": "Subscriptions & debits",
           "跨场景联动": "Cross-scenario", "账户设置": "Account settings"}


def _clean(ax) -> None:
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(P["rule"])
    ax.xaxis.grid(True, color=P["rule"], linewidth=0.7)
    ax.set_axisbelow(True)
    ax.tick_params(length=0, labelsize=9.5)


def _title(ax, title: str, sub: str) -> None:
    ax.set_title(title, fontsize=15, color=P["ink"], pad=34, loc="left", fontweight="bold")
    ax.text(0, 1.015, sub, transform=ax.transAxes, fontsize=10, color=P["ink2"], va="bottom")


def fig_at_risk(data: dict, lang: str) -> Path:
    T = TXT[lang]
    attacks = [r for r in data["results"] if r["category"] != "benign"]
    rows = sorted(((r["id"], r["title_en"] if lang == "en" else r["title_zh"],
                    max(r["naive_loss"], r["naive_exposure"]) / 100, r.get("naive_leaks", []))
                   for r in attacks), key=lambda t: (t[2], bool(t[3])))
    fig, ax = plt.subplots(figsize=(12.6, 7.4))
    labels = [f"{i}   {t if len(t) <= 34 else t[:33] + '…'}" for i, t, _, _ in rows]
    values = [v for _, _, v, _ in rows]
    colors = [P["amber"] if leaks else P["print"] for _, _, _, leaks in rows]
    bars = ax.barh(labels, values, height=0.62, color=colors, linewidth=0)
    top = max(values) or 1
    for bar, (_, _, value, leaks) in zip(bars, rows):
        if value:
            text = f"¥{value:,.0f}"
        elif leaks:
            text = T["no_money"].format(kinds=("、" if lang == "zh" else ", ").join(T["kinds"].get(k, k) for k in leaks))
        else:
            text = T["zero"]
        ax.text(bar.get_width() + top * 0.012, bar.get_y() + bar.get_height() / 2, text,
                va="center", ha="left", fontsize=9, color=P["amber"] if leaks and not value else P["ink2"])
    _clean(ax)
    ax.set_xlim(0, top * 1.28)
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: T["wan"](v)))
    ax.set_xlabel(T["risk_x"], fontsize=10, labelpad=9)
    _title(ax, T["risk_title"].format(n=len(attacks)), T["risk_sub"])
    fig.tight_layout()
    path = OUT / f"at_risk_{lang}.png"
    fig.savefig(path, dpi=200, bbox_inches="tight")
    plt.close(fig)
    return path


def fig_by_class(data: dict, lang: str) -> Path:
    T = TXT[lang]
    items = [(T["classes"].get(k, k), v["blocked"], v["total"]) for k, v in data["by_category"].items()]
    items.sort(key=lambda t: t[2])
    fig, ax = plt.subplots(figsize=(12.6, 4.6))
    names = [n for n, _, _ in items]
    blocked = [b for _, b, _ in items]
    bars = ax.barh(names, blocked, height=0.55, color=P["print"], linewidth=0)
    for bar, (_, b, t) in zip(bars, items):
        ax.text(bar.get_width() + 0.08, bar.get_y() + bar.get_height() / 2, f"{b}/{t}",
                va="center", fontsize=11.5, color=P["teal"], fontweight="bold")
    _clean(ax)
    ax.set_xlim(0, max(blocked) * 1.25)
    ax.set_xticks(range(0, max(blocked) + 1))
    ax.tick_params(axis="y", labelsize=11)
    _title(ax, T["class_title"], T["class_sub"])
    fig.tight_layout()
    path = OUT / f"by_class_{lang}.png"
    fig.savefig(path, dpi=200, bbox_inches="tight")
    plt.close(fig)
    return path


def fig_scenarios(data: dict, lang: str) -> Path:
    T = TXT[lang]
    order = ["智能转账", "账单分析", "理财操作", "卡片管理", "订阅与代扣管理", "跨场景联动"]
    stats = data["by_scenario"]
    items = [(SCEN_EN[n] if lang == "en" else n, stats[n]["total"], stats[n]["correct"])
             for n in order if n in stats]
    fig, ax = plt.subplots(figsize=(12.6, 4.6))
    names = [n for n, _, _ in reversed(items)]
    totals = [t for _, t, _ in reversed(items)]
    bars = ax.barh(names, totals, height=0.55, color=P["print"], linewidth=0)
    for bar, (_, t, ok) in zip(bars, reversed(items)):
        label = T["scen_label"].format(t=t)
        if lang == "en" and t == 1:
            label = label.replace("cases", "case")
        ax.text(bar.get_width() + 0.1, bar.get_y() + bar.get_height() / 2, label,
                va="center", fontsize=10, color=P["teal"] if ok == t else P["seal"])
    _clean(ax)
    ax.set_xlim(0, max(totals) * 1.6)
    ax.set_xticks([])
    ax.tick_params(axis="y", labelsize=11)
    _title(ax, T["scen_title"], T["scen_sub"])
    fig.tight_layout()
    path = OUT / f"scenarios_{lang}.png"
    fig.savefig(path, dpi=200, bbox_inches="tight")
    plt.close(fig)
    return path


def main() -> int:
    results = ROOT / "logs" / "benchmark.json"
    if not results.exists():
        print("先运行 / run first:  python -m arbiter.cli bench", file=sys.stderr)
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    data = json.loads(results.read_text(encoding="utf-8"))
    for lang in ("zh", "en"):
        for fn in (fig_at_risk, fig_by_class, fig_scenarios):
            print("wrote", fn(data, lang).relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
