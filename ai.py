import os
import sys
import json
import urllib.request
import urllib.error

import config


def get_ollama_model() -> str:
    if config.OLLAMA_MODEL:
        return config.OLLAMA_MODEL

    models = []
    ollama_available = False
    url = f"{config.OLLAMA_HOST.rstrip('/')}/api/tags"
    try:
        req = urllib.request.Request(url, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=3) as response:
            data = json.loads(response.read().decode("utf-8"))
            models = data.get("models", [])
            if models:
                ollama_available = True
    except Exception:
        pass

    if ollama_available:
        print("\nAvailable Ollama models:")
        for i, m in enumerate(models, 1):
            print(f"  {i}. {m['name']}")
        
        gemini_option_idx = len(models) + 1
        clean_option_idx = gemini_option_idx + 1
        print(f"  {gemini_option_idx}. Use Gemini API (Skip Ollama)")
        print(f"  {clean_option_idx}. Clean generated reports")
        
        while True:
            choice = input(f"Select a model (1-{clean_option_idx}) [default 1]: ").strip()
            if not choice:
                config.OLLAMA_MODEL = models[0]["name"]
                return config.OLLAMA_MODEL
            if choice.isdigit() and 1 <= int(choice) <= clean_option_idx:
                selected_idx = int(choice)
                if selected_idx == clean_option_idx:
                    from report import clean_reports
                    clean_reports()
                    sys.exit(0)
                elif selected_idx == gemini_option_idx:
                    config.OLLAMA_MODEL = "GEMINI"
                else:
                    config.OLLAMA_MODEL = models[selected_idx-1]["name"]
                return config.OLLAMA_MODEL
            print("Invalid choice. Please try again.")
    else:
        print("\n[INFO] Local Ollama not running or has no models.")
        print("Available options:")
        print("  1. Use Gemini API")
        print("  2. Clean generated reports")
        
        while True:
            choice = input("Select an option (1-2) [default 1]: ").strip()
            if not choice or choice == "1":
                config.OLLAMA_MODEL = "GEMINI"
                return config.OLLAMA_MODEL
            if choice == "2":
                from report import clean_reports
                clean_reports()
                sys.exit(0)
            print("Invalid choice. Please try again.")


def _get_ai_instructions(scan_data: dict) -> tuple[str, str]:
    system_instruction = """You are an expert in Windows storage optimization.
Always respond in English. Your responses must be direct, starting directly with the first section header "## Disk Status" without any introductory greeting, preamble, or chatty conversational filler.
Strictly adhere to the requested markdown formatting. Do not recommend deleting operating system files or critical Windows folders."""

    user_prompt = f"""Analyze this disk report and respond using exactly this markdown format:

## Disk Status
One line per disk: `LETTER:` — X GB used of Y GB (Z% free) — status (OK / Attention / Critical).
Provide a brief 1-2 sentence diagnostic summary.

## Top space saving actions
List up to 5 space saving recommendations.
IMPORTANT: Only recommend actions that have a meaningful impact relative to the disk size. If all available folders/files in the scan report are small (e.g. less than 10 GB or under 1% of disk size), list fewer recommendations (1 or 2), and recommend general system actions (like Windows Disk Cleanup 'cleanmgr', Storage Sense, or web browser cache clearing) rather than presenting small 1-5 GB folders as major space-saving action cards.

Each action must use this EXACT markdown format (use '###' for the header, do not use '####' or any other header level):
### N. Short action title
- **What:** description of the action, detailing the files/folders involved
- **Why/How:** explanation of how this space can be freed (e.g. specific tool, path, or command) and why it is safe or risky
- **Impact:** ~X GB
- **Safety:** ✅ Safe | ⚠️ With caution | 🔴 Manual review

## Preventive tips
5 actionable preventive tips to maintain disk health, each explained in 1-2 sentences.

---
System data:
{json.dumps(scan_data, ensure_ascii=False, indent=2)}
"""
    return system_instruction, user_prompt


def ask_ollama(scan_data: dict) -> str:
    model = get_ollama_model()
    system_instruction, user_prompt = _get_ai_instructions(scan_data)

    url = f"{config.OLLAMA_HOST.rstrip('/')}/api/chat"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": user_prompt}
        ],
        "options": {
            "temperature": 0.2
        },
        "stream": False
    }

    print(f"\n  Querying local Ollama AI (model: {model})...", flush=True)
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=300) as response:
        resp_data = json.loads(response.read().decode("utf-8"))
        message = resp_data.get("message", {})
        content = message.get("content", "")
        if not content:
            raise ValueError("Ollama returned an empty response.")
        return content


def ask_gemini(scan_data: dict) -> str:
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        print("\n[ERROR] The 'google-genai' package is not installed.")
        print("  Please run: pip install google-genai")
        sys.exit(1)

    try:
        client = genai.Client(api_key=config.API_KEY)
        system_instruction, user_prompt = _get_ai_instructions(scan_data)

        print("\n  Querying Gemini AI...", flush=True)
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=0.2,
            ),
        )
        return response.text
    except Exception as e:
        print(f"\n[ERROR] Error querying Gemini AI: {e}")
        print("Saving the report without AI recommendations.")
        return "Could not retrieve Gemini AI recommendations due to an API error or quota limit exceeded."
