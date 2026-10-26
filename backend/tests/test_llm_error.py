
import os
import sys
from pathlib import Path

# Ensure backend root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.llm import create_base_llm

print("Testing OpenRouter LLM connection...", flush=True)

llm = create_base_llm()

if llm is None:
    print("ERROR: LLM client initialization failed.", flush=True)
else:
    print(
        f"LLM initialized: model={getattr(llm, 'model_name', getattr(llm, 'model', 'unknown'))}, "
        f"max_tokens={getattr(llm, 'max_tokens', None)}, "
        f"timeout_ms={getattr(llm, 'request_timeout', None)}, "
        f"max_retries={getattr(llm, 'max_retries', None)}",
        flush=True,
    )
    try:
        response = llm.invoke("Reply with PONG")
        print("LLM REQUEST SUCCEEDED:", response, flush=True)
    except Exception as exc:
        print("\n========== LLM REQUEST FAILED ==========", flush=True)
        print("Error type:", type(exc).__name__, flush=True)
        print("Error details:", str(exc), flush=True)
        print("========================================", flush=True)

