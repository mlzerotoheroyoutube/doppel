"""Model access by role, with a replay cache so a verdict can be reproduced.

Each role goes to a different model family on purpose, so no two stages share
blind spots:

    role         provider  model               job
    implementer  meta      muse-wonder-1.0     writes and reworks code (falls back to muse-spark-1.3)
    exam         meta      muse-spark-1.3      writes the hidden blind tests from the issue only
    planner      meta      muse-spark-1.3      plans the day from the user's targets and schedule
    judge        subq      subq-preview        judges every agent output: code, loop rounds, plans
    baseline     anthropic claude-opus-5-5     the "LLM reads the diff" reviewer we compare against

Override any role with RECEIPTS_ROLE_<ROLE>=anthropic|meta|subq.
Every call is cached under .receipts_cache/ by a hash of
(provider, model, effort, system, prompt).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

CACHE_DIR = Path(os.environ.get("RECEIPTS_CACHE", Path(__file__).resolve().parent.parent / ".receipts_cache"))

DEFAULT_ROLES = {"implementer": "meta", "exam": "meta", "planner": "meta",
                 "judge": "subq", "baseline": "anthropic"}

# Meta model per role, chosen by strength. Each entry is tried in order.
META_MODELS = {
    "implementer": [os.environ.get("RECEIPTS_META_CODER", "muse-wonder-1.0"), "muse-spark-1.3"],
    "exam": [os.environ.get("RECEIPTS_META_MODEL", "muse-spark-1.3")],
    "planner": [os.environ.get("RECEIPTS_META_MODEL", "muse-spark-1.3")],
}

PROVIDERS = {
    "anthropic": {"model": os.environ.get("RECEIPTS_MODEL", "claude-opus-5-5"), "label": "Claude Opus 5.5"},
    "meta": {"model": os.environ.get("RECEIPTS_META_MODEL", "muse-spark-1.3"), "label": "Meta",
             "base_url": "https://api.meta.ai/v1", "key_env": "MODEL_API_KEY"},
    "subq": {"model": os.environ.get("RECEIPTS_SUBQ_MODEL", "subq-preview"), "label": "SubQ",
             "base_url": "https://api.subq.ai/v1", "key_env": "SUBQ_API_KEY"},
}


class LLMError(RuntimeError):
    pass


# Tests can install a fake: fn(role, system, prompt) -> text. Never cached.
_FAKE = None


def install_fake(fn) -> None:
    global _FAKE
    _FAKE = fn


def provider_for(role: str) -> str:
    return os.environ.get(f"RECEIPTS_ROLE_{role.upper()}", DEFAULT_ROLES.get(role, "anthropic"))


def who(role: str) -> str:
    """Human-readable name of the model that plays a role, for reports."""
    p = provider_for(role)
    return f"{PROVIDERS[p]['label']} ({models_for(role)[0]})" if p == "meta" else PROVIDERS[p]["label"]


def _key(provider: str, model: str, system: str, prompt: str, effort: str) -> str:
    h = hashlib.sha256()
    for part in (provider, model, effort, system, prompt):
        h.update(part.encode())
        h.update(b"\0")
    return h.hexdigest()[:32]


# ---------------------------------------------------------------- providers

def _anthropic_sdk(system: str, prompt: str, effort: str) -> str:
    import anthropic

    client = anthropic.Anthropic()
    with client.beta.messages.stream(
        model=PROVIDERS["anthropic"]["model"],
        max_tokens=32000,
        system=system,
        thinking={"type": "adaptive"},
        output_config={"effort": effort},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        msg = stream.get_final_message()
    if msg.stop_reason == "refusal":
        raise LLMError("model declined the request")
    return "".join(b.text for b in msg.content if b.type == "text")


def _anthropic_cli(system: str, prompt: str, effort: str) -> str:
    exe = shutil.which("claude")
    if not exe:
        raise LLMError("no `claude` CLI found")
    proc = subprocess.run(
        [exe, "-p", "--model", PROVIDERS["anthropic"]["model"], "--append-system-prompt", system,
         "--tools", "", "--output-format", "text"],
        input=prompt, capture_output=True, text=True, timeout=600,
    )
    if proc.returncode != 0 or proc.stdout.startswith("Failed to authenticate"):
        raise LLMError(f"claude CLI failed: {(proc.stderr or proc.stdout).strip()[:300]}")
    return proc.stdout


def _anthropic(system: str, prompt: str, effort: str) -> str:
    errors = []
    for fn in (_anthropic_sdk, _anthropic_cli):
        try:
            return fn(system, prompt, effort)
        except Exception as e:  # noqa: BLE001 - try the next way in
            errors.append(str(e)[:300])
    raise LLMError("anthropic: " + " | ".join(errors))


def _openai_compatible(provider: str, model: str, system: str, prompt: str) -> str:
    from openai import OpenAI

    cfg = PROVIDERS[provider]
    key = os.environ.get(cfg["key_env"])
    if not key:
        raise LLMError(f"{provider}: set {cfg['key_env']}")
    client = OpenAI(base_url=cfg["base_url"], api_key=key, timeout=900, max_retries=2)
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
    )
    text = resp.choices[0].message.content or ""
    if not text.strip():
        raise LLMError(f"{provider}/{model}: empty reply")
    return text


def models_for(role: str) -> list[str]:
    provider = provider_for(role)
    if provider == "meta":
        return META_MODELS.get(role, [PROVIDERS["meta"]["model"]])
    return [PROVIDERS[provider]["model"]]


def _call(provider: str, model: str, system: str, prompt: str, effort: str) -> str:
    if provider == "anthropic":
        return _anthropic(system, prompt, effort)
    return _openai_compatible(provider, model, system, prompt)


# ---------------------------------------------------------------- public

def complete(system: str, prompt: str, effort: str = "high", role: str = "implementer") -> str:
    """Return the text answer for `role`, replaying from cache when possible."""
    provider = provider_for(role)
    models = models_for(role)
    path = CACHE_DIR / f"{_key(provider, models[0], system, prompt, effort)}.json"
    if path.exists():
        return json.loads(path.read_text())["text"]
    if _FAKE is not None:
        return _FAKE(role, system, prompt)
    errors, text, model = [], None, models[0]
    for model in models:  # e.g. Muse Wonder (preview) first, then Muse Spark
        try:
            text = _call(provider, model, system, prompt, effort)
            break
        except Exception as e:  # noqa: BLE001
            errors.append(f"{model}: {str(e)[:200]}")
    if text is None:
        raise LLMError("; ".join(errors))
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"provider": provider, "model": model, "role": role,
                                "effort": effort, "text": text}, indent=1))
    return text


def extract_block(text: str, lang: str = "python") -> str:
    """Pull the last fenced code block of the given language out of a reply."""
    fence = f"```{lang}"
    if fence in text:
        body = text.rsplit(fence, 1)[1]
        return body.split("```", 1)[0].strip() + "\n"
    if "```" in text:
        parts = text.split("```")
        if len(parts) >= 3:
            return parts[-2].split("\n", 1)[-1].strip() + "\n"
    return text.strip() + "\n"


def status() -> dict[str, str]:
    """Which roles have credentials, for a startup check."""
    out = {}
    for role in DEFAULT_ROLES:
        p = provider_for(role)
        if p == "anthropic":
            ok = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")) or "maybe (SDK profile or CLI)"
        else:
            ok = bool(os.environ.get(PROVIDERS[p]["key_env"]))
        out[role] = f"{PROVIDERS[p]['label']}: {'ready' if ok is True else ok or 'missing ' + PROVIDERS[p]['key_env']}"
    return out
