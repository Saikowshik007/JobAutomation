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
4. Make an evidence map: priority requirements, supported matching evidence and ATS terms, gaps that cannot be claimed, and bullets that are genuinely duplicate, unsupported, or irrelevant. Classify each source bullet as retain, tighten, or remove; retain is the default for distinct, supported evidence.
5. Tailor the YAML conservatively: reorder and reframe supported content, preserve distinct accomplishments and implementation detail, remove only genuine duplication or low-relevance filler, and organize relevant skills.
6. Complete the quality review, correct supported issues, then render with `resume_renderer.render_resume_from_yaml`.
7. Verify that the PDF exists, has an appropriate page count, and extracts without obvious omissions or truncation. Visually inspect page utilization and information density. Preserve a full first page; if a second page contains only skills or a small residual section, first restore the strongest omitted evidence or retain more relevant bullets rather than compressing valuable content away. Use two pages when the base resume contains enough distinct, relevant evidence to support them.
8. Return the company, role, key changes, YAML path, PDF path, and caveats.

## Truthfulness and Scope

- Never invent or inflate employers, projects, dates, degrees, credentials, tools, outcomes, scale, or experience level.
- Use job terminology when it is explicitly supported, clearly demonstrated, or a strong, interview-defensible inference from the source resume. A term need not be named verbatim when the described implementation, design decision, operational responsibility, or outcome reasonably demonstrates it.
- For every inferred term, record the source evidence, the inferred capability, and a one-sentence rationale in the evidence map. Prefer the most general accurate term when the evidence supports a capability but not a particular language, library, platform, implementation mechanism, or level of ownership. Omit the inference or ask the user when it would be difficult to defend accurately in an interview.
- Prefer reordering and precise framing over deletion. Tighten wording only when it preserves the implementation, scope, and outcome of the original claim. Treat missing domain experience as a caveat; use adjacent experience only when genuinely comparable.
- Keep contact details and education intact, except for workflow-permitted normalization. Preserve valid renderer schema.
- Use user-supplied facts only within their stated scope; request dates or employer details before changing work history.

## Resume Writing Standard

- Lead the first third of page one with the most relevant evidence, skills, and experience. Use simple ATS-readable headings and reverse chronology.
- Make each bullet one achievement: action + concrete implementation/context + outcome. Aim for 15–20 words; 25 is the normal maximum.
- Add metrics or scale only when supported and persuasive. State them consistently (for example, “by 30%” or “from X to Y”); do not imply a missing baseline or method. Use a mix of impact, architecture, ownership, reliability, collaboration, and complexity.
- Limit bullets to two key technologies. Use accurate ownership verbs, vary active verbs naturally, and avoid buzzwords or technology lists.
- Treat the usual bullet-length target as a guide, not a deletion rule: preserve a longer bullet when shortening it would remove meaningful implementation detail, technical complexity, scope, or outcome.
- Keep each role distinct; use projects for complementary proof. Do not remove a strong bullet merely because it repeats a job keyword if it proves a different capability, scale, ownership area, or outcome. Remove only true duplicates, weakly related filler, or content displaced by stronger evidence.
- Include source-supported skills and narrowly defensible technical inferences; normalize variants, list each once, organize into at most four relevant categories, and usually keep at most 25 technical skills. Do not use soft-skill keywords as filler. For inferred skills, retain the evidence-map rationale and omit the skill if it cannot be explained accurately in an interview.
- Omit a summary if total experience is less than 3 years. Add a two- or three-line summary only if it adds distinct, substantiated value without forcing an unnecessary page.
- Treat page count as an output of the evidence, not a target. Prefer one page only when all distinct, high-value evidence fits without reducing it to generic statements. Never delete, merge, or materially shorten strong relevant bullets merely to force one page.
- After the first render, inspect both pages. If page one is underfilled, restore the strongest omitted supported evidence. If page two is sparse, first retain or restore distinct, relevant bullets and projects so it contains substantive evidence; then consider a two-page layout acceptable when page two is meaningfully populated.
- Do not perform a second compression pass solely to move skills onto page one. Each deletion must have an explicit reason: duplicate evidence, weak relevance, unsupported content, or a stronger retained bullet proving the same claim.
- Use two pages when retained experience, projects, education, and skills are substantive. Avoid a skills-only second page, but do not solve it by removing valuable experience; instead preserve relevant content so the second page earns its space.

## Pre-Render Review

Before rendering, confirm:

- factual grounding: employers, dates, titles, skills, metrics, and scale match the source or supplied facts;
- relevance: priority requirements and keywords appear naturally in supported experience, projects, or skills;
- credibility: claims contain meaningful implementation detail and an interviewer could discuss them;
- clarity: no duplicate achievement or skill remains; bullets, tense, dates, punctuation, and metrics are consistent and scannable;
- structure: the strongest evidence appears early, white space is readable, the first page is not visibly underfilled, and a second page—if used—contains substantive experience, projects, or supporting evidence rather than only a residual skills block;
- preservation: every removed or materially shortened bullet has a documented rationale, and no distinct, high-value supported accomplishment was removed solely to satisfy a one-page preference.

## Failure Handling

- If an MCP server or base resume is unavailable, identify it and stop; do not fabricate output.
- If URL extraction fails, continue only when sufficient pasted job text is available.
- If the description is too thin to tailor responsibly, request only the missing information.
- If rendering fails, return the tailored YAML and the rendering error.
