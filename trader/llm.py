"""One door to every free AI model. Calls go down a fallback chain:
Groq -> Gemini -> Mistral -> OpenRouter -> local Ollama. A model that is out of
free quota, rate-limited or broken is skipped automatically, so the agents never
stop because of an AI limit. If every model is unavailable the call returns
None and the agents carry on with numbers only."""
import hashlib
import json
import re
import threading
import time
from datetime import datetime, timedelta, timezone

import requests

from . import config, db, log

L = log.get("llm")

# "localhost" costs ~2s per call on Windows (tries IPv6 first)
_OLLAMA = config.OLLAMA_HOST.rstrip("/").replace("//localhost", "//127.0.0.1")

# Self-imposed budgets sit just under each free tier, so we never hit the wall.
# Override any of them in .env, e.g. LLM_GEMINI_RPD=1000
PROVIDERS = {
    "groq": dict(url="https://api.groq.com/openai/v1/chat/completions", key=config.GROQ_API_KEY,
                 rpm=28, rpd=950, tpd=190_000, json_mode=True),
    "gemini": dict(url="https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
                   key=config.GEMINI_API_KEY, rpm=9, rpd=240, tpd=3_000_000, json_mode=True),
    "mistral": dict(url="https://api.mistral.ai/v1/chat/completions", key=config.MISTRAL_API_KEY,
                    rpm=50, rpd=5_000, tpd=20_000_000, json_mode=True),
    "openrouter": dict(url="https://openrouter.ai/api/v1/chat/completions", key=config.OPENROUTER_API_KEY,
                       rpm=18, rpd=45, tpd=10**9, json_mode=False),
    "ollama": dict(url=_OLLAMA + "/api/chat", key="local",
                   rpm=1000, rpd=10**9, tpd=10**12, json_mode=True),
}
for _n, _p in PROVIDERS.items():
    for _lim in ("rpm", "rpd", "tpd"):
        _p[_lim] = config._i(f"LLM_{_n.upper()}_{_lim.upper()}", _p[_lim])


def _parse_chain(s):
    out = []
    for part in s.split(","):
        part = part.strip()
        if ":" in part:
            prov, model = part.split(":", 1)
            if prov in PROVIDERS:
                out.append((prov, model))
    return out


CHAINS = {"smart": _parse_chain(config.LLM_CHAIN_SMART), "fast": _parse_chain(config.LLM_CHAIN_FAST)}

_lock = threading.Lock()
_ollama_lock = threading.Lock()
_cooldown = {}        # (provider, model) or provider -> unix time
_last_call = {}       # provider -> unix time
_cache = {}           # prompt hash -> (time, result)
_ollama_models = {"at": 0, "up": False}


def _today():
    return datetime.now(timezone.utc).date().isoformat()


def _usage(provider, model):
    r = db.one("SELECT requests, tokens FROM llm_usage WHERE day=? AND provider=? AND model=?",
               (_today(), provider, model))
    return (r["requests"], r["tokens"]) if r else (0, 0)


def _record(provider, model, tokens, error=False):
    db.execute(
        "INSERT INTO llm_usage VALUES (?,?,?,?,?,?) ON CONFLICT(day, provider, model) DO UPDATE SET "
        "requests=requests+excluded.requests, tokens=tokens+excluded.tokens, errors=errors+excluded.errors",
        (_today(), provider, model, 0 if error else 1, int(tokens), 1 if error else 0))


def _midnight_utc():
    t = datetime.now(timezone.utc)
    return (t.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).timestamp() + 60


def _ollama_has(model):
    """True if the local Ollama server is up. (A missing model shows up as an
    HTTP 404 on the actual call and is then skipped for 6h.)"""
    ttl = 60 if _ollama_models["up"] else 30
    if time.time() - _ollama_models["at"] > ttl:
        try:
            requests.get(_OLLAMA + "/api/tags", timeout=5).raise_for_status()
            _ollama_models["up"] = True
        except Exception:
            _ollama_models["up"] = False
        _ollama_models["at"] = time.time()
    return _ollama_models["up"]


def available(tier="smart") -> list:
    """Models in the chain that currently have a key and aren't cooling down."""
    out = []
    for prov, model in CHAINS.get(tier, []):
        p = PROVIDERS[prov]
        if not p["key"] or (prov == "ollama" and not _ollama_has(model)):
            continue
        if max(_cooldown.get(prov, 0), _cooldown.get((prov, model), 0)) > time.time():
            continue
        out.append(f"{prov}:{model}")
    return out


def extract_json(text):
    if not text:
        return None
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    a, b = text.find("{"), text.rfind("}")
    if a >= 0 and b > a:
        try:
            return json.loads(text[a:b + 1])
        except Exception:
            return None
    return None


def _call_openai_style(prov, model, messages, max_tokens, want_json):
    p = PROVIDERS[prov]
    body = {"model": model, "messages": messages, "temperature": 0.3, "max_tokens": max_tokens}
    extra = {}
    if prov == "groq" and "gpt-oss" in model:
        extra["reasoning_effort"] = "low"
        body["max_tokens"] = max_tokens + 1000      # reasoning tokens count toward the cap
    if prov == "gemini":
        extra["reasoning_effort"] = "low"
        body["max_tokens"] = max_tokens + 1500
    if want_json and p["json_mode"]:
        body["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {p['key']}", "Content-Type": "application/json"}
    if prov == "openrouter":
        headers["X-Title"] = "StocksBot"
    r = requests.post(p["url"], json={**body, **extra}, headers=headers, timeout=120)
    if r.status_code == 400 and extra:     # model doesn't accept the reasoning knob -> retry plain
        r = requests.post(p["url"], json=body, headers=headers, timeout=120)
    return r


def _call_ollama(model, messages, max_tokens, want_json):
    body = {"model": model, "messages": messages, "stream": False, "think": False,
            "options": {"temperature": 0.3, "num_ctx": 8192, "num_predict": max_tokens}}
    if want_json:
        body["format"] = "json"
    with _ollama_lock:                     # one local generation at a time (CPU heavy)
        return requests.post(PROVIDERS["ollama"]["url"], json=body, timeout=600)


def chat(system: str, user: str, tier="smart", max_tokens=1200, want_json=True, agent="", cache_min=30, local=True):
    """local=False skips the slow on-PC models - used for time-critical trade decisions, which then fall back to
    numbers-only instead of waiting minutes."""
    """Returns {"text", "json", "provider", "model"} or None if no model could answer."""
    if not config.LLM_ENABLED:
        return None
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    h = hashlib.sha1((tier + system + user).encode()).hexdigest()
    hit = _cache.get(h)
    if hit and time.time() - hit[0] < cache_min * 60:
        return hit[1]
    est = (len(system) + len(user)) // 3 + max_tokens

    for prov, model in CHAINS.get(tier, []):
        p = PROVIDERS[prov]
        if not p["key"] or (prov == "ollama" and not local):
            continue
        if prov == "ollama" and not _ollama_has(model):
            continue
        with _lock:
            if max(_cooldown.get(prov, 0), _cooldown.get((prov, model), 0)) > time.time():
                continue
            reqs, toks = _usage(prov, model)
            if reqs >= p["rpd"] or toks + est > p["tpd"]:
                continue
            wait = _last_call.get(prov, 0) + 60.0 / p["rpm"] - time.time()
            if wait > 15:
                continue
            if wait > 0:
                time.sleep(wait)
            _last_call[prov] = time.time()
        try:
            if prov == "ollama":
                r = _call_ollama(model, messages, max_tokens, want_json)
            else:
                r = _call_openai_style(prov, model, messages, max_tokens, want_json)
        except requests.RequestException as e:
            L.warning("%s:%s unreachable (%s) - trying next model", prov, model, type(e).__name__)
            _cooldown[(prov, model)] = time.time() + 120
            _record(prov, model, 0, error=True)
            continue

        if r.status_code != 200:
            body = r.text[:300].lower()
            _record(prov, model, 0, error=True)
            if r.status_code in (401, 403):
                _cooldown[prov] = time.time() + 6 * 3600
                db.event(agent or "llm", f"{prov} rejected the API key (HTTP {r.status_code}). Check .env.", "warning")
            elif r.status_code == 404 or "model" in body and ("not found" in body or "does not exist" in body
                                                             or "decommissioned" in body):
                _cooldown[(prov, model)] = time.time() + 6 * 3600
                db.event(agent or "llm", f"{prov}:{model} not available - skipping it for 6h", "warning")
            elif r.status_code == 429:
                daily = any(w in body for w in ("per day", "daily", "rpd", "tpd", "quota"))
                retry = float(r.headers.get("retry-after", 30) or 30)
                _cooldown[(prov, model)] = _midnight_utc() if daily and retry > 120 else time.time() + max(retry, 20)
            else:   # server error; a broken local model fails the same way every time
                _cooldown[(prov, model)] = time.time() + (1800 if prov == "ollama" else 120)
            L.info("%s:%s HTTP %s - falling back", prov, model, r.status_code)
            continue

        try:
            data = r.json()
            if prov == "ollama":
                text = data["message"]["content"]
                tokens = data.get("prompt_eval_count", 0) + data.get("eval_count", 0)
            else:
                text = data["choices"][0]["message"].get("content") or ""
                tokens = (data.get("usage") or {}).get("total_tokens", est)
        except Exception:
            _record(prov, model, 0, error=True)
            continue
        _record(prov, model, tokens)
        parsed = extract_json(text) if want_json else None
        if want_json and parsed is None:
            L.info("%s:%s returned unreadable JSON - trying next model", prov, model)
            continue
        result = {"text": text, "json": parsed, "provider": prov, "model": model}
        _cache[h] = (time.time(), result)
        if len(_cache) > 500:
            for k in sorted(_cache, key=lambda k: _cache[k][0])[:250]:
                _cache.pop(k, None)
        return result
    L.warning("No AI model available right now (all out of quota or offline) - continuing without AI")
    return None


def usage_summary(days=1) -> list:
    since = (datetime.now(timezone.utc).date() - timedelta(days=days - 1)).isoformat()
    return db.query("SELECT provider, model, SUM(requests) requests, SUM(tokens) tokens, SUM(errors) errors "
                    "FROM llm_usage WHERE day >= ? GROUP BY provider, model ORDER BY requests DESC", (since,))
