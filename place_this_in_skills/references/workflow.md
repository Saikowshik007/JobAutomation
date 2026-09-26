# Workflow Reference

## MCP Tools

- `job_intelligence.get_llm_text_for_this_url` — extract a live job posting.
- `resume_renderer.get_base_resume_yaml` — load the base resume YAML.
- `resume_renderer.render_resume_from_yaml` — render the tailored resume.

## Procedure

1. For a job URL, call `job_intelligence.get_llm_text_for_this_url` and use its structured output. For pasted text only, derive the requirements and state that extraction was skipped.
2. Load the base YAML with `resume_renderer.get_base_resume_yaml`.
3. Edit only as follows:
   - Reorder or rewrite `experiences[*].highlights` and `projects[*].highlights`.
   - Reorder skill categories or trim irrelevant skills.
   - Normalize education formatting lightly.
   - Do not invent achievements or add employers, projects, schools, dates, credentials, or unsupported tool experience unless the user supplied the fact.
4. Render with `resume_renderer.render_resume_from_yaml` using:
   - `resume_yaml`: final tailored YAML string
   - `company_name`: target company
   - `position_title`: target role

## Outputs

The renderer writes to `JobAutomation\resume\<Company>\<Position>\`.

Return a short change summary, final YAML path, final PDF path, and relevant caveats or assumptions.
