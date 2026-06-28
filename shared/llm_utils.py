from __future__ import annotations

import os
import time
from typing import Any

import requests


def llm_api_base_url() -> str:
    base = (
        os.getenv("OPENAI_BASE_URL", "").strip()
        or os.getenv("GOOGLE_API_BASE_URL", "").strip()
        or "https://api.openai.com/v1"
    )
    return base.rstrip("/")


def llm_api_url(path: str) -> str:
    normalized_path = path if path.startswith("/") else f"/{path}"
    return f"{llm_api_base_url()}{normalized_path}"


def llm_auth_headers(api_key: str) -> dict[str, str]:
    base_url = llm_api_base_url().lower()
    headers = {"Content-Type": "application/json"}
    if "generativelanguage.googleapis.com" in base_url and "/openai" not in base_url:
        headers["x-goog-api-key"] = api_key
        return headers
    headers["Authorization"] = f"Bearer {api_key}"
    return headers


def llm_api_key() -> str:
    return os.getenv("OPENAI_API_KEY", "").strip() or os.getenv("GOOGLE_API_KEY", "").strip()


def is_google_genai_base() -> bool:
    return "generativelanguage.googleapis.com" in llm_api_base_url().lower()


def llm_provider_name() -> str:
    base = llm_api_base_url().lower()
    if "generativelanguage.googleapis.com" in base:
        return "google"
    if "anthropic" in base:
        return "anthropic"
    if "yandex" in base:
        return "yandex"
    return "openai"


def llm_model_for_request(request_type: str) -> str:
    fallback = os.getenv("OPENAI_MODEL", "gpt-4.1-mini").strip()
    if request_type == "owner_daily_report":
        return (
            os.getenv("OWNER_DAILY_MODEL", "").strip()
            or os.getenv("OWNER_REPORT_MODEL", "").strip()
            or os.getenv("OPENAI_OWNER_DAILY_MODEL", "").strip()
            or "gemini-2.0-flash"
        )
    if request_type == "owner_weekly_report":
        return (
            os.getenv("OWNER_WEEKLY_MODEL", "").strip()
            or os.getenv("OWNER_REPORT_MODEL", "").strip()
            or os.getenv("OPENAI_OWNER_WEEKLY_MODEL", "").strip()
            or fallback
        )
    if request_type == "zoom_processing":
        return os.getenv("ZOOM_PROCESSING_MODEL", "").strip() or fallback
    return fallback


def _parse_model_list(raw: str) -> list[str]:
    items = [part.strip() for part in (raw or "").split(",")]
    return [item for item in items if item]


def _google_ocr_model_candidates(primary_model: str) -> list[str]:
    explicit = _parse_model_list(os.getenv("GOOGLE_OCR_MODEL_CANDIDATES", "").strip())
    if explicit:
        return explicit
    default_fallback = _parse_model_list(
        os.getenv("GOOGLE_OCR_FALLBACK_MODELS", "gemini-2.0-flash")
    )
    primary = primary_model if primary_model.startswith("gemini-") else "gemini-2.0-flash"
    ordered = [primary] + default_fallback
    dedup: list[str] = []
    seen: set[str] = set()
    for model_name in ordered:
        if model_name not in seen:
            seen.add(model_name)
            dedup.append(model_name)
    return dedup


def _retry_after_seconds(response: requests.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if not value:
        return None
    try:
        seconds = float(value.strip())
    except ValueError:
        return None
    return max(0.0, seconds)


def llm_post_with_retry(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: int = 120,
) -> requests.Response:
    max_retries = max(0, int(os.getenv("LLM_MAX_RETRIES", "2")))
    base_delay = max(0.2, float(os.getenv("LLM_RETRY_BASE_DELAY", "0.8")))
    last_response: requests.Response | None = None
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=timeout)
            if response.status_code not in {429, 503}:
                return response
            last_response = response
            if attempt >= max_retries:
                return response
            wait_seconds = _retry_after_seconds(response)
            if wait_seconds is None:
                wait_seconds = min(base_delay * (2**attempt), 8.0)
            time.sleep(wait_seconds)
        except requests.RequestException as exc:
            last_error = exc
            if attempt >= max_retries:
                break
            time.sleep(min(base_delay * (2**attempt), 8.0))
    if last_response is not None:
        return last_response
    if last_error is not None:
        raise last_error
    raise RuntimeError("LLM request failed without details")
