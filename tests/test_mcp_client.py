from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Call the local Job Intelligence MCP server with a job URL."
    )
    parser.add_argument("url", help="Job posting URL to parse")
    parser.add_argument(
        "--tool",
        default="get_llm_text_for_this_url",
        choices=["get_llm_text_for_this_url"],
        help="MCP tool to invoke",
    )
    return parser


async def run(url: str, tool: str) -> int:
    workspace_root = Path(__file__).resolve().parent.parent
    project_root = workspace_root / "mcp" / "job_intelligence_mcp"
    source_root = project_root / "src"
    python_path = str(source_root)
    existing_python_path = None

    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "job_intelligence_mcp.server"],
        cwd=str(project_root),
        env={
            "PYTHONPATH": (
                python_path
                if not existing_python_path
                else f"{python_path}{os.pathsep}{existing_python_path}"
            )
        },
    )

    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.call_tool(tool, {"url": url})
            payload = result.structuredContent
            if payload is None:
                payload = result.model_dump()
            print(json.dumps(payload, indent=2))
            print(result)
    return 0


def main() -> None:
    args = build_argument_parser().parse_args()
    raise SystemExit(asyncio.run(run(args.url, args.tool)))


if __name__ == "__main__":
    main()
