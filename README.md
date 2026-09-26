# Resume End-to-End Skill

Tailor a truthful, job-specific resume from the JobAutomation base resume and render submission-ready PDF artifacts.

## Install

Copy the entire `place_this_in_skills` folder into a Codex-discovered skills directory and rename it `resume-end-to-end` if needed. Keep the folder contents together:

```text
resume-end-to-end/
├── SKILL.md
├── README.md
└── references/
    └── workflow.md
```

Start a new Codex session after installing or updating the skill so it can be discovered. If you distribute skills as a plugin, place this folder under that plugin's `skills/` directory.

## Prerequisites

- Work in the JobAutomation workspace.
- Add the `job_intelligence` and `resume_renderer` MCP servers to your Codex `config.toml` file. Use the following configuration, adjusting the workspace path if your clone is elsewhere:

```toml
[mcp_servers.job_intelligence]
command = "python"
args = [
    "D:/projects/JobAutomation/mcp/job_intelligence_mcp/src/job_intelligence_mcp/server.py"
]

[mcp_servers.resume_renderer]
command = "python"
args = [
    "-m",
    "resume_renderer_mcp.server"
]
cwd = "D:/projects/JobAutomation/mcp/resume_renderer_mcp"

[mcp_servers.resume_renderer.env]
PYTHONPATH = "D:/projects/JobAutomation/mcp/resume_renderer_mcp/src"
```

- Restart Codex after updating `config.toml` so both MCP servers are available.
- Keep the base resume accessible through `resume_renderer.get_base_resume_yaml`.

## Use

Ask Codex to use the `resume-end-to-end` skill and provide:

- company name
- role title
- job-posting URL or pasted job description

Example:

```text
Use the resume-end-to-end skill to tailor my resume for a Software Engineer role at Acme.
Job URL: https://example.com/jobs/software-engineer
```

With pasted text:

```text
Use the resume-end-to-end skill for Data Analyst at Acme. Here is the job description:
<paste description>
```

## What It Produces

The skill extracts or summarizes the role requirements, tailors only supported resume content, validates the result, and renders the final YAML and PDF under:

```text
JobAutomation\resume\<Company>\<Position>\
```

Codex returns the artifact paths, a short summary of changes, and any caveats.

## Guardrails

The skill does not invent experience, metrics, employers, education, credentials, or unsupported tools. It avoids cutting strong relevant evidence merely to force one page: an underfilled first page should regain the best supported content, while a justified second page should not be sparse.
