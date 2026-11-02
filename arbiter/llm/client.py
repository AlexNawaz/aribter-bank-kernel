"""
模型边界 / The model boundary.

Everything a language model returns crosses into the system here, and nowhere
else. Three rules apply:

  1. Output is parsed, never evaluated. A strict schema with a closed
     vocabulary; an unknown field is a rejection, not a warning.
  2. Output is tainted on arrival.
  3. Output carries no authority. The most a model can do is cause the kernel
     to evaluate a different plan, and the kernel judges plans on observed
     effects.

The third rule is what makes the first two a convenience rather than a
defence. Schema validation stops malformed output; it does not stop a model
that has been talked into proposing a well-formed theft.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol


class LLMClient(Protocol):
    name: str
    def complete(self, system: str, user: str) -> str: ...


class SchemaError(ValueError):
    """Model output did not conform to the expected shape."""


@dataclass
class ParsedOutput:
    data: dict[str, Any]
    raw: str


def parse_strict(raw: str, required: set[str], allowed: set[str]) -> ParsedOutput:
    """
    Parse model output as JSON with a closed field set.

    Models fail at this boundary constantly -- fenced blocks, a helpful
    preamble, trailing prose. Recovering from those is fine. Accepting a field
    the schema does not know about is not: an unknown field is either a model
    error or an attempt to reach a code path the schema was written to exclude.
    """
    text = str(raw).strip()
    if text.startswith("```"):
        text = "\n".join(ln for ln in text.splitlines()
                         if not ln.strip().startswith("```")).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise SchemaError("模型输出中没有 JSON 对象 / no JSON object in model output")
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise SchemaError(f"模型输出不是合法 JSON / invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SchemaError("模型输出不是 JSON 对象 / model output is not a JSON object")

    missing = required - data.keys()
    if missing:
        raise SchemaError(f"缺少必填字段 / missing required field(s): {sorted(missing)}")
    unknown = data.keys() - allowed
    if unknown:
        raise SchemaError(f"存在未知字段，已拒绝 / unknown field(s) rejected: {sorted(unknown)}")
    return ParsedOutput(data=data, raw=raw)


@dataclass
class ScriptedModel:
    """Returns pre-set responses in order. Deterministic, for tests."""
    responses: list[str]
    name: str = "scripted"
    calls: list[tuple[str, str]] = field(default_factory=list)
    _index: int = 0

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        if self._index >= len(self.responses):
            raise RuntimeError("ScriptedModel exhausted")
        out = self.responses[self._index]
        self._index += 1
        return out


@dataclass
class CompromisedModel:
    """
    完全受控的模型 / A model that obeys any instruction it finds, including
    instructions that arrived inside quarantined untrusted content.

    This is the adversary the benchmark assumes. The usual way to evaluate an
    agent's injection resistance is to attack the model and count how often it
    holds -- a measurement that produces figures like "blocked 94% of
    payloads", which is an admission that the remaining 6% empties the account.

    So the evaluation is inverted: the model is assumed fully compromised and
    proposes exactly what the attacker wants, every time. The question being
    measured is not whether the model resists. It is whether the customer is
    protected when it does not resist at all.
    """
    hostile_plan: dict[str, Any]
    name: str = "compromised"
    calls: list[tuple[str, str]] = field(default_factory=list)

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        return json.dumps(self.hostile_plan, ensure_ascii=False)


@dataclass
class FunctionModel:
    fn: Callable[[str, str], str]
    name: str = "function"
    calls: list[tuple[str, str]] = field(default_factory=list)

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        return self.fn(system, user)
