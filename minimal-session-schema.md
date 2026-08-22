# Minimal Session Schema

This only becomes relevant if the repo grows beyond URL parsing.

For the current repo, the practical state model is just the MCP request and response:

## Request

- `url` - job posting URL
- `timeout_seconds` - optional timeout override

## Response

- `llm_input_text` - compact model-ready job description text

## If State Is Needed Later

- add a local session table only when there is a real pause/resume workflow
- keep file paths and artifact storage out of the first design
- prefer reconstructing state from the source URL unless that proves insufficient

## Notes

- Do not design persistence before the repo actually needs persistence.
- Avoid schema work that exists only to support hypothetical later modules.
