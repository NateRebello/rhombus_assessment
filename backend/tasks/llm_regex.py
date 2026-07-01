"""LLM-driven regex generation, transform specs, and PII classification. Results cached in Redis.

Every generated regex passes static ReDoS checks and a 1-second timeout test match before
reaching PySpark. normalize modes are allowlisted — arbitrary LLM strings never drive UDF dispatch.
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


def _cache_key(prompt: str) -> str:
    digest = hashlib.sha256(prompt.strip().lower().encode()).hexdigest()
    return f"regex_cache:{digest}"


def _spec_cache_key(prompt: str) -> str:
    digest = hashlib.sha256(prompt.strip().lower().encode()).hexdigest()
    return f"spec_cache:{digest}"


def _pii_cache_key(column_samples: dict) -> str:
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


_NESTED_QUANTIFIER_RE = re.compile(
    r"""
    (?<!\\)\(
    [^)]*
    [+*?{]
    [^)]*
    (?<!\\)\)
    \s*
    [+*?{]
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
    """Raised when a generated regex fails safety validation. Terminal — do not retry."""


def validate_regex(pattern: str) -> None:
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
\\d{4}3 means FIVE subscriber digits — phone numbers only have four.

CRITICAL — email domain / suffix (apex domain, no extra subdomain segment):
When the user names a specific domain (e.g. company.com, gmail.com), match
user@that-domain directly. Do NOT insert an extra [chars]+ or \\. before the
domain label — that only matches subdomains like user@mail.company.com.

Concrete examples (memorise these exactly):
  "domain is company.com"              →  [a-zA-Z0-9._%+-]+@company\\.com
  "emails ending with gmail.com"       →  [a-zA-Z0-9._%+-]+@gmail\\.com
  "email addresses ending in @foo.org" →  [a-zA-Z0-9._%+-]+@foo\\.org

WRONG for user@company.com (matches subdomains only, misses apex domain):
  [a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.company\\.com
  [a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+company\\.com"""

_DOMAIN_FROM_PROMPT_RE = re.compile(
    r"""
    (?:
        domain\s+is\s+ |
        ending\s+(?:with|in)\s+(?:@)? |
        emails?\s+at\s+
    )
    ([a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?)+)
    """,
    re.IGNORECASE | re.VERBOSE,
)

_TRAILING_DIGIT_RE = re.compile(
    r'^(.*?)\\d\{(\d+)\}([0-9])((?:\\b|\\Z|\$)?)$'
)
_STANDARD_DIGIT_COUNTS = frozenset({9, 10})


def _fix_trailing_digit_quantifier(pattern: str) -> str:
    """Correct LLM off-by-one where a trailing digit is appended after \\d{N} at end of pattern."""
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

    group_sum = sum(int(g) for g in all_groups)
    total_with_trailing = group_sum + 1

    if group_sum in _STANDARD_DIGIT_COUNTS and total_with_trailing not in _STANDARD_DIGIT_COUNTS:
        corrected = f"{prefix}\\d{{{n - 1}}}{digit}{anchor}"
        logger.info(
            "Trailing-digit quantifier fix: %r → %r (%d→%d digits)",
            pattern, corrected, total_with_trailing, group_sum,
        )
        return corrected

    return pattern


def _extract_domain_from_prompt(prompt: str) -> Optional[str]:
    m = _DOMAIN_FROM_PROMPT_RE.search(prompt)
    if m:
        return m.group(1).lower()
    m = re.search(
        r"@([a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?)+)",
        prompt,
        re.IGNORECASE,
    )
    return m.group(1).lower() if m else None


def _matches_apex_email(pattern: str, domain: str) -> bool:
    try:
        return bool(re.search(pattern, f"user@{domain}"))
    except re.error:
        return False


def _fix_email_domain_regex(pattern: str, prompt: str) -> str:
    """Correct LLM patterns that require a bogus subdomain before the named apex domain."""
    domain = _extract_domain_from_prompt(prompt)
    if not domain or _matches_apex_email(pattern, domain):
        return pattern

    dom_lit = domain.replace(".", r"\.")
    tail = rf"(?={dom_lit}(?:\\b|\\Z|\$))"
    corrected = re.sub(rf"(@)\[[^\]]+\]\+\\\.{tail}", r"\1", pattern)
    if corrected != pattern and _matches_apex_email(corrected, domain):
        logger.info("Email domain regex fix (subdomain): %r → %r", pattern, corrected)
        return corrected

    corrected = re.sub(rf"(@)\[[^\]]+\]\+{tail}", r"\1", pattern)
    if corrected != pattern and _matches_apex_email(corrected, domain):
        logger.info("Email domain regex fix (concat): %r → %r", pattern, corrected)
        return corrected

    if "@" in pattern and dom_lit in pattern:
        canonical = rf"[a-zA-Z0-9._%+-]+@{dom_lit}\b"
        if _matches_apex_email(canonical, domain):
            logger.info(
                "Email domain regex fallback: %r → %r (prompt domain %r)",
                pattern, canonical, domain,
            )
            return canonical

    return pattern


def _postprocess_regex(pattern: str, prompt: str) -> str:
    pattern = _fix_trailing_digit_quantifier(pattern)
    pattern = _fix_email_domain_regex(pattern, prompt)
    return pattern


def generate_regex(prompt: str) -> str:
    cache_k = _cache_key(prompt)
    cached = _redis_get(cache_k)
    if cached:
        logger.info("Regex cache hit for prompt (sha256 prefix %s…)", cache_k[:12])
        pattern = _postprocess_regex(cached, prompt)
        validate_regex(pattern)
        if pattern != cached:
            _redis_set(cache_k, pattern)
        return pattern

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
    pattern = _postprocess_regex(pattern, prompt)

    validate_regex(pattern)
    _redis_set(cache_k, pattern)
    return pattern


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

    if normalize not in ALLOWED_NORMALIZE:
        logger.warning("LLM returned unknown normalize %r; falling back to 'none'", normalize)
        normalize = "none"

    validate_regex(pattern)

    spec = {"pattern": pattern, "normalize": normalize}
    _redis_set(cache_key, json.dumps(spec))
    return spec


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
    if not column_samples:
        return []

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
