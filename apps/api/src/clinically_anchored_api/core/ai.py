"""Thin wrapper around AWS Bedrock (Converse API) for model calls, plus a loader
for the versioned prompt templates in `templates/`.

What every call does, and nothing more:
  * runs in Bedrock `ca-central-1` -- fixed here rather than read from AWS_REGION,
    because keeping inference in Canada is a data-residency requirement and an
    ambient AWS_REGION must not be able to move it;
  * takes the caller's prompt-version string, sends it to Bedrock as request
    metadata, and returns it with the model id, so the caller can put both in the
    audit trail (this module does not write audit events or touch the database);
  * returns token usage parsed from the response;
  * checks a per-clinic daily call cap BEFORE calling Bedrock.

Credentials come from boto3's standard chain (AWS_ACCESS_KEY_ID /
AWS_SECRET_ACCESS_KEY in the process environment); there is no custom handling.
Prompt and reply text are never logged here: they can contain patient content.

The daily cap is in-memory, like core/ratelimit.py: per instance, reset by a
restart, and the day is a UTC day. Every attempt counts, including ones Bedrock
fails, so an error loop can't run up cost. Nothing calls this module yet.
"""

import re
import threading
from dataclasses import dataclass
from datetime import UTC, date, datetime
from functools import lru_cache
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from clinically_anchored_api.core.config import get_settings

BEDROCK_REGION = "ca-central-1"

# Bedrock accepts only a restricted character set in request metadata values.
_PROMPT_VERSION = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


class AIDailyCapExceeded(Exception):
    """The clinic has used its daily AI call allowance; Bedrock was not called."""

    def __init__(self, clinic_id: str, cap: int):
        self.clinic_id, self.cap = clinic_id, cap
        super().__init__(
            f"Daily AI call cap reached for clinic {clinic_id} ({cap} calls per day). "
            "No call was made; it resets at 00:00 UTC."
        )


class AIInvocationError(Exception):
    """Bedrock could not be called, or its response wasn't usable."""


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class AIResult:
    text: str
    model_id: str  # log these two in the audit trail
    prompt_version: str
    usage: TokenUsage
    stop_reason: str | None


# --- daily cap ----------------------------------------------------------------

_lock = threading.Lock()
_calls: dict[tuple[str, date], int] = {}


def _today() -> date:
    return datetime.now(UTC).date()


def reset() -> None:
    """Forget all counters (tests)."""
    with _lock:
        _calls.clear()


def _reserve_call(clinic_id: str, cap: int) -> None:
    """Count one call for the clinic today, or raise if the cap is spent. Counted
    before the Bedrock call so concurrent callers can't overshoot the cap."""
    today = _today()
    with _lock:
        for key in [k for k in _calls if k[1] != today]:
            del _calls[key]
        used = _calls.get((clinic_id, today), 0)
        if used >= max(cap, 0):
            raise AIDailyCapExceeded(clinic_id, max(cap, 0))
        _calls[(clinic_id, today)] = used + 1


# --- the call -----------------------------------------------------------------


@lru_cache
def _client():
    return boto3.client(
        "bedrock-runtime",
        region_name=BEDROCK_REGION,
        config=Config(retries={"max_attempts": 2, "mode": "standard"}, read_timeout=60),
    )


def generate(
    *,
    clinic_id: str,
    prompt_version: str,
    system: str,
    prompt: str,
    max_tokens: int = 1024,
    temperature: float = 0.2,
) -> AIResult:
    """One Converse call. Raises AIDailyCapExceeded (without calling Bedrock) when
    the clinic is over its cap, and AIInvocationError if the call or its response
    fails. `prompt_version` identifies the template/wording used, e.g.
    "draft_reply_v1" (`PromptTemplate.prompt_version`)."""
    if not _PROMPT_VERSION.match(prompt_version):
        raise ValueError(f"Invalid prompt_version {prompt_version!r}.")
    settings = get_settings()
    model_id = settings.bedrock_model_id

    _reserve_call(clinic_id, settings.ai_daily_call_cap_per_clinic)

    try:
        response = _client().converse(
            modelId=model_id,
            system=[{"text": system}],
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": max_tokens, "temperature": temperature},
            requestMetadata={"prompt_version": prompt_version, "model_id": model_id},
        )
    except (BotoCoreError, ClientError) as exc:
        raise AIInvocationError(f"Bedrock call failed: {exc}") from exc

    try:
        blocks = response["output"]["message"]["content"]
        usage = response["usage"]
        return AIResult(
            text="".join(b["text"] for b in blocks if "text" in b),
            model_id=model_id,
            prompt_version=prompt_version,
            usage=TokenUsage(
                input_tokens=int(usage["inputTokens"]), output_tokens=int(usage["outputTokens"])
            ),
            stop_reason=response.get("stopReason"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise AIInvocationError(f"Unexpected Bedrock response shape: {exc!r}") from exc


# --- prompt templates ---------------------------------------------------------

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
_VERSION = re.compile(r"^v[0-9]+$")
_PLACEHOLDER = re.compile(r"\{\{\s*([a-z][a-z0-9_]*)\s*\}\}")


class TemplateNotFound(Exception):
    pass


class TemplateError(Exception):
    """A template is malformed, or render() got the wrong variables."""


@dataclass(frozen=True)
class PromptTemplate:
    name: str
    version: str
    system: str  # raw text, `{{placeholders}}` intact
    user: str

    @property
    def prompt_version(self) -> str:
        """The identifier to pass to generate() and log: the template's file stem."""
        return f"{self.name}_{self.version}"

    @property
    def placeholders(self) -> frozenset[str]:
        return frozenset(_PLACEHOLDER.findall(self.system) + _PLACEHOLDER.findall(self.user))

    def render(self, **variables: str) -> tuple[str, str]:
        """Fill the placeholders; returns (system, user). Strict both ways -- a
        missing or unknown variable is an error, so a typo can't silently send a
        prompt with a hole in it."""
        missing = self.placeholders - variables.keys()
        unknown = variables.keys() - self.placeholders
        if missing or unknown:
            raise TemplateError(
                f"{self.prompt_version}: missing {sorted(missing)}, unknown {sorted(unknown)}"
            )

        def fill(text: str) -> str:
            return _PLACEHOLDER.sub(lambda m: str(variables[m.group(1)]), text)

        return fill(self.system), fill(self.user)


def load_template(name: str, version: str) -> PromptTemplate:
    """Read `templates/<name>_<version>.md`, e.g. load_template("draft_reply", "v1")."""
    if not _NAME.match(name) or not _VERSION.match(version):
        raise TemplateNotFound(f"Invalid template name/version: {name!r} {version!r}")
    path = TEMPLATES_DIR / f"{name}_{version}.md"
    if not path.is_file():
        raise TemplateNotFound(f"No template {name}_{version}.md")
    text = re.sub(r"<!--.*?-->", "", path.read_text(encoding="utf-8"), flags=re.DOTALL)
    parts = re.split(r"^## (System|User)[ \t]*$", text, flags=re.MULTILINE)
    # parts = [preamble, "System", body, "User", body] when well formed.
    if len(parts) != 5 or parts[1] != "System" or parts[3] != "User":
        raise TemplateError(f"{path.name} must have a '## System' then a '## User' section")
    system, user = parts[2].strip(), parts[4].strip()
    return PromptTemplate(name=name, version=version, system=system, user=user)
