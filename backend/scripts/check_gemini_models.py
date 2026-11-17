from backend.app.ai.gemini_provider import gemini_provider
from google.genai import types

client = gemini_provider._get_client()
models = [
    "gemini-2.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash-lite",
    "gemini-3.5-flash",
    "gemini-3.6-flash",
    "gemini-3.7-flash",
    "gemini-3.8-flash"
]

for m in models:
    try:
        res = client.models.generate_content(
            model=m,
            contents='Return valid JSON: {"status": "ok", "model": "' + m + '"}',
            config=types.GenerateContentConfig(response_mime_type="application/json")
        )
        print(f"{m}: SUCCESS -> {res.text.strip()[:100]}")
    except Exception as e:
        print(f"{m}: FAILED -> {e}")
