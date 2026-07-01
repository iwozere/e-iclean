"""Local entrypoint for the E-FileTrans backend sidecar.

Started by the Tauri shell (or directly, for dev). Reads newline-delimited
JSON-RPC requests from stdin and writes responses/notifications to stdout,
per docs/ipc_protocol.md.
"""
import asyncio

from app.main import run_stdio_loop

if __name__ == "__main__":
    asyncio.run(run_stdio_loop())
