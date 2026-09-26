---
name: resume-end-to-end
description: Tailor and render a complete job-specific resume using the local JobAutomation MCP servers and repo assets. Use when Codex needs to take a job URL or pasted job description, identify role requirements, adapt the base YAML resume truthfully for a specific company and role, and render final PDF artifacts.
---

# Resume End To End

Produce a submission-ready, job-specific resume for the JobAutomation workspace. Optimize for interview conversion through truthful relevance, ATS alignment, and fast recruiter comprehension.

Read [references/workflow.md](references/workflow.md) before acting. It defines MCP tools, permitted YAML edits, and output locations.

## Required Inputs

Identify the target company, role title, and a job URL or pasted job description. Ask only for missing fields.

## Workflow

1. Confirm `job_intelligence` and `resume_renderer` are available.
2. Build a job snapshot. For a live URL, call `job_intelligence.get_llm_text_for_this_url`; for pasted text, summarize it and state that extraction was skipped.
3. Load the base resume with `resume_renderer.get_base_resume_yaml`.
4. Make an evidence map: priority requirements, supported matching evidence and ATS terms, gaps that cannot be claimed, and low-priority content to cut.
5. Tailor the YAML conservatively: reorder, tighten, and reframe supported content; deduplicate evidence; organize relevant skills.
6. Complete the quality review, correct supported issues, then render with `resume_renderer.render_resume_from_yaml`.
7. Verify that the PDF exists, has an appropriate page count, and extracts without obvious omissions or truncation.
8. Return the company, role, key changes, YAML path, PDF path, and caveats.

## Truthfulness and Scope

- Never invent or inflate employers, projects, dates, degrees, credentials, tools, outcomes, scale, or experience level.
- Use job terminology only when it is explicitly supported or clearly demonstrated in the source resume.
- Prefer reordering, compression, and precise framing over adding claims. Treat missing domain experience as a caveat; use adjacent experience only when genuinely comparable.
- Keep contact details and education intact, except for workflow-permitted normalization. Preserve valid renderer schema.
- Use user-supplied facts only within their stated scope; request dates or employer details before changing work history.

## Resume Writing Standard

- Lead the first third of page one with the most relevant evidence, skills, and experience. Use simple ATS-readable headings and reverse chronology.
- Make each bullet one achievement: action + concrete implementation/context + outcome. Aim for 15–20 words; 25 is the normal maximum.
- Add metrics or scale only when supported and persuasive. State them consistently (for example, “by 30%” or “from X to Y”); do not imply a missing baseline or method. Use a mix of impact, architecture, ownership, reliability, collaboration, and complexity.
- Limit bullets to two key technologies. Use accurate ownership verbs, vary active verbs naturally, and avoid buzzwords or technology lists.
- Keep each role distinct; use projects for complementary proof. Remove duplicated or weakly related content before reducing readability.
- Include only source-supported skills; normalize variants, list each once, organize into at most four relevant categories, and usually keep at most 25 technical skills. Do not use soft-skill keywords as filler.
- Omit a summary by default. Add a two- or three-line summary only if it adds distinct, substantiated value without forcing an unnecessary page. Never write an objective.
- Prefer one page for early-career candidates when relevant evidence fits, but never cut strong relevant evidence solely to force it. If the rendered page is visibly underfilled (roughly 70–80%), restore the best supported, role-relevant content; use two pages when that content genuinely warrants it, and avoid a sparse second page.

## Pre-Render Review

Before rendering, confirm:

- factual grounding: employers, dates, titles, skills, metrics, and scale match the source or supplied facts;
- relevance: priority requirements and keywords appear naturally in supported experience, projects, or skills;
- credibility: claims contain meaningful implementation detail and an interviewer could discuss them;
- clarity: no duplicate achievement or skill remains; bullets, tense, dates, punctuation, and metrics are consistent and scannable;
- structure: the strongest evidence appears early, white space is readable, the first page is not visibly underfilled, and a summary or second page earns its space.

## Failure Handling

- If an MCP server or base resume is unavailable, identify it and stop; do not fabricate output.
- If URL extraction fails, continue only when sufficient pasted job text is available.
- If the description is too thin to tailor responsibly, request only the missing information.
- If rendering fails, return the tailored YAML and the rendering error.
