"""Provider-agnostic wrapper over whichever LLM API key is actually available.

Set LLM_PROVIDER=anthropic or LLM_PROVIDER=openai to force which one is used — otherwise this
auto-detects from whichever key env var is set, preferring Anthropic if both happen to be
present. Auto-detection is a footgun if a stale key lingers in your shell (whichever var is set,
even to a bad value, wins) — set LLM_PROVIDER explicitly once you know which one you're using.
Both providers offer the same two primitives this pipeline needs: a plain free-text call (Pass
A's unconstrained reasoning) and a schema-constrained structured-output call (guaranteed-valid
PolicyRule/CalculationRule JSON).
"""

import os

ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.6")


def provider() -> str:
    forced = os.environ.get("LLM_PROVIDER")
    if forced in ("anthropic", "openai"):
        return forced
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    raise RuntimeError(
        "Set ANTHROPIC_API_KEY or OPENAI_API_KEY (and optionally LLM_PROVIDER=anthropic|openai "
        "to force which one is used, e.g. if a stale key is lingering in your shell)."
    )


def free_text(system: str, user: str, max_tokens: int = 4096) -> str:
    """Unconstrained text generation — for Pass A's reasoning step."""
    if provider() == "anthropic":
        from anthropic import Anthropic
        response = Anthropic().messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(block.text for block in response.content if block.type == "text")
    else:
        from openai import OpenAI
        response = OpenAI().chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
        return response.choices[0].message.content


def structured(system: str, messages: list[dict], output_model, max_tokens: int = 4096):
    """Schema-constrained structured output. `messages` is a list of {"role", "content"} dicts
    (excluding the system message) — lets callers build up a multi-turn reflection conversation."""
    if provider() == "anthropic":
        from anthropic import Anthropic
        response = Anthropic().messages.parse(
            model=ANTHROPIC_MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
            output_format=output_model,
        )
        return response.parsed_output
    else:
        from openai import OpenAI
        completion = OpenAI().chat.completions.parse(
            model=OPENAI_MODEL,
            messages=[{"role": "system", "content": system}, *messages],
            response_format=output_model,
        )
        return completion.choices[0].message.parsed
