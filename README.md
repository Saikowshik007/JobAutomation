# JobAutomation

This repo should stay small.

The practical scope is:

- keep one local MCP server in this repo
- use it to fetch and normalize job posting text from a URL
- return compact LLM-ready output that the rest of the workflow can consume

What this repo is not trying to be:

- a multi-service platform
- a collection of separate MCP wrappers for every future subsystem
- a Drive upload or document storage pipeline

## Current shape

```text
JobAutomation/
  mcp/
    job_intelligence_mcp/
      pyproject.toml
      src/job_intelligence_mcp/
        parser.py
        server.py
  tests/
    test_mcp_client.py
  README.md
```

## Why this is enough for now

- The MCP boundary already exists where it is useful: `get_llm_text_for_this_url`.
- The parser logic is still evolving, so keeping it in the same repo avoids premature separation.
- Uploading artifacts to Google Drive adds state, auth, and operational complexity without helping the main job-to-resume flow.

## Near-term direction

1. Keep hardening `job_intelligence_mcp`.
2. Add tests around parsing and normalization.
3. Only split more modules or add more MCP servers after stable internal interfaces actually emerge.
