"""
Local-First AI Provider Router for Gaming Uncut Automation.
Directs tasks across Local LLMs (Ollama / Local Inference), Gemini 2.5 Flash,
and Deterministic Heuristic Fallbacks with token usage tracking and exponential backoff.
"""

import os
import json
import time
import logging
from typing import Dict, Any, Optional, List
import requests

from backend.app.core.config import settings
from backend.app.security.vault import SecureVault
from backend.app.ai.gemini_provider import _call_gemini_with_timeout

logger = logging.getLogger("gaming_uncut.llm_router")

class LLMRouter:
    def __init__(self):
        self.vault = SecureVault()
        self.ollama_host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
        self.default_local_model = os.getenv("OLLAMA_MODEL", "llama3")
        self._stats = {
            "local_calls": 0,
            "cloud_calls": 0,
            "heuristic_calls": 0,
            "total_prompt_tokens": 0,
            "total_completion_tokens": 0,
        }

    def get_stats(self) -> Dict[str, Any]:
        """Returns live statistics of provider invocations and token metrics."""
        return dict(self._stats)

    def is_local_llm_available(self, timeout: float = 1.0) -> bool:
        """Fast check to verify if Ollama or local inference server is responding."""
        try:
            r = requests.get(f"{self.ollama_host}/api/tags", timeout=timeout)
            return r.status_code == 200
        except Exception:
            return False

    def get_available_local_models(self) -> List[str]:
        """Queries local Ollama instance for installed models."""
        try:
            r = requests.get(f"{self.ollama_host}/api/tags", timeout=1.5)
            if r.status_code == 200:
                data = r.json()
                return [m["name"] for m in data.get("models", [])]
        except Exception:
            pass
        return []

    def generate(
        self,
        task_type: str,
        prompt: str,
        system_prompt: str = "",
        preferred_provider: Optional[str] = None,
        json_mode: bool = False,
        temperature: float = 0.3,
        heuristic_fallback_fn: Optional[Any] = None
    ) -> Dict[str, Any]:
        """
        Executes generation following the local-first routing strategy:
        - Captions, descriptions, titles, hashtags, keyword grouping -> Local LLM
        - Advanced reasoning -> Cloud AI (Gemini)
        - Seamless automatic fallback with exponential backoff on rate limits / network errors.
        """
        # Determine initial preferred provider
        if preferred_provider:
            target_provider = preferred_provider.lower()
        elif task_type in ("captions", "descriptions", "titles", "hashtags", "keyword_grouping", "performance_analysis"):
            target_provider = "local"
        elif task_type in ("advanced_reasoning", "multimodal_context"):
            target_provider = "gemini"
        else:
            target_provider = "local"

        start_time = time.time()
        fallback_occurred = False

        # 1. Attempt Local LLM if targeted
        if target_provider == "local":
            if self.is_local_llm_available(timeout=0.3):
                try:
                    result = self._call_ollama(prompt, system_prompt, json_mode, temperature)
                    if result:
                        self._stats["local_calls"] += 1
                        self._stats["total_prompt_tokens"] += result.get("prompt_tokens", 0)
                        self._stats["total_completion_tokens"] += result.get("completion_tokens", 0)
                        result["latency_ms"] = round((time.time() - start_time) * 1000, 1)
                        result["fallback_occurred"] = False
                        return result
                except Exception as e:
                    logger.warning(f"Local LLM execution failed ({e}). Escalating to cloud provider fallback.")
                    fallback_occurred = True
                    target_provider = "gemini"
            else:
                fallback_occurred = True
                target_provider = "gemini"

        # 2. Attempt Cloud Gemini with exponential backoff for rate limits (429)
        if target_provider == "gemini":
            try:
                result = self._call_gemini_resilient(prompt, system_prompt, json_mode, temperature)
                if result:
                    self._stats["cloud_calls"] += 1
                    self._stats["total_prompt_tokens"] += result.get("prompt_tokens", 0)
                    self._stats["total_completion_tokens"] += result.get("completion_tokens", 0)
                    result["latency_ms"] = round((time.time() - start_time) * 1000, 1)
                    result["fallback_occurred"] = fallback_occurred
                    return result
            except Exception as e:
                logger.warning(f"Cloud AI provider execution failed ({e}). Engaging deterministic fallback.")
                fallback_occurred = True

        # 3. Deterministic Heuristic Fallback
        self._stats["heuristic_calls"] += 1
        raw_text = ""
        parsed_json = None
        if heuristic_fallback_fn:
            try:
                parsed_json = heuristic_fallback_fn()
                raw_text = json.dumps(parsed_json) if isinstance(parsed_json, (dict, list)) else str(parsed_json)
            except Exception as e:
                logger.error(f"Heuristic fallback failed: {e}")

        return {
            "text": raw_text,
            "json": parsed_json,
            "provider": "heuristic",
            "model": "rule_based_engine",
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "latency_ms": round((time.time() - start_time) * 1000, 1),
            "fallback_occurred": True
        }

    def _call_ollama(
        self,
        prompt: str,
        system_prompt: str,
        json_mode: bool,
        temperature: float,
        timeout: float = 30.0
    ) -> Optional[Dict[str, Any]]:
        """Invokes local Ollama inference server."""
        available_models = self.get_available_local_models()
        model_to_use = self.default_local_model
        if available_models and model_to_use not in available_models:
            model_to_use = available_models[0]

        payload = {
            "model": model_to_use,
            "prompt": prompt,
            "system": system_prompt,
            "stream": False,
            "options": {
                "temperature": temperature
            }
        }
        if json_mode:
            payload["format"] = "json"

        resp = requests.post(
            f"{self.ollama_host}/api/generate",
            json=payload,
            timeout=timeout
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Ollama returned HTTP {resp.status_code}: {resp.text}")

        data = resp.json()
        raw_response = data.get("response", "").strip()

        parsed_json = None
        if json_mode and raw_response:
            try:
                parsed_json = json.loads(raw_response)
            except Exception:
                # Attempt to extract JSON substring if wrapped in markdown
                start = raw_response.find("{")
                end = raw_response.rfind("}")
                if start != -1 and end != -1:
                    parsed_json = json.loads(raw_response[start:end+1])

        prompt_eval_count = data.get("prompt_eval_count", len(prompt.split()))
        eval_count = data.get("eval_count", len(raw_response.split()))

        return {
            "text": raw_response,
            "json": parsed_json,
            "provider": "ollama",
            "model": model_to_use,
            "prompt_tokens": prompt_eval_count,
            "completion_tokens": eval_count
        }

    def _call_gemini_resilient(
        self,
        prompt: str,
        system_prompt: str,
        json_mode: bool,
        temperature: float,
        max_retries: int = 2
    ) -> Optional[Dict[str, Any]]:
        """Invokes Gemini 2.5 Flash with exponential backoff on HTTP 429."""
        api_key = self.vault.get_secret("google", "gemini_api_key") or os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("Gemini API key is unconfigured.")

        from google import genai
        client = genai.Client(api_key=api_key)

        full_prompt = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt
        delay = 1.0

        for attempt in range(max_retries + 1):
            try:
                raw_json = _call_gemini_with_timeout(
                    client=client,
                    contents=full_prompt,
                    response_mime_type="application/json" if json_mode else "text/plain",
                    temperature=temperature,
                    timeout_seconds=20.0
                )
                if raw_json:
                    parsed = json.loads(raw_json) if json_mode else None
                    p_tokens = len(full_prompt.split())
                    c_tokens = len(raw_json.split())
                    return {
                        "text": raw_json,
                        "json": parsed,
                        "provider": "gemini",
                        "model": settings.GEMINI_MODEL,
                        "prompt_tokens": p_tokens,
                        "completion_tokens": c_tokens
                    }
                break
            except Exception as e:
                err_str = str(e).lower()
                if ("429" in err_str or "quota" in err_str or "rate" in err_str) and attempt < max_retries:
                    logger.warning(f"Gemini 429 rate limit hit. Backing off for {delay}s...")
                    time.sleep(delay)
                    delay *= 2.0
                else:
                    raise e

        return None

llm_router = LLMRouter()
