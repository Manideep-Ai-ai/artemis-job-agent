"""Tiny client for any OpenAI-compatible chat API (Gemini, Groq, Ollama, OpenAI...).

AI is optional everywhere in LeadHunter: every caller has a non-AI fallback.
"""
from __future__ import annotations

import json
import re

import httpx

from leadhunter.config import Config
from leadhunter.keyhealth import record, report_http


BUSY_FALLBACK = {"gemini": "gemini-flash-lite-latest"}  # tried once when the chosen model is overloaded


class AI:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.provider, self.base_url, self.model, self.key = cfg.ai_settings()

    @property
    def enabled(self) -> bool:
        if self.provider in ("", "none") or not self.base_url or not self.model:
            return False
        return self.provider == "ollama" or bool(self.key)

    def chat(self, system: str, user: str, temperature: float = 0.4, max_tokens: int = 900) -> str:
        if not self.enabled:
            raise RuntimeError("AI is not configured")
        headers = {"Content-Type": "application/json"}
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        url = f"{self.base_url.rstrip('/')}/chat/completions"
        try:
            r = httpx.post(url, json=body, headers=headers, timeout=45)
            busy = r.status_code in (429, 503)
        except httpx.TimeoutException:
            if self.provider not in BUSY_FALLBACK:
                raise
            busy = True
        if busy and self.provider in BUSY_FALLBACK:  # Gemini's main model gets "high demand" 503s at peak times
            r = httpx.post(url, json={**body, "model": BUSY_FALLBACK[self.provider]}, headers=headers, timeout=45)
        report_http(self.cfg, "ai", r.status_code, r.text)
        if r.status_code == 404 and "model" in r.text.lower():
            record(self.cfg, "ai", f"can't run: the model {self.model} is gone (retired by the provider). Pick another in setup")
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()

    def json(self, system: str, user: str, **kw) -> dict | list | None:
        """Ask for JSON and parse it leniently. Returns None on failure."""
        try:
            text = self.chat(system + "\nReply with valid JSON only, no prose.", user, temperature=0.1, **kw)
        except Exception:
            return None
        return parse_json_loose(text)


def parse_json_loose(text: str):
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"(\{.*\}|\[.*\])", text, flags=re.S)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                return None
    return None
