"""
授权与签署 / Ratification and execution authorisation.

Two distinct signing events with different meanings:

  ratify     the customer signs the *envelope digest* -- a bounded statement
             of purpose they can actually read. This is the trust anchor.
             Customers cannot meaningfully review an API payload, and a system
             that asks them to has offloaded its security problem onto the
             person least equipped to solve it.

  authorise  the kernel authorises a plan, and only one it has already
             approved. The authoriser re-checks the binding itself rather than
             trusting its caller, so a bug elsewhere cannot become an
             authorisation on an unapproved plan.

Ed25519 keeps the demo and the test suite free of native dependencies. A
production deployment puts this behind the bank's HSM, with the kernel's ALLOW
verdict as the authorisation input, so compromising the orchestration process
does not yield signing capability.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from arbiter.types import Decision, OpPlan, RatifiedIntent, Verdict


class Keyring(Protocol):
    def sign(self, digest: str) -> str: ...
    def verify(self, digest: str, signature: str, signer: str) -> bool: ...


class AuthorisationRefused(RuntimeError):
    """Raised when the authoriser is asked to act on something unapproved."""


@dataclass
class Ed25519Keyring:
    _private: Ed25519PrivateKey
    _registry: dict[str, Ed25519PublicKey]

    @classmethod
    def generate(cls, seed: bytes | None = None) -> "Ed25519Keyring":
        key = (Ed25519PrivateKey.from_private_bytes(hashlib.sha256(seed).digest())
               if seed is not None else Ed25519PrivateKey.generate())
        return cls(key, {})

    def bind(self, identity: str) -> "Ed25519Keyring":
        """Declare that this keyring signs for `identity` (a customer id)."""
        self._registry[str(identity)] = self._private.public_key()
        return self

    def sign(self, digest: str) -> str:
        return self._private.sign(bytes.fromhex(digest)).hex()

    def verify(self, digest: str, signature: str, signer: str) -> bool:
        public = self._registry.get(str(signer))
        if public is None:
            return False
        try:
            public.verify(bytes.fromhex(signature), bytes.fromhex(digest))
            return True
        except (InvalidSignature, ValueError):
            return False


class ExecutionAuthoriser:
    """
    Authorises execution payloads, and refuses everything else.

    The invariant: there is no path from a plan to an execution authorisation
    that does not pass a kernel decision with verdict ALLOW whose plan digest
    matches the plan being authorised. The check is repeated here on purpose --
    it costs a string comparison, and the failure mode is an irreversible
    payment.
    """

    def __init__(self, keyring: Keyring):
        self.keyring = keyring
        self.issued: list[dict[str, str]] = []

    def authorise(self, plan: OpPlan, decision: Decision, ratified: RatifiedIntent) -> str:
        if decision.verdict is not Verdict.ALLOW:
            raise AuthorisationRefused(
                f"内核裁决为 {decision.verdict.value}；授权器仅对 ALLOW 生效 / "
                f"kernel verdict is {decision.verdict.value}; the authoriser acts only on ALLOW"
            )
        if decision.plan_digest != plan.digest():
            raise AuthorisationRefused(
                "已批准的计划摘要与待授权计划不一致 / approved plan digest does not match "
                "the plan presented for authorisation"
            )
        if decision.intent_digest != ratified.spec.digest():
            raise AuthorisationRefused(
                "已批准的授权信封与待授权信封不一致 / approved envelope digest does not "
                "match the ratified envelope presented"
            )
        if plan.intent_digest != ratified.spec.digest():
            raise AuthorisationRefused(
                "计划未绑定到该授权信封 / plan is not bound to the ratified envelope"
            )

        payload = json.dumps({
            "plan": plan.digest(),
            "intent": ratified.spec.digest(),
            "effects": decision.effects_digest,
            "tier": decision.tier.value,
        }, sort_keys=True, separators=(",", ":"))
        envelope_digest = hashlib.sha256(payload.encode()).hexdigest()
        signature = self.keyring.sign(envelope_digest)
        self.issued.append({"envelope": envelope_digest, "signature": signature,
                            "plan": plan.digest()})
        return signature
