"""Core data model. Everything a run produces is serialisable so it can be replayed."""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class Layer(str, Enum):
    """Where in the voice stack a failure originated. Assigned deterministically from the trace."""
    PERCEPTION = "PERCEPTION"  # the agent never heard the truth (ASR/channel)
    REASONING = "REASONING"    # the agent heard the truth but acted on something else
    POLICY = "POLICY"          # the agent did something it must never do
    LANGUAGE = "LANGUAGE"      # the agent answered in the wrong language/script
    TASK = "TASK"              # the conversation ended without the goal being met


class Severity(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class Truth(BaseModel):
    """Ground truth the caller holds. Oracles compare the agent's actions against this."""
    identity: Literal["customer", "third_party"] = "customer"
    dob: str = "1994-03-12"                 # ISO date the customer verifies with
    promise_amount: int = 4500             # rupees the caller commits to pay
    promise_date: str = "2026-10-15"       # ISO date of the promised payment
    wrong_amount: int | None = None        # said first, then corrected (self-correction mutation)


class Seed(BaseModel):
    """A base conversation goal. Mutations are layered on top of a seed."""
    id: str
    title: str
    lang: str                              # BCP-47, e.g. ta-IN
    persona: str
    truth: Truth


class ToolCall(BaseModel):
    name: str
    args: dict[str, Any]
    result: dict[str, Any] | None = None


class Turn(BaseModel):
    role: Literal["caller", "agent"]
    # caller turns
    said: str | None = None                # what the caller actually said (ground truth text)
    heard: str | None = None               # what the agent received after the channel (ASR output)
    heard_clean: str | None = None         # ASR of the clean audio: the validity control for entity turns
    audio_said: str | None = None          # path to clean TTS audio
    audio_heard: str | None = None         # path to degraded audio fed to ASR
    entity_slots: list[str] = Field(default_factory=list)  # which truth fields this turn carried
    # agent turns
    text: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    latency_ms: float | None = None


class Finding(BaseModel):
    """Structured, evidence-backed result of one oracle on one conversation."""
    oracle: str
    passed: bool
    severity: Severity
    layer: Layer | None = None
    expected: str
    actual: str
    evidence: list[str] = Field(default_factory=list)
    turn_index: int | None = None
    recommended_fix: str | None = None


class Conversation(BaseModel):
    case_id: str
    seed_id: str
    agent_version: str
    mutations: list[str]
    repetition: int = 0
    turns: list[Turn] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    error: str | None = None

    @property
    def passed(self) -> bool:
        return self.error is None and all(f.passed for f in self.findings)
