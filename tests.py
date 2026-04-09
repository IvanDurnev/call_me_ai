import os

import requests

url = "https://api.openai.com/v1/audio/voice_consents"
api_key = os.getenv("OPENAI_API_KEY", "").strip()

if not api_key:
    raise RuntimeError("OPENAI_API_KEY is not set")

headers = {
    "Authorization": f"Bearer {api_key}",
    "Content-Type": "application/json",
}

response = requests.post(url, headers=headers)

print(response.status_code)
print(response.json())
