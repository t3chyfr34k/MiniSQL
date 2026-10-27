"""SQL generation with Ollama, Gemini, and OpenAI-compatible cloud fallback."""
import json
import os
import urllib.request
import urllib.error


class ProviderError(RuntimeError):
    pass


SYSTEM_PROMPT = """You generate SQL Server T-SQL for a human to review.
Rules:
- Use only tables and columns in the supplied schema.
- Output SQL only, with no Markdown fences or explanatory prose.
- Never execute anything.
- Default to SELECT queries. If the user requests INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE, MERGE, or other changes, generate the requested SQL but add a SQL comment warning at the top that it changes data/schema and must be reviewed.
- Never use GO in generated output.
- Never invent schema objects. If required information is missing, output a SQL comment explaining the missing schema details.
- Prefer explicit column names over SELECT *.
- Use T-SQL syntax, not MySQL LIMIT.
"""


def _post_json(url, payload, headers=None, timeout=45):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={
        "Content-Type": "application/json",
        **(headers or {}),
    }, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1000]
        raise ProviderError(f"HTTP {exc.code}: {detail}") from exc
    except Exception as exc:
        raise ProviderError(str(exc)) from exc


def _ollama(question, schema, config):
    base = config.get("ollama", "base_url", fallback="http://localhost:11434").rstrip("/")
    model = config.get("ollama", "model", fallback="qwen2.5:3b")
    result = _post_json(
        base + "/api/chat",
        {
            "model": model,
            "stream": False,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"SCHEMA:\n{schema}\n\nREQUEST:\n{question}"},
            ],
            "options": {"temperature": 0.1},
        },
        timeout=90,
    )
    return result["message"]["content"].strip(), f"Ollama ({model})"


def _cloud(question, schema, config):
    base = config.get("cloud", "base_url", fallback="https://api.openai.com/v1").rstrip("/")
    model = config.get("cloud", "model", fallback="gpt-4o-mini")
    api_key = config.get("cloud", "api_key", fallback="").strip()
    if not api_key:
        raise ProviderError("Cloud fallback is not configured. Add an API key to config.ini.")
    result = _post_json(
        base + "/chat/completions",
        {
            "model": model,
            "temperature": 0.1,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"SCHEMA:\n{schema}\n\nREQUEST:\n{question}"},
            ],
        },
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=60,
    )
    return result["choices"][0]["message"]["content"].strip(), f"Cloud ({model})"


def _gemini(question, schema, config):
    base = config.get("gemini", "base_url", fallback="https://generativelanguage.googleapis.com/v1beta").rstrip("/")
    model = config.get("gemini", "model", fallback="gemini-2.5-flash").strip()
    api_key = config.get("gemini", "api_key", fallback="").strip() or os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise ProviderError("Gemini is not configured. Set gemini.api_key in config.ini or GEMINI_API_KEY.")
    result = _post_json(
        f"{base}/models/{model}:generateContent",
        {
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": [{"role": "user", "parts": [{"text": f"SCHEMA:\n{schema}\n\nREQUEST:\n{question}"}]}],
            "generationConfig": {"temperature": 0.1},
        },
        headers={"x-goog-api-key": api_key},
        timeout=60,
    )
    candidates = result.get("candidates") or []
    if not candidates:
        reason = result.get("promptFeedback", {}).get("blockReason", "no candidates returned")
        raise ProviderError(f"Gemini returned no SQL: {reason}")
    candidate = candidates[0]
    text = "\n".join(
        part["text"] for part in candidate.get("content", {}).get("parts", [])
        if part.get("text") and not part.get("thought")
    ).strip()
    if not text:
        raise ProviderError(f"Gemini returned no SQL: {candidate.get('finishReason', 'empty response')}")
    return text, f"Gemini ({model})"


def generate_sql(question, schema, config):
    errors = []
    try:
        return _ollama(question, schema, config)
    except Exception as exc:
        errors.append(f"Local Ollama failed: {exc}")

    # Only try Gemini when a key is configured, preserving existing setups.
    if config.get("gemini", "api_key", fallback="").strip() or os.environ.get("GEMINI_API_KEY", "").strip():
        try:
            return _gemini(question, schema, config)
        except Exception as exc:
            errors.append(f"Gemini fallback failed: {exc}")

    try:
        return _cloud(question, schema, config)
    except Exception as exc:
        errors.append(f"Cloud fallback failed: {exc}")

    raise ProviderError("\n\n".join(errors))
