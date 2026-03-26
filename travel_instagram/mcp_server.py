"""
Minimal MCP-like stdio JSON-RPC server exposing one tool:
`generate_travel_reel`.

This is intentionally lightweight so it can run without extra dependencies.

Expected request shape (JSON-RPC 2.0):
  {"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}
  {"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"generate_travel_reel","arguments":{"prompt":"..."}}}

Each request must be a single-line JSON object on stdin.
Each response is a single-line JSON object on stdout.
"""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import asdict, is_dataclass
from typing import Any

from travel_instagram.mcp_reel_tool import generate_travel_reel_from_prompt

logger = logging.getLogger(__name__)


TOOL_DEFS: dict[str, dict[str, Any]] = {
    "generate_travel_reel": {
        "description": (
            "Generate a short vertical travel reel for a destination. Input is natural language; "
            "the tool parses destination + intent, looks up prices in an Excel table, fetches "
            "Pexels media, and assembles a 9:16 reel with destination/price overlays."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Natural language prompt, e.g. 'Beautiful destination Paris, now fly at'."},
                "excel_path": {"type": "string", "description": "Optional path to the Excel file. If omitted, uses TRAVEL_PRICES_EXCEL_PATH."},
                "output_dir": {"type": "string", "description": "Optional output directory for the reel."},
            },
            "required": ["prompt"],
            "additionalProperties": False,
        },
    }
}


def _json_line(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _tool_list() -> dict[str, Any]:
    tools: list[dict[str, Any]] = []
    for name, meta in TOOL_DEFS.items():
        tools.append(
            {
                "name": name,
                "description": meta.get("description") or "",
                "inputSchema": meta.get("inputSchema") or {},
            }
        )
    return {"tools": tools}


def _tool_call(name: str, arguments: dict[str, Any]) -> Any:
    if name != "generate_travel_reel":
        raise ValueError(f"Unknown tool: {name}")
    prompt = arguments.get("prompt")
    excel_path = arguments.get("excel_path")
    output_dir = arguments.get("output_dir")
    res = generate_travel_reel_from_prompt(
        str(prompt),
        excel_path=excel_path,
        output_dir=output_dir,
    )
    return res


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    while True:
        line = sys.stdin.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as e:
            logger.error("Bad JSON line: %s", e)
            continue

        rid = req.get("id")
        method = req.get("method")
        params = req.get("params") or {}

        try:
            if method == "tools/list":
                result = _tool_list()
                resp = {"jsonrpc": "2.0", "id": rid, "result": result}
            elif method == "tools/call":
                name = params.get("name")
                arguments = params.get("arguments") or {}
                result = _tool_call(str(name), dict(arguments))
                # MCP clients often expect a "content" array; we keep it simple.
                resp = {
                    "jsonrpc": "2.0",
                    "id": rid,
                    "result": {"content": [{"type": "text", "text": json.dumps(result)}]},
                }
            else:
                resp = {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "Method not found"}}
        except Exception as e:
            resp = {"jsonrpc": "2.0", "id": rid, "error": {"code": -32000, "message": str(e)}}

        sys.stdout.write(_json_line(resp) + "\n")
        sys.stdout.flush()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

