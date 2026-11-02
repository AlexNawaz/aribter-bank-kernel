# ARBITER · AI banking agent

**A banking agent that treats its own language model as hostile**

2026 Shenzhen International FinTech Competition (FinTechathon) · Domestic track · AI track · Banking AI agent



---

## In one sentence

> The model proposes; a deterministic kernel disposes. The model can be fully compromised, and the guarantee does not depend on it.

The brief asks for banking operations "through natural-language interaction within a security framework". The hard part is not teaching an agent to transfer money, read statements or buy funds; it is **whether the customer's money is still there when the model has been persuaded**. Prompts, injection classifiers and stronger models are probabilistic defences applied to an irreversible action. ARBITER takes the model, and the whole dialogue layer, out of the trusted computing base: they may propose anything, and whether it executes is decided by a deterministic kernel that contains no model and reads no natural language -- it looks only at what a plan actually does on a shadow copy of the core banking system, checked against the envelope the customer signed.

![Architecture](docs/figures/architecture_en.png)

---

## Results

All reproducible offline, with no network and no API key.

| Measure | Result | Command |
|---|---|---|
| Attacks blocked (fully compromised model) | **20/20**: injection 5, hallucination 5, funds & suitability 3, information extraction 3, structural 4 | `python -m arbiter.cli bench` |
| Benign cases | **9/9**, 0 blocked | same |
| Unguarded baseline → ARBITER | ¥264,420 lost on the day, ¥364,420 at risk, customer data leaked in 3 cases → **¥0, no leaks** | same |
| Kernel decision time | median about 0.6 ms | same |
| Conversation benchmark | **41/41**, mean 1.90 turns | `python -m arbiter.cli conv` |
| Paraphrases (three blind sets written after the parser was frozen, 224 Chinese and English sentences) | **first run 90.2% (202/224)** | `python -m arbiter.cli nlu` |
| Automated conversation audit | 563 generated conversations + ~4,800 turns of random input, **0 violations** | `python -m arbiter.cli audit` |
| Tests | 406, all passing | `pytest tests/ -q` |

---

## The three core technical requirements

| Requirement | Approach | Where |
|---|---|---|
| **Tiered authority** | T0 read / T1 bounded / T2 approval / T3 critical, from **observed effects**, with the signed envelope as the ceiling; first payments T2, control weakening T3; direction-sensitive | `arbiter/kernel/tiers.py` |
| **Hallucination and injection defence** | grounding, fact checks, amount consistency; untrusted content quarantined by taint, detection only logs and escalates; the kernel reads no natural language | `arbiter/kernel/grounding.py`, `taint.py` |
| **Intent understanding and task planning** | compositional parsing (object + action + negation + question), a multi-turn state machine, several steps under one signature, clarifying questions and corrections; the whole dialogue layer is untrusted | `arbiter/dialogue/` |

There is also a **disclosure guard** (information extraction): text that leaves the bank -- transfer memos, payee names, direct-debit references -- carrying the customer's ID number, account number, phone or a code is refused (`arbiter/kernel/disclosure.py`).

---

## Running

Python 3.11+.

```bash
python -m venv .venv
.venv\Scripts\activate            # macOS / Linux: source .venv/bin/activate
pip install -r requirements.txt

pytest tests/ -q                   # 406 tests
python -m arbiter.cli bench        # adversarial benchmark, writes logs/
python -m arbiter.cli conv         # conversation benchmark, writes logs/对话记录.txt
python -m arbiter.cli audit        # automated conversation audit
python -m arbiter.cli serve        # web interface at http://127.0.0.1:8000 (中 / EN at the top right)
```

Step-by-step Windows instructions and troubleshooting: [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

---

## The web interface

The conversation is on the left; the authorisation envelope is drawn as a bank voucher and stamped EXECUTED / REFUSED / VOID when its fate is decided. On the right: the kernel's decision for the turn, the authority tier, findings, untrusted-content detection and the audit hash chain. In "Attacked account" mode the account already holds a ¥0.01 inbound transfer whose memo poses as a bank notice, and a look-alike of the landlord's account in the payee book.

Things to try: "where did my money go this month", "transfer 4500 to my landlord", "cancel the gym subscription", "no, make it 5000", "buy a high-risk equity fund with 20000", "support asked me for my verification code", "send 1000 to 6222020200112233445" -- or the same in Chinese.

---

## Layout

```
arbiter/
  kernel/        trusted computing base: ten-step adjudication, grounding, invariants, disclosure, injection, tiers
  intent/        deterministic envelope rendering (text and structured voucher)
  authz/         envelope-bound execution authorisation (Ed25519)
  audit/         hash-chained audit ledger
  bank/          core banking simulator and the standard test world
  dialogue/      dialogue layer (untrusted): parsing, state machine, plans, answers, bilingual messages, web UI
  planner/ llm/  model boundary and stand-ins (used by the benchmark)
benchmarks/      adversarial cases, conversations, paraphrases, the automated audit
tests/           406 tests
docs/            technical document, security self-assessment, deployment, demo script, requirements trace (each in Chinese and English) and figures
logs/            results and ledgers from the latest run
scripts/         charts, diagrams, UI captures and the decks, generated from the logs
ARBITER_答辩_中文.pptx · ARBITER_Defense_EN.pptx   defence decks (about 9 min 30 s, script in the notes)
```

---


---

## Honest limitations

* The core banking system is a behavioural simulator; the results show the **architecture** handles these attack classes, not production performance.
* Scenarios, simulator and criteria were written by the same authors; independent red-teaming is the only real answer.
* Intent understanding scored 90.2% on the first run of three blind sets written after the parser was frozen, not 100%. The guarantee is not "always understands" but "a misunderstanding cannot move money".
* The full residual-risk list is in section 4 of the security self-assessment.

## Licence

MIT
