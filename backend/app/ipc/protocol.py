"""Newline-delimited JSON message framing — docs/ipc_protocol.md."""
import json
from typing import Any, Optional


def encode_message(message: dict[str, Any]) -> str:
    """Serialize a message dict to a single JSON line (no trailing newline)."""
    return json.dumps(message, separators=(",", ":"))


def decode_message(line: str) -> dict[str, Any]:
    """Parse one line of stdin into a message dict. Raises ValueError if malformed."""
    return json.loads(line)


def make_response(request_id: str, result: Any) -> dict:
    return {"id": request_id, "result": result}


def make_error_response(request_id: str, error: dict) -> dict:
    return {"id": request_id, "error": error}


def make_notification(event: str, data: Any) -> dict:
    return {"event": event, "data": data}


def request_id_and_method(message: dict) -> tuple[Optional[str], Optional[str]]:
    return message.get("id"), message.get("method")
