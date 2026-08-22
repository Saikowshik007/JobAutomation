# Recommended Repo Structure

```text
JobAutomation/
  mcp/
    job_intelligence_mcp/
      pyproject.toml
      src/
        job_intelligence_mcp/
          parser.py
          server.py

  tests/
    test_mcp_client.py

  README.md
  .gitignore
```

## Notes

- Keep the parser and MCP server in the same repo until the interface becomes stable.
- Prefer one useful MCP server over several speculative ones.
- Do not add storage or upload layers unless a concrete workflow requires them.
