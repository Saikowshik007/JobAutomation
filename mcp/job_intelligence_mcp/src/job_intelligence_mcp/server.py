from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from job_intelligence_mcp.parser import LlmTextResult, get_llm_text_for_url_async


mcp = FastMCP("job-intelligence-mcp")


@mcp.tool(structured_output=True)
async def get_llm_text_for_this_url(url: str) -> LlmTextResult:
    """Fetch a job posting URL and return compact model-ready text as structured JSON."""
    return await get_llm_text_for_url_async(url)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
