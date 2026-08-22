# Module Boundary Decision Table

| Component | Best First Form | Why | MCP Later? | Separate Service Later? |
|---|---|---|---|---|
| Job page fetch/render | Plain module inside current MCP package | Closely coupled to parsing and still changing | Already exposed through MCP entrypoint | No |
| Job parsing and normalization | Plain module inside current MCP package | Core logic is local and should stay easy to edit | Already exposed through MCP entrypoint | No |
| MCP server wrapper | MCP | This is the stable tool boundary that other agents can call | Yes, keep this one | No |
| Resume tailoring | Not implemented yet | Do not create the boundary before the real logic exists | Maybe later | No |
| Application automation | Not implemented yet | High-change area; avoid speculative architecture | Maybe later | Maybe |

## Rules of Thumb

- Keep evolving logic as local modules in this repo.
- Expose MCP only where a tool boundary is immediately useful.
- Do not introduce extra services, queues, or storage layers without a concrete operational need.

## Recommended Current MCP Export

1. `job_intelligence_mcp`
   - `get_llm_text_for_this_url(url)`

Anything beyond this should wait until there is proven demand in the current repo.
