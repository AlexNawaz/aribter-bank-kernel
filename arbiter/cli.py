"""
命令行入口 / Command line entry point.

    python -m arbiter.cli list                场景清单 / list the adversarial cases
    python -m arbiter.cli demo I01            逐步演示单个场景 / walk through one case
    python -m arbiter.cli bench               对抗基准测试 / adversarial benchmark
    python -m arbiter.cli conv                对话基准测试 / conversation benchmark
    python -m arbiter.cli nlu                 换一种说法 / paraphrase evaluation
    python -m arbiter.cli audit               自动对话审计 / automated conversation audit
    python -m arbiter.cli chat                终端多轮对话 / chat in the terminal
    python -m arbiter.cli serve               网页界面 / web interface
    python -m arbiter.cli verify <path>       校验审计日志哈希链 / verify an audit log

The demo output is written to be read aloud: each stage prints what it did and
why, so a reviewer watching a recording can follow the decision without
reading source.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from arbiter.audit.ledger import AuditLedger
from arbiter.authz.signer import Ed25519Keyring, ExecutionAuthoriser
from arbiter.bank.world import CUSTOMER_ID
from arbiter.intent.compiler import IntentCompiler
from arbiter.intent.ratify import ratify_with, render_en, render_zh
from arbiter.kernel.adjudicator import Adjudicator, KernelConfig
from arbiter.llm.client import ScriptedModel
from arbiter.types import Verdict, cny, fen_to_yuan, mask_account

C = {"dim": "\033[2m", "bold": "\033[1m", "red": "\033[31m", "green": "\033[32m",
     "yellow": "\033[33m", "cyan": "\033[36m", "off": "\033[0m"}


def _c(text: str, colour: str, plain: bool = False) -> str:
    return text if plain else f"{C[colour]}{text}{C['off']}"


def _rule(title: str, plain: bool = False) -> None:
    print(f"\n{_c('─' * 76, 'dim', plain)}")
    print(_c(f" {title}", "bold", plain))
    print(_c("─" * 76, "dim", plain))


def cmd_demo(case_id: str, plain: bool = False, lang: str = "zh") -> int:
    from benchmarks.cases import CASES
    from benchmarks.runner import FIXED_NOW, _build_plan

    case = next((c for c in CASES if c.id.upper() == case_id.upper()), None)
    if case is None:
        print(f"没有编号为 {case_id!r} 的场景；请先运行 list", file=sys.stderr)
        return 2

    print()
    print(_c(f"  ARBITER 银行智能体  ·  场景 {case.id}  ({case.scenario})", "bold", plain))
    print(_c(f"  {case.title_zh}", "cyan", plain))
    print(_c(f"  {case.title_en}", "dim", plain))
    if case.attack_zh != "无":
        print(_c(f"  攻击手法：{case.attack_zh}", "red", plain))

    core = case.world()
    keyring = Ed25519Keyring.generate(seed=case.id.encode()).bind(CUSTOMER_ID)
    ledger = AuditLedger()

    # The demo runs on the wall clock rather than the benchmark's fixed
    # timestamp, so the envelope a viewer reads is live rather than dated to
    # whenever the fixture was written. An envelope that looks expired while
    # its plan executes would contradict the EXPIRY invariant on screen.
    # Reproducibility lives in `bench`, which keeps the fixed clock.
    now = int(time.time())

    # 1 ---------------------------------------------------------------
    _rule("1 · 意图理解：请求 → 可签署的授权信封", plain)
    compiler = IntentCompiler(ScriptedModel([case.envelope]), core)
    spec = compiler.compile("客户请求", CUSTOMER_ID, now=now)
    print(render_zh(spec) if lang == "zh" else render_en(spec))
    print(_c("\n  以上文本由已签署的结构体确定性生成，不经过模型。", "dim", plain))
    print(_c("  被攻陷的模型无法「描述一套限制、编码另一套」。", "dim", plain))

    # 2 ---------------------------------------------------------------
    _rule("2 · 客户签署的是「限制条件」，不是某一笔交易", plain)
    ratified = ratify_with(spec, keyring, now=now)
    ledger.append("intent_ratified", {"digest": spec.digest()})
    print(f"  签名   {ratified.signature[:48]}…")
    print(f"  锚定   {spec.digest()[:48]}…")

    # 3 ---------------------------------------------------------------
    _rule("3 · 任务规划：模型提出具体银行操作", plain)
    plan = _build_plan(case, spec.digest())
    for i, op in enumerate(plan.operations):
        target = mask_account(op.target) if op.target.isdigit() else op.target
        amount = f"  金额 {cny(op.amount)}" if op.amount else ""
        print(f"  [{i}] {op.kind.value}  →  {target or '—'}{amount}")
        if op.account:
            print(f"      来源账户 {mask_account(op.account)}")
        if op.rationale:
            print(_c(f'      模型理由：「{op.rationale}」', "dim", plain))
    if plan.claimed_effects:
        print(_c(f'\n  模型声称：「{plan.claimed_effects}」', "yellow", plain))
    if plan.claimed_facts:
        facts = ", ".join(f"{k}={fen_to_yuan(int(v))}" for k, v in plan.claimed_facts.items())
        print(_c(f"  模型声称的事实：{facts}", "yellow", plain))
    if case.untrusted:
        print(_c(f"\n  模型上下文中含有 {len(case.untrusted)} 段来自账单/附言的不可信内容。",
                 "red", plain))

    # 4 ---------------------------------------------------------------
    _rule("4 · 内核裁决：只看影子执行的实际效果，不看模型的说法", plain)
    kernel = Adjudicator(core, KernelConfig(), verifier=keyring)
    report = kernel.adjudicate(ratified, plan, now=now + 5, untrusted=case.untrusted)
    ledger.append("adjudicated", report.to_dict())

    fx = report.effects
    if fx is None:
        print(_c("  计划在模拟执行前已被拒绝（静态策略或实体接地）。", "yellow", plain))
    else:
        print(f"  资金变动 {len(fx.balances)} 项   代扣变更 {len(fx.mandates)} 项   "
              f"卡片风控 {len(fx.card_controls)} 项   凭证变更 {len(fx.credentials)} 项")
        for bal in fx.balances:
            if bal.delta:
                arrow = _c("出", "red", plain) if bal.delta < 0 else _c("入", "green", plain)
                print(f"     {arrow}  {cny(abs(bal.delta))}   {mask_account(bal.account)}")
        for mandate in fx.mandates:
            state = "新建" if mandate.created else ("取消" if mandate.cancelled else "修改")
            cap = "无上限" if mandate.uncapped else cny(max(0, mandate.cap_after))
            print(f"     代扣 {state}  「{mandate.merchant}」 额度 {cap}")
        for change in fx.card_controls:
            direction = _c("削弱", "red", plain) if change.weakening else _c("加强", "green", plain)
            print(f"     卡片 {change.card_id} {change.control} {direction}："
                  f"{change.before} → {change.after}")

    flagged = [t for t in report.taint_reports if t["score"] > 0]
    if flagged:
        print(_c(f"\n  不可信内容检测：{len(flagged)} 段被标记", "yellow", plain))
        for t in flagged[:3]:
            print(f"     来源 {t['origin']}  评分 {t['score']}（{t['severity']}）")
            print(f"     特征 {', '.join(t['signals'][:4])}")
            if t["encoding_layers"]:
                print(f"     已剥离编码 {', '.join(t['encoding_layers'])}")

    blocking = report.decision.blocking()
    if blocking:
        print(_c(f"\n  {len(blocking)} 项阻断性发现：", "red", plain))
        for f in blocking:
            print(f"     {_c(f.code, 'red', plain)}  {f.message_zh}")

    # 5 ---------------------------------------------------------------
    _rule("5 · 裁决", plain)
    verdict = report.decision.verdict
    colour = {"allow": "green", "require_approval": "yellow", "deny": "red"}[verdict.value]
    print(f"  {_c(verdict.label_zh, colour, plain)}  "
          f"（{verdict.value.upper()}）   权限 {report.decision.tier.label_zh}"
          f" / {report.decision.tier.label}   用时 {report.decision.elapsed_ms:.2f} ms")

    if verdict is Verdict.ALLOW:
        ExecutionAuthoriser(keyring).authorise(plan, report.decision, ratified)
        receipts = core.commit(plan.operations)
        ledger.append("executed", {"receipts": receipts})
        print(_c(f"\n  已执行，回单：{', '.join(receipts)}", "green", plain))
    elif verdict is Verdict.REQUIRE_APPROVAL:
        print(_c("\n  已暂停并提交客户确认，未产生任何授权。", "yellow", plain))
    else:
        print(_c("\n  已拒绝。授权器从未被调用，核心系统未收到任何指令。", "red", plain))
        if case.naive_zh:
            print(_c(f"  无防护的智能体在此处会：{case.naive_zh}。", "dim", plain))

    ok, msg = ledger.verify()
    print(_c(f"\n  审计日志：{msg}", "dim" if ok else "red", plain))
    print()
    return 0


def cmd_bench(out: Path, plain: bool = False) -> int:
    from benchmarks.runner import run_all

    out.mkdir(parents=True, exist_ok=True)
    cases_dir = out / "cases"
    cases_dir.mkdir(exist_ok=True)

    summary = run_all(cases_dir)
    (out / "benchmark.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print()
    print(_c("  ARBITER 银行智能体 · 对抗基准测试", "bold", plain))
    print(_c("  " + "─" * 86, "dim", plain))
    print(_c(f"  {'编号':5} {'类别':14} {'场景':20} {'预期':17} {'实际':17} {'风险敞口':>12}",
             "dim", plain))
    for r in summary["results"]:
        mark = _c("✓", "green", plain) if r["correct"] else _c("✗ 不符", "red", plain)
        at_risk = max(r["naive_loss"], r["naive_exposure"])
        risk = cny(at_risk) if at_risk else "—"
        if r["category"] == "benign":
            risk = _c("不适用", "dim", plain)
        print(f"  {r['id']:5} {r['category']:14} {r['scenario']:20} "
              f"{r['expected']:17} {r['verdict']:17} {risk:>12}  {mark}")

    print(_c("  " + "─" * 86, "dim", plain))
    print(f"  攻击拦截            {summary['attacks_blocked']}/{summary['attacks']}")
    print(f"  正常用例处理正确    {summary['benign_correct']}/{summary['benign']}")
    print(f"  误报（误拦正常操作）{summary['false_positives']}")
    print(f"  无防护基线风险敞口  {cny(summary['at_risk_naive_fen'])}")
    print(f"  无防护基线当日损失  {cny(summary['loss_naive_fen'])}")
    print(f"  ARBITER 实际损失    {cny(summary['loss_arbiter_fen'])}")
    print(f"  单场景最大敞口      {cny(summary['worst_single_fen'])}")
    print(f"  信息外泄（无防护）  {summary['leaks_naive']} 个场景      "
          f"ARBITER {summary['leaks_arbiter']} 个")
    print(f"  决策耗时中位数      {summary['median_latency_ms']:.2f} ms")
    print()
    print(_c("  按攻击类别 / by attack class", "bold", plain))
    labels = {"injection": "注入防御", "hallucination": "幻觉防护",
              "financial": "资金与适当性", "structural": "结构性攻击",
              "disclosure": "信息套取"}
    for name, stat in summary["by_category"].items():
        print(f"    {labels.get(name, name):16} {stat['blocked']}/{stat['total']}")
    # as_posix() so the two paths are punctuated the same way on Windows,
    # where Path renders a backslash and the literal separator beside it does not.
    print(f"\n  结果已写入 {out.as_posix()}/benchmark.json 与 {cases_dir.as_posix()}/")
    print()
    return 0 if summary["all_correct"] else 1


def cmd_conv(out: Path, plain: bool = False) -> int:
    """对话基准 / The conversation benchmark."""
    import json as _json
    from benchmarks.conversations import run_all

    out.mkdir(parents=True, exist_ok=True)
    summary = run_all()
    (out / "conversations.json").write_text(
        _json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print()
    print(_c("  ARBITER 银行智能体 · 对话基准测试", "bold", plain))
    print(_c("  （赛题「创新性及交互体验」一项考察的五个维度）", "dim", plain))
    print(_c("  " + "─" * 84, "dim", plain))
    print(_c(f"  {'编号':5} {'维度':10} {'轮次':>4}  {'预期':12} {'实际':12} {'用例'}",
             "dim", plain))
    for r in summary["results"]:
        mark = _c("✓", "green", plain) if r["ok"] else _c("✗", "red", plain)
        print(f"  {r['id']:5} {r['dimension']:10} {r['turns']:>4}  "
              f"{r['expected']:12} {r['actual']:12} {r['title_zh']}  {mark}")
    print(_c("  " + "─" * 84, "dim", plain))
    print(f"  通过          {summary['passed']}/{summary['total']}")
    print(f"  平均轮次      {summary['avg_turns']}")
    print()
    print(_c("  按维度 / by dimension", "bold", plain))
    for dim, stat in summary["by_dimension"].items():
        print(f"    {dim:10} {stat['ok']}/{stat['total']}")
    _write_transcripts(summary, out / "对话记录.txt")
    print(f"\n  结果已写入 {out.as_posix()}/conversations.json 与 {out.as_posix()}/对话记录.txt")
    print()
    return 0 if summary["all_ok"] else 1


def _write_transcripts(summary: dict, path: Path) -> None:
    """把每一段对话原样写下来，评委不用运行就能读到智能体实际说了什么。"""
    from benchmarks.conversations import CONVERSATIONS
    titles_en = {c.id: c.title_en for c in CONVERSATIONS}
    rule = "─" * 78
    lines = ["=" * 78,
             "ARBITER 银行 AI 智能体 — 对话记录 / conversation transcripts",
             "复现 / reproduce:  python -m arbiter.cli conv      交互 / try it:  python -m arbiter.cli serve",
             f"生成时间 / generated:  {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
             f"通过 / passed:  {summary['passed']}/{summary['total']}      "
             f"平均轮次 / mean turns:  {summary['avg_turns']}",
             "=" * 78, ""]
    for r in summary["results"]:
        lines += [rule, f"{r['id']} · {r['dimension']} · {r['title_zh']}",
                  f"       {titles_en.get(r['id'], '')}", ""]
        if r.get("note_zh"):
            lines += [f"  说明：{r['note_zh']}", ""]
        lines += [f"  预期最后一轮 / expected: {r['expected']}   实际 / actual: {r['actual']}   "
                  f"轮次 / turns: {r['turns']}   {'通过 / pass' if r['ok'] else '未通过 / FAIL'}", ""]
        for t in r["transcript"]:
            lines.append(f"  客户> {t['user']}")
            for row in (t["agent"] or "").splitlines() or [""]:
                lines.append(f"  助手> {row}")
            lines.append(f"        [{t['kind']}]")
            lines.append("")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def cmd_nlu(out: Path, plain: bool = False) -> int:
    """
    换一种说法 / The paraphrase evaluation.

    回答的是一个很直接的质疑：「这些是你预先写好的问法，我换个说法还行吗？」
    七组说法都不在规则里也不在语料里。调参组和盲测一至三在重写解析器时对照过，
    分数只能说明没有退化。盲测四、五、六在解析器定稿之后写成、各只跑一次，表里
    单列它们的首测；对外引用首测合计，不是补齐之后的 100%。
    """
    import json as _json

    import arbiter.dialogue.nlu as _nlu
    from arbiter.bank.world import build_world
    from arbiter.dialogue.corpus import size as corpus_size
    from arbiter.dialogue.nlu import RuleBasedNLU
    from benchmarks.paraphrases import (BLIND, BLIND2, BLIND3, BLIND4, BLIND5, BLIND6, FIRST_RUN,
                                        HELD_OUT, run_all)

    sets = ((HELD_OUT, "调参用"), (BLIND, "盲测一"), (BLIND2, "盲测二"), (BLIND3, "盲测三"),
            (BLIND4, "盲测四"), (BLIND5, "盲测五"), (BLIND6, "盲测六"))

    saved = (_nlu._SIM_ACCEPT_READ, _nlu._SIM_ACCEPT_ACTION, _nlu._SIM_CONFIRM_FLOOR)
    _nlu._SIM_ACCEPT_READ = _nlu._SIM_ACCEPT_ACTION = _nlu._SIM_CONFIRM_FLOOR = 99.0
    rules_only = [run_all(nlu=RuleBasedNLU(build_world()), cases=c) for c, _ in sets]
    _nlu._SIM_ACCEPT_READ, _nlu._SIM_ACCEPT_ACTION, _nlu._SIM_CONFIRM_FLOOR = saved
    full = [run_all(nlu=RuleBasedNLU(build_world()), cases=c, label=l) for c, l in sets]

    print()
    print(_c("  ARBITER 银行智能体 · 换一种说法", "bold", plain))
    print(_c(f"  语料 {corpus_size()} 条例句，纯标准库，无模型、无网络", "dim", plain))
    print(_c("  " + "─" * 72, "dim", plain))
    print(_c(f"  {'用例组':10} {'条数':>4}  {'仅规则':>8}  {'规则+相似度':>12}  {'首测':>8}",
             "dim", plain))
    for (_, label), a, b in zip(sets, rules_only, full):
        first = FIRST_RUN.get(label)
        first_s = f"{first[0] / first[1] * 100:.1f}%" if first else "—"
        print(f"  {label:10} {b['total']:>4}  {a['accuracy']*100:>7.1f}%  "
              f"{b['accuracy']*100:>11.1f}%  {first_s:>8}")
    print(_c("  " + "─" * 72, "dim", plain))
    blind = full[-1]
    ok = sum(v[0] for v in FIRST_RUN.values())
    n = sum(v[1] for v in FIRST_RUN.values())
    print("  前四组都在重写解析器的过程中对照过，它们的分数只能说明「没有退化」。")
    print(f"  盲测四、五、六都是定稿后写成、各只跑一次（中英共 {n} 句）：首测合计 {ok / n * 100:.1f}%"
          f"（{ok}/{n}）。")
    print("  按首测的错误补齐之后的 100% 不再是盲测；对外引用首测。")
    print("  Sets 1-4 were consulted while the parser was rebuilt, so they show no regression,")
    print(f"  not generalisation. BLIND4-6 ({n} Chinese and English sentences) were each written after")
    print(f"  the parser was frozen and run once: first run {ok / n * 100:.1f}% ({ok}/{n}) -- the number we quote.")
    print()

    print(_c("  按路由 / by route", "bold", plain))
    for route, stat in sorted(blind["by_route"].items()):
        print(f"    {route:10} {stat['ok']}/{stat['total']}")
    if blind["misses"]:
        print()
        print(_c("  最后一组里没答对的 / what it still gets wrong", "bold", plain))
        for m in blind["misses"]:
            print(f"    {m['expected']:14} 判成 {m['actual']:14} "
                  f"{m['route']:8} {m['confidence']:.2f}  {m['text']}")

    out.mkdir(parents=True, exist_ok=True)
    payload = {"corpus": corpus_size(),
               "first_run": {label: {"passed": ok_, "total": n_} for label, (ok_, n_) in FIRST_RUN.items()},
               "first_run_total": {"passed": ok, "total": n},
               "sets": [{"label": l, "rules_only": a["accuracy"], **b}
                        for (_, l), a, b in zip(sets, rules_only, full)]}
    (out / "paraphrases.json").write_text(
        _json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n  结果已写入 {out.as_posix()}/paraphrases.json")
    print()
    return 0


def cmd_audit(out: Path, plain: bool = False, fuzz: bool = True) -> int:
    """
    自动对话审计 / The automated conversation audit.

    成批生成对话（六大场景 × 说法 × 回答方式 × 确认/拒绝措辞），逐轮自动检查，外加
    随机输入与随机游走。最重要的一条全局性质：账户的任何变化，之前一定有一份客户看过
    的授权，并且客户明确确认了。
    """
    import json as _json
    from benchmarks.audit import run_all

    out.mkdir(parents=True, exist_ok=True)
    res = run_all(fuzz=fuzz)
    (out / "audit.json").write_text(_json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
    print()
    print(_c("  ARBITER 银行智能体 · 自动对话审计 / automated conversation audit", "bold", plain))
    print(_c("  " + "─" * 76, "dim", plain))
    print(f"  脚本 / scripts          {res['scripts']}（{res['turns']} 轮 / turns）")
    print(f"  通过 / passed           {res['passed']}/{res['scripts']}")
    if fuzz:
        print(f"  随机输入与随机游走      {len(res['fuzz_failures'])} 处违反 / violations")
    print(f"  用时 / elapsed          {res['elapsed_s']} s")
    print()
    print(_c("  按性质 / by property（0 表示没有违反）", "bold", plain))
    props = ["U1_exception", "U1_empty", "U1_kind", "U2_internals", "U3_full_account",
             "U5_unanswerable_question", "U6_offer_not_honoured", "U7_hollow_completion",
             "U8_unconfirmed_change", "E_kind", "E_direction", "E_target", "E_amount", "F_effect"]
    for prop in props:
        n = res["by_property"].get(prop, 0)
        mark = _c("✓", "green", plain) if n == 0 else _c(f"✗ {n}", "red", plain)
        print(f"    {prop:28} {mark}")
    print()
    print(_c("  按类别 / by category", "bold", plain))
    for cat, stat in res["by_category"].items():
        mark = "" if stat["ok"] == stat["total"] else _c("  ✗", "red", plain)
        print(f"    {cat:24} {stat['ok']}/{stat['total']}{mark}")
    print(f"\n  结果已写入 / written to {out.as_posix()}/audit.json")
    print()
    clean = res["passed"] == res["scripts"] and not res["fuzz_failures"]
    return 0 if clean else 1


def cmd_chat(plain: bool = False, attacked: bool = False) -> int:
    """终端里的多轮对话 / A conversation in the terminal."""
    from arbiter.bank.world import (
        INJECT_MEMO_TRANSFER, build_world, with_lookalike_payee, with_memo_injection,
    )
    from arbiter.dialogue.session import Session

    core = build_world()
    if attacked:
        core = with_lookalike_payee(with_memo_injection(core, INJECT_MEMO_TRANSFER))

    session = Session(core=core)
    name = core.state.customer.name

    print()
    print(_c(f"  ARBITER 银行 AI 智能体  ·  客户 {name}", "bold", plain))
    if attacked:
        print(_c("  演示模式：账户已被攻击（附言注入 + 账号混淆收款人）。客户本人没有做错任何事。",
                 "red", plain))
    print(_c("  直接说您想做什么（中文或英文都可以）。输入 退出 结束。", "dim", plain))
    print(_c("  Say what you need, in Chinese or English. Type exit to quit.", "dim", plain))
    print(_c("  例如：这个月钱都花哪了 / 给房东转4500 / transfer 4500 to my landlord", "dim", plain))
    print()

    colour = {"refusal": "red", "escalation": "yellow", "result": "green",
              "envelope": "cyan", "ask": "cyan", "info": "dim"}
    while True:
        try:
            text = input(_c("  您 > ", "bold", plain)).strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  再见。")
            return 0
        if text in ("退出", "exit", "quit", "q"):
            print("  再见。")
            return 0
        if not text:
            continue

        reply = session.say(text)
        body = "\n".join("      " + line for line in reply.text.splitlines())
        print(_c("  助手 >", colour.get(reply.kind, "dim"), plain))
        print(body)
        if reply.envelope:
            print()
            for line in reply.envelope.splitlines():
                print(_c("      " + line, "dim", plain))
        if reply.kind in ("envelope", "escalation"):
            print(_c("      （回复「确认」执行，回复「取消」放弃 / reply confirm or cancel）", "dim", plain))
        print()


def cmd_serve(host: str, port: int) -> int:
    from arbiter.dialogue.server import serve
    serve(host, port)
    return 0


def cmd_verify(path: Path) -> int:
    ok, message = AuditLedger(path).verify()
    print(("完整 / intact:  " if ok else "已损坏 / BROKEN:  ") + message)
    return 0 if ok else 1


def cmd_list() -> int:
    from benchmarks.cases import CASES
    width = max(len(c.title_zh) for c in CASES)
    for c in CASES:
        print(f"  {c.id}  {c.scenario:10} {c.category:14} {c.title_zh:{width}}  "
              f"预期 {c.expect}")
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        return _main(argv)
    except BrokenPipeError:
        # 输出被提前关掉（比如接了 | head）：安静退出，不打印回溯
        # output closed early (e.g. piped into head): exit quietly, no traceback
        import os
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 0


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="arbiter", description="ARBITER 银行智能体")
    parser.add_argument("--plain", action="store_true", help="禁用彩色输出")
    sub = parser.add_subparsers(dest="command", required=True)

    p_demo = sub.add_parser("demo", help="逐步演示单个场景")
    p_demo.add_argument("case", nargs="?", default="I01")
    p_demo.add_argument("--lang", choices=["zh", "en"], default="zh")

    p_bench = sub.add_parser("bench", help="运行全部场景")
    p_bench.add_argument("--out", type=Path, default=Path("logs"))

    p_conv = sub.add_parser("conv", help="运行对话基准测试")
    p_conv.add_argument("--out", type=Path, default=Path("logs"))

    p_nlu = sub.add_parser("nlu", help="换一种说法：意图理解覆盖率")
    p_nlu.add_argument("--out", type=Path, default=Path("logs"))

    p_audit = sub.add_parser("audit", help="自动对话审计")
    p_audit.add_argument("--out", type=Path, default=Path("logs"))
    p_audit.add_argument("--no-fuzz", action="store_true", help="跳过随机输入与随机游走")

    p_chat = sub.add_parser("chat", help="在终端里多轮对话")
    p_chat.add_argument("--attacked", action="store_true",
                        help="演示模式：账户中已存在注入附言与混淆收款人")

    p_serve = sub.add_parser("serve", help="启动网页对话界面")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)

    p_verify = sub.add_parser("verify", help="校验审计日志")
    p_verify.add_argument("path", type=Path)

    sub.add_parser("list", help="场景清单")

    # 「--plain」放在子命令前后都行 / accept --plain before or after the subcommand
    for name, sp in sub.choices.items():
        sp.add_argument("--plain", action="store_true", default=argparse.SUPPRESS,
                        help="禁用彩色输出 / no colour")
    args = parser.parse_args(argv)
    if args.command == "demo":
        return cmd_demo(args.case, args.plain, args.lang)
    if args.command == "bench":
        return cmd_bench(args.out, args.plain)
    if args.command == "conv":
        return cmd_conv(args.out, args.plain)
    if args.command == "nlu":
        return cmd_nlu(args.out, args.plain)
    if args.command == "audit":
        return cmd_audit(args.out, args.plain, not args.no_fuzz)
    if args.command == "chat":
        return cmd_chat(args.plain, args.attacked)
    if args.command == "serve":
        return cmd_serve(args.host, args.port)
    if args.command == "verify":
        return cmd_verify(args.path)
    return cmd_list()


if __name__ == "__main__":
    raise SystemExit(main())
