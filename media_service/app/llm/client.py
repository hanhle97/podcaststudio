"""LLM clients returning validated JSON.

claude_code: runs the Claude Code CLI headless (`claude -p`) with structured output
(--json-schema), no tools, in an empty working directory. Authenticates with a
subscription token from `claude setup-token` (env CLAUDE_CODE_OAUTH_TOKEN).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from typing import Any, Callable, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    pass


class LLMClient(Protocol):
    name: str
    model: str

    def complete_json(self, system: str, prompt: str, schema: dict[str, Any]) -> Any: ...


class ClaudeCodeClient:
    name = "claude_code"

    def __init__(self, model: str = "sonnet", timeout_s: float = 300, token: str = ""):
        self.model = model
        self.timeout_s = timeout_s
        self.token = token or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")

    def check(self) -> None:
        if not shutil.which("claude"):
            raise LLMError("Chưa cài Claude Code CLI trong container (build lại image).")
        if not self.token:
            raise LLMError("Chưa có CLAUDE_CODE_OAUTH_TOKEN. Chạy `claude setup-token` trong "
                           "container media-service rồi dán token vào .env.")

    def complete_json(self, system: str, prompt: str, schema: dict[str, Any]) -> Any:
        self.check()
        cmd = ["claude", "-p", "--output-format", "json", "--json-schema", json.dumps(schema),
               "--system-prompt", system, "--model", self.model, "--tools", "",
               "--strict-mcp-config", "--no-session-persistence"]
        env = {**os.environ, "CLAUDE_CODE_OAUTH_TOKEN": self.token}
        with tempfile.TemporaryDirectory(prefix="llm-") as cwd:
            try:
                proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                                      encoding="utf-8", errors="replace", cwd=cwd, env=env,
                                      timeout=self.timeout_s)
            except subprocess.TimeoutExpired as e:
                raise LLMError(f"Claude Code không trả lời sau {self.timeout_s:.0f}s.") from e
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError as e:
            raise LLMError(f"Claude Code trả về dữ liệu không đọc được: "
                           f"{(proc.stdout or proc.stderr)[:400]}") from e
        if data.get("is_error") or data.get("subtype", "success") != "success":
            reason = data.get("terminal_reason") or data.get("subtype")
            raise LLMError(f"Claude Code lỗi ({reason}): {str(data.get('result', ''))[:400]}")
        if data.get("structured_output") is not None:
            return data["structured_output"]
        return _json_from_text(str(data.get("result", "")))


def _json_from_text(text: str) -> Any:
    """Fallback: the first JSON object/array in a text answer."""
    start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
    if start < 0:
        raise LLMError(f"Không tìm thấy JSON trong câu trả lời: {text[:300]}")
    try:
        return json.JSONDecoder().raw_decode(text[start:])[0]
    except json.JSONDecodeError as e:
        raise LLMError(f"JSON trong câu trả lời bị hỏng: {e}") from e


def generate(client: LLMClient, system: str, prompt: str, model_cls: type[T],
             check: Callable[[T], None] | None = None, attempts: int = 3) -> tuple[T, int]:
    """Ask for JSON matching `model_cls`; retry with the validation errors as feedback.

    `check` may raise ValueError for rules Pydantic cannot express (e.g. timing).
    Returns (result, attempts used).
    """
    schema = model_cls.model_json_schema()
    feedback = ""
    last_error = ""
    for attempt in range(1, attempts + 1):
        raw = client.complete_json(system, prompt + feedback, schema)
        try:
            obj = model_cls.model_validate(raw)
            if check:
                check(obj)
            return obj, attempt
        except (ValidationError, ValueError) as e:
            last_error = str(e)
            log.warning("LLM output không hợp lệ (lần %d/%d): %s", attempt, attempts, last_error)
            feedback = (f"\n\nLần trả lời trước KHÔNG hợp lệ:\n{last_error}\n"
                        "Hãy trả lời lại, sửa đúng các lỗi trên.")
    raise LLMError(f"LLM trả về dữ liệu không hợp lệ sau {attempts} lần: {last_error[:500]}")


def get_client(settings: Any) -> LLMClient:
    if settings.llm_provider == "claude_code":
        return ClaudeCodeClient(settings.llm_model, settings.llm_timeout_s,
                                settings.claude_code_oauth_token)
    raise LLMError(f"LLM provider không hợp lệ: {settings.llm_provider}")
