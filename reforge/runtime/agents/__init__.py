"""Agent role layer — Protocols, identity, message contract, registry, bus, and adapters.

Public surface:
    - PlannerAgent / VerifierAgent / SynthesizerAgent (Protocols)
    - SynthesisResult (return type)
    - ActorContext (lightweight runtime identity — P18.0)
    - RuntimeMessage (typed inter-agent message contract — P18.0)
    - AgentRegistry / RegistryKeyError (role/variant lookup — P18.1)
    - MessageBus / BusRoutingError (routing layer — P18.2)
    - BusVerifier / make_verifier_handler (multi-verifier fan-out — P18.3)
    - VerifierVoter (consensus voting — P18.3)
    - RunnerVerifier (default VerifierAgent backed by RuntimeRunner)
    - DefaultSynthesizer (default SynthesizerAgent backed by EvidenceAggregator)
"""

from __future__ import annotations

from reforge.runtime.agents.bus import BusRoutingError, MessageBus
from reforge.runtime.agents.bus_verifier import BusVerifier, make_verifier_handler
from reforge.runtime.agents.identity import ActorContext
from reforge.runtime.agents.message import RuntimeMessage
from reforge.runtime.agents.registry import AgentRegistry, RegistryKeyError
from reforge.runtime.agents.role import (
    PlannerAgent,
    SynthesisResult,
    SynthesizerAgent,
    VerifierAgent,
)
from reforge.runtime.agents.synthesizer import DefaultSynthesizer, render_conclusion
from reforge.runtime.agents.verifier import RunnerVerifier
from reforge.runtime.agents.voter import VerifierVoter

__all__ = [
    "ActorContext",
    "AgentRegistry",
    "BusRoutingError",
    "BusVerifier",
    "DefaultSynthesizer",
    "MessageBus",
    "PlannerAgent",
    "RegistryKeyError",
    "RunnerVerifier",
    "RuntimeMessage",
    "SynthesisResult",
    "SynthesizerAgent",
    "VerifierAgent",
    "VerifierVoter",
    "make_verifier_handler",
    "render_conclusion",
]
