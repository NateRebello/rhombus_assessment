"""
Natural-language → regex generation via LLM, with Redis caching and safety
validation.  Now also provides:

  generate_transform_spec()  — returns {"pattern": ..., "normalize": ...} for
                               standardization jobs (E.164 / ISO 8601).
  classify_columns()         — samples a few rows per column, calls the LLM
                               once to classify PII type + suggest a prompt,
                               for the pre-submit suggestion feature.

Safety model (unchanged):
  Two-layer defence before any regex reaches PySpark:
    1. Static check — reject obvious ReDoS shapes (nested quantifiers).
    2. Timeout-bound test match — run a 1-second re.match against a 10 KB
       string of 'a' chars.
"""
import hashlib
import json
import logging
import re
import threading
from typing import Optional

import redis as redis_lib
from django.conf import settings
from openai import OpenAI, APITimeoutError, APIConnectionError

logger = logging.getLogger(__name__)

# ── Redis client (module-level singleton, thread-safe) ────────────────────────
_redis_client: Optional[redis_lib.Redis] = None


def _get_redis() -> redis_lib.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis_lib.from_url(settings.REDIS_CACHE_URL, decode_responses=True)
    return _redis_client


def _openai_client() -> OpenAI:
    return OpenAI(
        api_key=settings.OPENAI_API_KEY,
        timeout=settings.LLM_TIMEOUT_SECONDS,
    )


# ── Cache helpers ─────────────────────────────────────────────────────────────

def _cache_key(prompt: str) -> str:
    """Deterministic cache key for a regex prompt (sha256, fixed length)."""
    digest = hashlib.sha256(prompt.strip().lower().encode()).hexdigest()
    return f"regex_cache:{digest}"


def _spec_cache_key(prompt: str) -> str:
    """Separate namespace for transform-spec JSON payloads."""
    digest = hashlib.sha256(prompt.strip().lower().encode()).hexdigest()
    return f"spec_cache:{digest}"


def _pii_cache_key(column_samples: dict) -> str:
    """Cache key for PII classification results, keyed on column name + samples."""
    payload = json.dumps(
        {k: sorted(v[:5]) for k, v in sorted(column_samples.items())},
        sort_keys=True,
    )
    digest = hashlib.sha256(payload.encode()).hexdigest()
    return f"pii_suggest:{digest}"


def _redis_get(key: str) -> Optional[str]:
    try:
        return _get_redis().get(key)
    except Exception as exc:
        logger.warning("Redis GET failed (%s): %s", key[:20], exc)
        return None


def _redis_set(key: str, value: str) -> None:
    try:
        _get_redis().setex(key, settings.REGEX_CACHE_TTL_SECONDS, value)
    except Exception as exc:
        logger.warning("Redis SET failed (non-fatal): %s", exc)


# ── Regex safety validation ───────────────────────────────────────────────────

_NESTED_QUANTIFIER_RE = re.compile(
    r"""
    (?<!\\)\(   # unescaped opening group (not \( which is a literal paren)
    [^)]*       # any content
    [+*?{]      # inner quantifier
    [^)]*
    (?<!\\)\)   # unescaped closing group (not \) which is a literal paren)
    \s*
    [+*?{]      # outer quantifier
    """,
    re.VERBOSE,
)

_MAX_REGEX_LENGTH = 512
_REDOS_TEST_STRING = "a" * 10_000
_MATCH_TIMEOUT_SECONDS = 1


def _timeout_match(pattern: str) -> bool:
    result: dict = {"timed_out": True}

    def _run() -> None:
        try:
            re.match(pattern, _REDOS_TEST_STRING, re.DOTALL)
            result["timed_out"] = False
        except re.error:
            result["timed_out"] = False

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=_MATCH_TIMEOUT_SECONDS)
    return not result["timed_out"]


class RegexSafetyError(ValueError):
    """Raised when a generated regex fails safety validation.  Terminal — do not retry."""


def validate_regex(pattern: str) -> None:
    """Raise RegexSafetyError if the pattern is unsafe or malformed."""
    if len(pattern) > _MAX_REGEX_LENGTH:
        raise RegexSafetyError(
            f"Regex exceeds max length ({len(pattern)} > {_MAX_REGEX_LENGTH})"
        )
    try:
        re.compile(pattern)
    except re.error as exc:
        raise RegexSafetyError(f"Regex compile error: {exc}") from exc

    if _NESTED_QUANTIFIER_RE.search(pattern):
        raise RegexSafetyError(
            "Regex contains nested quantifiers (ReDoS risk): " + pattern
        )
    if _timeout_match(pattern):
        return
    raise RegexSafetyError(
        f"Regex timed out in test match ({_MATCH_TIMEOUT_SECONDS}s limit): " + pattern
    )


# ── Feature 1: plain regex generation (unchanged) ────────────────────────────

_SYSTEM_PROMPT = """You are a regex generation assistant.
Given a natural-language description, respond with a SINGLE Python-compatible
regular expression on its own line — no explanation, no markdown, no code fences.
The regex must be safe to use with Python's re module and Java's java.util.regex
(for PySpark compatibility).  Do not include capturing groups unless essential.

CRITICAL — specific ending digit in a structured fixed-length number:
The ending digit IS one of the digits already counted in the number.
To include it, reduce the last \\d{N} by 1 and append the digit explicitly.

Concrete examples (memorise these exactly):
  "phone numbers ending in 3"  →  \\d{3}[-.\\s]?\\d{3}[-.\\s]?\\d{3}3
  "phone numbers ending in 7"  →  \\d{3}[-.\\s]?\\d{3}[-.\\s]?\\d{3}7
  "SSN ending in 9"            →  \\d{3}-?\\d{2}-?\\d{3}9
  "5-digit zip ending in 3"    →  \\d{4}3

DO NOT write \\d{4}3 for the last group of a phone number.
\\d{4}3 means FIVE subscriber digits — phone numbers only have four."""


# ── Trailing-digit quantifier correction ──────────────────────────────────────
# The LLM reliably makes this mistake: given "phone numbers ending in 3" it
# generates \d{3}[-.\s]?\d{3}[-.\s]?\d{4}3 — appending 3 AFTER the standard
# last group, producing an 11-digit pattern instead of 10.
#
# The fix: if the total explicit digit count in the regex is exactly ONE MORE
# THAN a well-known structured-number length (10 for US phone, 9 for SSN) AND
# the corrected count IS that standard length, reduce the trailing \d{N} by 1.
#
# Guarded against over-correction: if the total already IS a standard length
# (e.g. already 10 for phone), we leave it alone.

_TRAILING_DIGIT_RE = re.compile(
    r'^(.*?)\\d\{(\d+)\}([0-9])((?:\\b|\\Z|\$)?)$'
)

# Standard digit counts for common structured numbers.
# Only apply the fix when the corrected total is one of these.
_STANDARD_DIGIT_COUNTS = frozenset({9, 10})   # SSN=9, US phone=10


def _fix_trailing_digit_quantifier(pattern: str) -> str:
    """
    Correct the LLM off-by-one for structured-number trailing-digit patterns.

    \\b\\d{3}[-.\\s]?\\d{3}[-.\\s]?\\d{4}3\\b  →  \\b\\d{3}[-.\\s]?\\d{3}[-.\\s]?\\d{3}3\\b

    Logic:
      1. Must end with \\d{N}<digit>[optional anchor].
      2. Must have ≥2 \\d{} groups (structured number — not a bare \\d{4}3 zip).
      3. N must be ≥ 2 (never reduces \\d{1}).
      4. Only fires if (sum_of_all_groups + 1) is NOT a standard length but
         sum_of_all_groups IS — i.e. the trailing digit pushed it over by 1.
    """
    m = _TRAILING_DIGIT_RE.match(pattern)
    if not m:
        return pattern
    prefix, n_str, digit, anchor = m.groups()
    n = int(n_str)
    if n < 2:
        return pattern

    all_groups = re.findall(r'\\d\{(\d+)\}', pattern)
    if len(all_groups) < 2:
        return pattern

    group_sum = sum(int(g) for g in all_groups)   # digits already in \d{} groups
    total_with_trailing = group_sum + 1            # +1 for the appended literal digit

    # Apply only when: removing the +1 lands on a standard length AND
    # the current total is NOT itself a standard length (avoids double-fix).
    if group_sum in _STANDARD_DIGIT_COUNTS and total_with_trailing not in _STANDARD_DIGIT_COUNTS:
        corrected = f"{prefix}\\d{{{n - 1}}}{digit}{anchor}"
        logger.info(
            "Trailing-digit quantifier fix: %r → %r (%d→%d digits)",
            pattern, corrected, total_with_trailing, group_sum,
        )
        return corrected

    return pattern


def generate_regex(prompt: str) -> str:
    """
    Return a validated regex string for the given natural-language prompt.

    Cache hit path (fast):   Redis lookup → validate → return
    Cache miss path (slow):  LLM call → fix → validate → write cache → return

    Raises:
        RegexSafetyError   – bad/unsafe regex (terminal, do not retry)
        APITimeoutError    – LLM request timed out (retryable)
        APIConnectionError – LLM unreachable (retryable)
    """
    cached = _redis_get(_cache_key(prompt))
    if cached:
        logger.info("Regex cache hit for prompt (sha256 prefix %s…)", _cache_key(prompt)[:12])
        validate_regex(cached)
        return cached

    logger.info("Regex cache miss — calling LLM for prompt: %.80s…", prompt)
    response = _openai_client().chat.completions.create(
        model=settings.LLM_MODEL,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0,
        max_tokens=256,
    )

    raw = response.choices[0].message.content or ""
    pattern = raw.strip().strip("`").strip()

    # Programmatic safety net: correct trailing-digit off-by-one before
    # validation and caching so the fix propagates to all future cache hits.
    pattern = _fix_trailing_digit_quantifier(pattern)

    validate_regex(pattern)
    _redis_set(_cache_key(prompt), pattern)
    return pattern


# ── Feature 1: transform spec (regex + normalize mode) ───────────────────────

# Hard allowlist.  LLM-returned normalize values are ALWAYS checked against
# this set before being used to select a Spark UDF.  An arbitrary string from
# the LLM must never drive code execution.
ALLOWED_NORMALIZE = frozenset({"none", "e164", "iso8601"})

_TRANSFORM_SYSTEM_PROMPT = """You are a regex + normalization spec assistant.
Given a natural-language description of a data pattern, respond with EXACTLY
two lines — no markdown, no quotes around the values, no explanation:

PATTERN: <regex>
NORMALIZE: <mode>

Where:
- <regex>: a Python/Java-compatible regex pattern, written as plain text with
  no surrounding quotes and no escaping of backslashes beyond what the regex
  itself requires.  It must be safe to compile directly with Python's re module.
- <mode>: EXACTLY one of:
    e164    — matched value is a phone number; normalize to E.164 (+15551234567)
    iso8601 — matched value is a date; normalize to YYYY-MM-DD
    none    — literal replacement (no normalization needed)

Example output (for "find US phone numbers"):
PATTERN: \+?1?\s*\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}
NORMALIZE: e164"""


def generate_transform_spec(prompt: str) -> dict:
    """
    Return {"pattern": <regex>, "normalize": <mode>} for the given prompt.

    Uses a line-based response format ("PATTERN: ..." / "NORMALIZE: ...") to
    avoid JSON/backslash escaping ambiguity — the LLM reliably produces Python
    raw-string notation when asked for JSON, which breaks json.loads().

    The normalize value is validated against ALLOWED_NORMALIZE before return.
    Caches the full spec so cache hits also preserve the normalize mode.

    Raises:
        RegexSafetyError   – bad/unsafe regex or unrecognised normalize value
        APITimeoutError    – LLM request timed out (retryable)
        APIConnectionError – LLM unreachable (retryable)
    """
    cache_key = _spec_cache_key(prompt)
    cached = _redis_get(cache_key)
    if cached:
        logger.info("Transform spec cache hit for prompt (prefix %s…)", cache_key[:12])
        spec = json.loads(cached)
        validate_regex(spec["pattern"])
        return spec

    logger.info("Transform spec cache miss — calling LLM for prompt: %.80s…", prompt)
    response = _openai_client().chat.completions.create(
        model=settings.LLM_MODEL,
        messages=[
            {"role": "system", "content": _TRANSFORM_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0,
        max_tokens=256,
    )

    raw = (response.choices[0].message.content or "").strip()

    # Parse the two-line response: "PATTERN: ..." / "NORMALIZE: ..."
    pattern: Optional[str] = None
    normalize: str = "none"
    for line in raw.splitlines():
        line = line.strip()
        if line.upper().startswith("PATTERN:"):
            pattern = line[len("PATTERN:"):].strip().strip("`").strip()
        elif line.upper().startswith("NORMALIZE:"):
            normalize = line[len("NORMALIZE:"):].strip().lower()

    if not pattern:
        raise RegexSafetyError(
            f"LLM did not return a PATTERN line. Raw response: {raw[:300]}"
        )

    # Allowlist check — never let an arbitrary LLM string reach Spark UDF dispatch.
    if normalize not in ALLOWED_NORMALIZE:
        logger.warning("LLM returned unknown normalize %r; falling back to 'none'", normalize)
        normalize = "none"

    validate_regex(pattern)

    spec = {"pattern": pattern, "normalize": normalize}
    _redis_set(cache_key, json.dumps(spec))
    return spec


# ── Feature 2: PII classification + prompt suggestion ────────────────────────

ALLOWED_PII_TYPES = frozenset({
    "email", "phone", "ssn", "credit_card", "date",
    "name", "ip_address", "url", "none",
})

_CLASSIFY_SYSTEM_PROMPT = """You are a PII detection assistant.
Given a JSON object mapping column names to lists of sample values, classify
each column for likely PII type and suggest a short natural-language processing
prompt.  Respond with ONLY a JSON array — no markdown, no explanation:

[
  {
    "column": "<column_name>",
    "pii_type": "<type>",
    "confidence": <0.0-1.0>,
    "suggested_prompt": "<short NL description for the regex generator>"
  },
  ...
]

pii_type must be exactly one of:
  "email", "phone", "ssn", "credit_card", "date", "name", "ip_address", "url", "none"

Only include columns with confidence >= 0.6.  Skip columns whose samples look
like plain IDs, numbers, or free text with no PII pattern.
suggested_prompt should be a short phrase like "find email addresses" or
"find US phone numbers" — something a regex generator can act on directly."""


def classify_columns(column_samples: dict) -> list:
    """
    Classify each column for likely PII type using a single batched LLM call.

    Args:
        column_samples: {"column_name": ["val1", "val2", ...], ...}
                        Pass up to ~10 non-null sample values per column.

    Returns:
        List of dicts:
        [{"column": "email", "pii_type": "email", "confidence": 0.95,
          "suggested_prompt": "find email addresses"}, ...]

    Raises:
        APITimeoutError    – LLM request timed out (retryable by caller)
        APIConnectionError – LLM unreachable (retryable by caller)
    """
    if not column_samples:
        return []

    # Trim to 8 samples per column to keep the prompt small.
    trimmed = {col: vals[:8] for col, vals in column_samples.items() if vals}

    cache_key = _pii_cache_key(trimmed)
    cached = _redis_get(cache_key)
    if cached:
        logger.info("PII classify cache hit (%s…)", cache_key[:16])
        return json.loads(cached)

    logger.info("PII classify cache miss — calling LLM for %d columns", len(trimmed))
    payload = json.dumps(trimmed, ensure_ascii=False)

    response = _openai_client().chat.completions.create(
        model=settings.LLM_MODEL,
        messages=[
            {"role": "system", "content": _CLASSIFY_SYSTEM_PROMPT},
            {"role": "user", "content": payload},
        ],
        temperature=0,
        max_tokens=1024,
    )

    raw = (response.choices[0].message.content or "").strip().strip("`").strip()

    try:
        suggestions = json.loads(raw)
        if not isinstance(suggestions, list):
            raise ValueError("Expected a JSON array")
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("PII classify LLM returned non-JSON: %s — %s", exc, raw[:200])
        return []

    # Sanitise: only keep dicts with the required keys and allowed pii_type values.
    clean = []
    for item in suggestions:
        if not isinstance(item, dict):
            continue
        pii_type = str(item.get("pii_type", "none")).lower()
        if pii_type not in ALLOWED_PII_TYPES:
            pii_type = "none"
        confidence = float(item.get("confidence", 0.0))
        if confidence < 0.6:
            continue
        clean.append({
            "column": str(item.get("column", "")),
            "pii_type": pii_type,
            "confidence": round(confidence, 2),
            "suggested_prompt": str(item.get("suggested_prompt", "")),
        })

    _redis_set(cache_key, json.dumps(clean))
    return clean
