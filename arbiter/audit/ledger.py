"""
审计日志：哈希链 / Append-only, hash-chained decision log.

Every adjudication is recorded with the digests of the envelope, the plan and
the observed effects, plus the full finding list. Each entry commits to the
previous entry's hash, so an entry cannot be altered or removed after the fact
without breaking every link that follows.

The log is written to be *replayable*. Because the kernel is deterministic and
each entry records the inputs it saw, a reviewer can re-run a decision and
check that the recorded verdict is the one the code produces. A log that
records only conclusions asks to be trusted; this one can be checked. For a
regulated institution that distinction is the difference between an audit
trail and a claim.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from arbiter.types import stable_digest

GENESIS = "0" * 64


@dataclass
class LedgerEntry:
    seq: int
    ts: float
    kind: str
    body: dict[str, Any]
    prev_hash: str
    entry_hash: str = ""

    def compute_hash(self) -> str:
        return stable_digest({
            "seq": self.seq, "ts": round(self.ts, 6), "kind": self.kind,
            "body": self.body, "prev_hash": self.prev_hash,
        })

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq, "ts": round(self.ts, 6), "kind": self.kind,
            "body": self.body, "prev_hash": self.prev_hash, "entry_hash": self.entry_hash,
        }


class AuditLedger:
    def __init__(self, path: str | os.PathLike[str] | None = None):
        self.path = Path(path) if path else None
        self.entries: list[LedgerEntry] = []
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists():
                self._load()

    @property
    def head(self) -> str:
        return self.entries[-1].entry_hash if self.entries else GENESIS

    def append(self, kind: str, body: dict[str, Any]) -> LedgerEntry:
        entry = LedgerEntry(len(self.entries), time.time(), kind, body, self.head)
        entry.entry_hash = entry.compute_hash()
        self.entries.append(entry)
        if self.path:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry.to_dict(), sort_keys=True, ensure_ascii=False) + "\n")
        return entry

    def _load(self) -> None:
        assert self.path
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                raw = json.loads(line)
                self.entries.append(LedgerEntry(
                    raw["seq"], raw["ts"], raw["kind"], raw["body"],
                    raw["prev_hash"], raw["entry_hash"],
                ))

    def verify(self) -> tuple[bool, str]:
        """Walk the chain and confirm every link."""
        prev = GENESIS
        for index, entry in enumerate(self.entries):
            if entry.seq != index:
                return False, f"条目 {index} 序号错乱 / entry {index} has sequence {entry.seq}"
            if entry.prev_hash != prev:
                return False, f"条目 {index} 与前序断链 / entry {index} does not link to its predecessor"
            if entry.compute_hash() != entry.entry_hash:
                return False, f"条目 {index} 内容被篡改 / entry {index} content does not match its hash"
            prev = entry.entry_hash
        return True, f"{len(self.entries)} 条记录校验通过 / {len(self.entries)} entries verified, head {prev[:16]}"

    def __iter__(self) -> Iterator[LedgerEntry]:
        return iter(self.entries)

    def __len__(self) -> int:
        return len(self.entries)
