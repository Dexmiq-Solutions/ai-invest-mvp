import json
import time
from pathlib import Path
from datetime import datetime
from functools import wraps

from app.node_behavior_logging import trace_node

LOG_FILE = Path(__file__).resolve().parent.parent / "logs" / "node-behavior.json"
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)


def trace_node_json(node_name, node_function):
    # Also attach the structured line-by-line logger for node-behavior.log
    wrapped_with_behavior = trace_node(node_name, node_function)

    @wraps(node_function)
    def wrapper(state, *args, **kwargs):
        start_time = time.perf_counter()
        event = {
            "timestamp": datetime.now().isoformat(),
            "node": node_name,
            "input": state,
        }

        try:
            result = wrapped_with_behavior(state, *args, **kwargs)
            event["status"] = "success"
            event["output"] = result
            event["duration_seconds"] = round(time.perf_counter() - start_time, 4)
            return result
        except Exception as exc:
            event["status"] = "error"
            event["error"] = str(exc)
            event["duration_seconds"] = round(time.perf_counter() - start_time, 4)
            raise
        finally:
            try:
                existing = []
                if LOG_FILE.exists() and LOG_FILE.stat().st_size > 0:
                    try:
                        content = LOG_FILE.read_text(encoding="utf-8").strip()
                        if content:
                            parsed = json.loads(content)
                            if isinstance(parsed, list):
                                existing = parsed
                    except Exception:
                        existing = []
                existing.append(event)
                LOG_FILE.write_text(
                    json.dumps(existing, indent=2, default=str),
                    encoding="utf-8",
                )
            except Exception as log_error:
                print(f"JSON logging failed: {log_error}")

    return wrapper

