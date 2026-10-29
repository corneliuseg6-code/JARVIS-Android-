"""What the paid brains have cost, and the brake that stops them.

Every billed turn reports the tokens the provider says it used. This module
writes them to ``~/.jarvis/usage.json``, converts them to an estimated cost,
and answers one question before every paid call: *may this run?*

Three rules
-----------
**The provider's own count, never a guess.** Tokens come from the usage
block each API returns (``usage``, ``usageMetadata``). A turn whose stream
broke before reporting usage is recorded with what is known and flagged.

**Estimates say they are estimates.** Prices change and none of the three
APIs report money, only tokens. The table below is the published per-token
rate at the time of writing; the user can override any of them in settings,
and a model with no known price is charged at the most expensive known rate
for its provider, so an unknown model can only make the estimate too high.

**A limit is a hard stop.** When today's or this month's spend reaches a
limit, ``check()`` refuses, the paid call is not made, and Automatic routing
falls back to the free local model. Zero means "no limit".
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any

from . import config, paths, store

USAGE_FILE = paths.JARVIS_HOME / "usage.json"

#: US dollars per million tokens: (input, output). Published list prices for
#: standard (non-batch, non-cached) requests when this was written. They are
#: defaults, not truth: the user can override any of them, and the
#: interface links each provider's pricing page.
PRICES: dict[str, dict[str, tuple[float, float]]] = {
    "openai": {
        "gpt-4o-mini": (0.15, 0.60),
        "gpt-4o": (2.50, 10.00),
        "gpt-4.1": (2.00, 8.00),
        "gpt-4.1-mini": (0.40, 1.60),
        "gpt-4.1-nano": (0.10, 0.40),
        "gpt-5": (1.25, 10.00),
        "gpt-5-mini": (0.25, 2.00),
        "gpt-5-nano": (0.05, 0.40),
    },
    "claude": {
        "claude-opus-5-5": (4.00, 20.00),
        "claude-sonnet-5-5": (2.00, 10.00),
        "claude-haiku-5-5": (0.10, 0.50),
        "claude-fable-5-1": (10.00, 50.00),
    },
    "gemini": {
        "gemini-2.5-pro": (1.25, 10.00),
        "gemini-2.5-flash": (0.30, 2.50),
        "gemini-2.5-flash-lite": (0.10, 0.40),
    },
}

PRICING_PAGES = {
    "openai": "https://openai.com/api/pricing/",
    "claude": "https://www.anthropic.com/pricing#api",
    "gemini": "https://ai.google.dev/gemini-api/docs/pricing",
}

BILLED = tuple(PRICES)

_LOCK = threading.Lock()


class BudgetExceeded(RuntimeError):
    def __init__(self, provider: str, detail: str) -> None:
        super().__init__(detail)
        self.provider = provider
        self.detail = detail


# --------------------------------------------------------------------------
# prices
# --------------------------------------------------------------------------

def price_of(provider: str, model: str) -> dict[str, Any]:
    """{input, output, source} in USD per million tokens."""
    overrides = config.get_config().get("price_overrides") or {}
    custom = overrides.get(f"{provider}:{model}")
    if isinstance(custom, (list, tuple)) and len(custom) == 2:
        try:
            return {"input": float(custom[0]), "output": float(custom[1]), "source": "custom"}
        except (TypeError, ValueError):
            pass
    table = PRICES.get(provider, {})
    if model in table:
        pair = table[model]
        return {"input": pair[0], "output": pair[1], "source": "list"}
    # A dated or suffixed id ("gpt-4o-mini-2024-07-18") prices as its family.
    for known in sorted(table, key=len, reverse=True):
        if model.startswith(known + "-"):
            pair = table[known]
            return {"input": pair[0], "output": pair[1], "source": "list"}
    if not table:
        return {"input": 0.0, "output": 0.0, "source": "free"}
    worst_in = max(p[0] for p in table.values())
    worst_out = max(p[1] for p in table.values())
    return {"input": worst_in, "output": worst_out, "source": "unknown_model_worst_case"}


def cost_of(provider: str, model: str, input_tokens: int, output_tokens: int) -> float:
    price = price_of(provider, model)
    return (input_tokens * price["input"] + output_tokens * price["output"]) / 1_000_000


# --------------------------------------------------------------------------
# the ledger
# --------------------------------------------------------------------------

def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _load() -> dict[str, Any]:
    data = store.read_json(USAGE_FILE, {}) or {}
    data.setdefault("days", {})
    data.setdefault("recent", [])
    return data


def record(
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    *,
    purpose: str = "chat",
    complete: bool = True,
) -> dict[str, Any]:
    """Add one billed turn. Free providers are not recorded."""
    if provider not in BILLED:
        return {}
    input_tokens = max(0, int(input_tokens or 0))
    output_tokens = max(0, int(output_tokens or 0))
    cost = cost_of(provider, model, input_tokens, output_tokens)
    entry = {
        "at": int(time.time()),
        "provider": provider,
        "model": model,
        "input": input_tokens,
        "output": output_tokens,
        "usd": round(cost, 6),
        "purpose": purpose,
        "estimated_price": price_of(provider, model)["source"] != "list",
        "complete": bool(complete),
    }
    with _LOCK:
        data = _load()
        day = data["days"].setdefault(_today(), {})
        bucket = day.setdefault(provider, {"input": 0, "output": 0, "usd": 0.0, "calls": 0})
        bucket["input"] += input_tokens
        bucket["output"] += output_tokens
        bucket["usd"] = round(bucket["usd"] + cost, 6)
        bucket["calls"] += 1
        data["recent"] = (data["recent"] + [entry])[-50:]
        # Keep about a year of daily totals; older days only bloat the file.
        for key in sorted(data["days"])[:-400]:
            data["days"].pop(key, None)
        store.write_json(USAGE_FILE, data)
    return entry


def spent(provider: str | None = None, *, period: str = "day") -> float:
    """USD spent today or this month, for one provider or all of them."""
    data = _load()
    prefix = _today() if period == "day" else _month()
    total = 0.0
    for day, providers in data["days"].items():
        if not day.startswith(prefix):
            continue
        for name, bucket in providers.items():
            if provider is None or name == provider:
                total += float(bucket.get("usd") or 0.0)
    return round(total, 6)


def _limit(key: str) -> float:
    try:
        return max(0.0, float(config.get_config().get(key) or 0.0))
    except (TypeError, ValueError):
        return 0.0


def check(provider: str) -> None:
    """Raise BudgetExceeded if a paid call to `provider` may not run now."""
    if provider not in BILLED:
        return
    rules = (
        (f"{provider}_daily_limit_usd", spent(provider, period="day"), "today", provider),
        ("cost_daily_limit_usd", spent(None, period="day"), "today", "all providers"),
        ("cost_monthly_limit_usd", spent(None, period="month"), "this month", "all providers"),
    )
    for key, used, when, scope in rules:
        limit = _limit(key)
        if limit and used >= limit:
            raise BudgetExceeded(
                provider,
                f"The {when} limit of ${limit:.2f} for {scope} is reached "
                f"(estimated ${used:.4f} spent). Raise it in AI Connections → Costs, "
                "or use the free local model.",
            )


def allowed(provider: str) -> bool:
    try:
        check(provider)
    except BudgetExceeded:
        return False
    return True


def summary() -> dict[str, Any]:
    cfg = config.get_config()
    data = _load()
    return {
        "today": {p: spent(p, period="day") for p in BILLED},
        "month": {p: spent(p, period="month") for p in BILLED},
        "today_total": spent(None, period="day"),
        "month_total": spent(None, period="month"),
        "limits": {
            "daily": _limit("cost_daily_limit_usd"),
            "monthly": _limit("cost_monthly_limit_usd"),
            **{p: _limit(f"{p}_daily_limit_usd") for p in BILLED},
        },
        "max_output_tokens": int(cfg.get("max_output_tokens") or 0),
        "recent": list(reversed(data["recent"][-15:])),
        "pricing_pages": PRICING_PAGES,
        "prices": {
            p: {m: {"input": v[0], "output": v[1]} for m, v in table.items()}
            for p, table in PRICES.items()
        },
        "note": "Estimates from each provider's reported token counts and list "
                "prices; your provider's invoice is authoritative.",
    }


def reset() -> None:
    with _LOCK:
        store.write_json(USAGE_FILE, {"days": {}, "recent": []})
