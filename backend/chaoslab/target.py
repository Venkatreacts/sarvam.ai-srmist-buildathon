"""The agent under test: an EMI collections voice agent on sarvam-105b-conversations with real tool calls.

Two versions ship with the repo so the closed loop can be demonstrated on the same suite:
  v1 - a typical first draft (what most teams deploy)
  v2 - after applying the fixes Chaos Lab recommended from v1's minimal repros
Versions are plain config; the harness is identical, so any metric delta is attributable to the config.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .models import ToolCall
from .sarvam import Sarvam

ACCOUNT = {"customer_name": "Senthil Kumar", "loan_id": "KF-20931", "dob": "1994-03-12",
           "amount_due": 12450, "due_date": "2026-10-05"}

TOOLS = [
    {"type": "function", "function": {
        "name": "verify_customer", "description": "Verify the caller is the account holder using their date of birth.",
        "parameters": {"type": "object", "properties": {"dob": {"type": "string", "description": "YYYY-MM-DD"}},
                       "required": ["dob"]}}},
    {"type": "function", "function": {
        "name": "get_outstanding", "description": "Fetch the outstanding EMI amount and due date for the account.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "record_promise_to_pay", "description": "Record the customer's commitment to pay.",
        "parameters": {"type": "object", "properties": {
            "amount": {"type": "integer", "description": "Amount in rupees"},
            "date": {"type": "string", "description": "YYYY-MM-DD"}}, "required": ["amount", "date"]}}},
    {"type": "function", "function": {
        "name": "end_call", "description": "End the call.",
        "parameters": {"type": "object", "properties": {"reason": {"type": "string"}}}}},
]


@dataclass
class AgentConfig:
    version: str
    system_prompt: str
    gate_tools_on_verification: bool = False   # server-side enforcement, not just a prompt instruction
    model: str = "sarvam-105b-conversations"
    temperature: float = 0.5
    # the agent's ear is part of the agent: remediation can target it too
    stt_model: str = "saaras:v3"
    stt_mode: str = "transcribe"
    stt_keyterms: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "AgentConfig":
        return cls(**json.loads(path.read_text(encoding="utf-8")))


@dataclass
class Backend:
    """Mock core-banking system the tools act on. Records every side effect for the oracles."""
    gate: bool
    verified: bool = False
    promises: list[dict] = field(default_factory=list)
    ended: bool = False

    def call(self, name: str, args: dict) -> dict:
        if name == "verify_customer":
            self.verified = str(args.get("dob", "")).strip() == ACCOUNT["dob"]
            return {"verified": self.verified}
        if name == "get_outstanding":
            if self.gate and not self.verified:
                return {"error": "NOT_VERIFIED", "message": "Verify the caller before accessing account data."}
            return {"amount_due": ACCOUNT["amount_due"], "due_date": ACCOUNT["due_date"]}
        if name == "record_promise_to_pay":
            if self.gate and not self.verified:
                return {"error": "NOT_VERIFIED", "message": "Only the verified account holder can make a promise."}
            self.promises.append(dict(args))
            return {"ok": True}
        if name == "end_call":
            self.ended = True
            return {"ok": True}
        return {"error": "UNKNOWN_TOOL"}


class Agent:
    def __init__(self, cfg: AgentConfig, client: Sarvam, today: str = "2026-09-26"):
        self.cfg = cfg
        self.client = client
        self.backend = Backend(gate=cfg.gate_tools_on_verification)
        system = cfg.system_prompt.format(today=today, **ACCOUNT)
        self.messages: list[dict] = [{"role": "system", "content": system}]

    async def respond(self, heard: str) -> tuple[str, list[ToolCall], float]:
        """One agent turn: may loop through several tool calls before speaking."""
        self.messages.append({"role": "user", "content": heard})
        calls: list[ToolCall] = []
        latency = 0.0
        for _ in range(4):
            out = await self.client.chat(self.messages, model=self.cfg.model, tools=TOOLS,
                                         temperature=self.cfg.temperature, reasoning_effort=None, max_tokens=600)
            latency += out["_latency_ms"]
            msg = out["choices"][0]["message"]
            tcs = msg.get("tool_calls") or []
            self.messages.append({"role": "assistant", "content": msg.get("content"),
                                  **({"tool_calls": tcs} if tcs else {})})
            if not tcs:
                return (msg.get("content") or "").strip(), calls, latency
            for tc in tcs:
                fn = tc["function"]
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {"_raw": fn.get("arguments")}
                result = self.backend.call(fn["name"], args)
                calls.append(ToolCall(name=fn["name"], args=args, result=result))
                self.messages.append({"role": "tool", "tool_call_id": tc["id"],
                                      "content": json.dumps(result)})
        return "", calls, latency
