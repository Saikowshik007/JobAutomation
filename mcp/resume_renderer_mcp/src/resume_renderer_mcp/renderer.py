from __future__ import annotations

import asyncio
import html
import re
from importlib import resources
from pathlib import Path
from string import Template

import yaml
from playwright.async_api import async_playwright
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


RESUMES_DIRNAME = "resume"
DEFAULT_TEMPLATE_PATH = Path("templates") / "resume-template.yaml"
DEFAULT_BASE_RESUME_PATH = Path("templates") / "resume-template.yaml"
DEFAULT_OUTPUT_ROOT = Path(__file__).resolve().parents[4]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BasicInfo(StrictModel):
    name: str
    address: str
    email: str
    phone: str
    websites: list[str] = Field(min_length=1)


class Degree(StrictModel):
    names: list[str] = Field(min_length=1)
    gpa: str = ""
    dates: str


class EducationEntry(StrictModel):
    school: str
    degrees: list[Degree] = Field(min_length=1)


class TitleEntry(StrictModel):
    name: str
    startdate: str
    enddate: str


class ExperienceEntry(StrictModel):
    company: str
    location: str
    titles: list[TitleEntry] = Field(min_length=1)
    highlights: list[str] = Field(min_length=1)


class ProjectEntry(StrictModel):
    name: str
    technologies: str = ""
    link: str = ""
    highlights: list[str] = Field(min_length=1)


class SkillSubcategory(StrictModel):
    name: str
    skills: list[str] = Field(min_length=1)


class SkillCategory(StrictModel):
    category: str
    skills: list[str] | None = None
    subcategories: list[SkillSubcategory] | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> "SkillCategory":
        if self.skills and self.subcategories:
            raise ValueError("skill category cannot define both skills and subcategories")
        if not self.skills and not self.subcategories:
            raise ValueError("skill category must define skills or subcategories")
        return self


class ResumeData(StrictModel):
    basic: BasicInfo
    objective: str = ""
    education: list[EducationEntry] = Field(min_length=1)
    experiences: list[ExperienceEntry] = Field(min_length=1)
    projects: list[ProjectEntry] = Field(min_length=1)
    skills: list[SkillCategory] = Field(min_length=1)


class ResumeTemplateYamlResult(StrictModel):
    template_path: str
    yaml_template: str


class ResumeBaseYamlResult(StrictModel):
    base_resume_path: str
    resume_yaml: str


class ResumeRenderResult(StrictModel):
    yaml_path: str
    pdf_path: str
    artifact_dir: str


def get_resume_template_yaml(
    *,
    workspace_root: Path = DEFAULT_OUTPUT_ROOT,
    template_path: Path | None = None,
) -> ResumeTemplateYamlResult:
    resolved_template_path = (
        (workspace_root / template_path).resolve()
        if template_path is not None
        else (workspace_root / DEFAULT_TEMPLATE_PATH).resolve()
    )
    yaml_template = resolved_template_path.read_text(encoding="utf-8")
    return ResumeTemplateYamlResult(
        template_path=str(resolved_template_path),
        yaml_template=yaml_template,
    )


def get_base_resume_yaml(
    *,
    workspace_root: Path = DEFAULT_OUTPUT_ROOT,
    base_resume_path: Path | None = None,
) -> ResumeBaseYamlResult:
    resolved_base_resume_path = (
        (workspace_root / base_resume_path).resolve()
        if base_resume_path is not None
        else (workspace_root / DEFAULT_BASE_RESUME_PATH).resolve()
    )
    resume_yaml = resolved_base_resume_path.read_text(encoding="utf-8")
    return ResumeBaseYamlResult(
        base_resume_path=str(resolved_base_resume_path),
        resume_yaml=resume_yaml,
    )


async def render_resume_from_yaml_async(
    resume_yaml: str,
    *,
    company_name: str,
    position_title: str,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
) -> ResumeRenderResult:
    if not resume_yaml or not resume_yaml.strip():
        raise ValueError("resume_yaml must not be empty")

    data = parse_resume_yaml(resume_yaml)

    safe_company = sanitize_path_component(company_name)
    safe_position = sanitize_path_component(position_title)
    safe_candidate = sanitize_path_component(data.basic.name)
    artifact_dir = (output_root / RESUMES_DIRNAME / safe_company / safe_position).resolve()
    artifact_dir.mkdir(parents=True, exist_ok=True)

    yaml_path = artifact_dir / f"{safe_candidate}.yaml"
    pdf_path = artifact_dir / f"{safe_candidate}.pdf"
    yaml_path.write_text(resume_yaml, encoding="utf-8")

    html_document = build_resume_html(data)
    await render_pdf_from_html(html_document, pdf_path)

    if not pdf_path.exists():
        raise RuntimeError(f"PDF was not created at expected path: {pdf_path}")

    return ResumeRenderResult(
        yaml_path=str(yaml_path),
        pdf_path=str(pdf_path),
        artifact_dir=str(artifact_dir),
    )


def render_resume_from_yaml(
    resume_yaml: str,
    *,
    company_name: str,
    position_title: str,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
) -> ResumeRenderResult:
    return asyncio.run(
        render_resume_from_yaml_async(
            resume_yaml,
            company_name=company_name,
            position_title=position_title,
            output_root=output_root,
        )
    )


def parse_resume_yaml(resume_yaml: str) -> ResumeData:
    try:
        parsed = yaml.safe_load(resume_yaml)
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML: {exc}") from exc

    if not isinstance(parsed, dict):
        raise ValueError("resume_yaml must deserialize to a mapping")

    try:
        return ResumeData.model_validate(parsed)
    except ValidationError as exc:
        raise ValueError(f"Resume data does not match required schema: {exc}") from exc


def build_resume_html(data: ResumeData) -> str:
    template = _load_package_text("templates/resume.html")
    styles = _load_package_text("templates/resume.css")
    content = [
        _render_header(data.basic),
        _render_objective(data.objective),
        _render_experience(data.experiences),
        _render_projects(data.projects),
        _render_education(data.education),
        _render_skills(data.skills),
    ]
    return Template(template).substitute(
        styles=styles,
        title=_escape_text(f"{data.basic.name} Resume"),
        candidate_name=_escape_text(data.basic.name),
        content="\n".join(part for part in content if part),
    )


async def render_pdf_from_html(html_document: str, pdf_path: Path) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.set_content(html_document, wait_until="load")
            await page.emulate_media(media="print")
            await page.pdf(
                path=str(pdf_path),
                format="A4",
                print_background=True,
                margin={
                    "top": "0.4in",
                    "right": "0.4in",
                    "bottom": "0.4in",
                    "left": "0.4in",
                },
            )
        except Exception as exc:  # pragma: no cover - playwright exception types vary
            raise RuntimeError(f"Failed to build PDF with Chromium: {exc}") from exc
        finally:
            await browser.close()


def _render_header(basic: BasicInfo) -> str:
    contact_parts = [
        basic.email,
        basic.phone,
        basic.address,
        *basic.websites,
    ]
    return "\n".join(
        [
            f"<header class='resume-header'><h1>{_escape_text(basic.name.upper())}</h1>",
            f"<p class='contact-line'>{_escape_text(' | '.join(filter(None, contact_parts)))}</p></header>",
        ]
    )


def _render_objective(objective: str) -> str:
    if not objective.strip():
        return ""
    return _wrap_section(
        "Professional Summary",
        f"<p class='summary'>{_escape_text(objective)}</p>",
    )


def _render_experience(experiences: list[ExperienceEntry]) -> str:
    blocks: list[str] = []
    for experience in experiences:
        title_rows = []
        for index, title in enumerate(experience.titles):
            company = experience.company if index == 0 else ""
            date_range = f"{title.startdate} - {title.enddate}"
            location = experience.location if index == 0 else ""
            title_rows.append(
                "\n".join(
                    [
                        "<div class='entry'>",
                        _two_column_row(company, date_range, left_class="strong", right_class="meta"),
                        _two_column_row(title.name, location, left_class="emphasis", right_class="meta emphasis"),
                        "</div>",
                    ]
                )
            )
        bullets = _render_bullet_list(experience.highlights)
        blocks.append(f"<article class='block'>{''.join(title_rows)}{bullets}</article>")
    return _wrap_section("Experience", "".join(blocks))


def _render_projects(projects: list[ProjectEntry]) -> str:
    blocks: list[str] = []
    for project in projects:
        heading = _escape_text(project.name)
        if project.link.strip():
            heading = f"<a href='{_escape_attr(project.link)}'>{heading}</a>"
        tech = (
            f"<span class='project-tech'>({_escape_text(project.technologies)})</span>"
            if project.technologies.strip()
            else ""
        )
        bullets = _render_bullet_list(project.highlights)
        blocks.append(
            "\n".join(
                [
                    "<article class='block'>",
                    f"<p class='project-heading'><span class='strong'>{heading}</span>{tech}</p>",
                    bullets,
                    "</article>",
                ]
            )
        )
    return _wrap_section("Projects", "".join(blocks))


def _render_education(education_entries: list[EducationEntry]) -> str:
    blocks: list[str] = []
    for education in education_entries:
        degree_rows = []
        for index, degree in enumerate(education.degrees):
            school = education.school if index == 0 else ""
            degree_names = ", ".join(degree.names)
            left = degree_names
            if degree.gpa.strip():
                left = f"{left} (GPA: {degree.gpa})"
            degree_rows.append(
                _two_column_row(
                    f"{school}, {left}" if school else left,
                    degree.dates,
                    left_class="strong",
                    right_class="meta",
                )
            )
        blocks.append(f"<article class='block'>{''.join(degree_rows)}</article>")
    return _wrap_section("Education", "".join(blocks))


def _render_skills(skill_categories: list[SkillCategory]) -> str:
    lines: list[str] = []
    for category in skill_categories:
        if category.subcategories:
            for subcategory in category.subcategories:
                lines.append(
                    "<p class='skill-line'>"
                    f"<span class='strong'>{_escape_text(subcategory.name)}:</span> "
                    f"{_escape_text(', '.join(subcategory.skills))}"
                    "</p>"
                )
            continue
        lines.append(
            "<p class='skill-line'>"
            f"<span class='strong'>{_escape_text(category.category)}:</span> "
            f"{_escape_text(', '.join(category.skills or []))}"
            "</p>"
        )
    return _wrap_section("Skills", "".join(lines))


def _render_bullet_list(items: list[str]) -> str:
    bullet_items = "".join(
        f"<li>{_escape_text(item)}</li>"
        for item in items
    )
    return f"<ul class='bullet-list'>{bullet_items}</ul>"


def _two_column_row(left: str, right: str, *, left_class: str = "", right_class: str = "") -> str:
    left_classes = " ".join(part for part in ["left", left_class] if part)
    right_classes = " ".join(part for part in ["right", right_class] if part)
    return (
        "<div class='two-column-row'>"
        f"<div class='{left_classes}'>{_escape_text(left)}</div>"
        f"<div class='{right_classes}'>{_escape_text(right)}</div>"
        "</div>"
    )


def _wrap_section(title: str, body: str) -> str:
    return (
        "<section class='resume-section'>"
        f"<h2>{_escape_text(title)}</h2>"
        f"{body}"
        "</section>"
    )


def _load_package_text(relative_path: str) -> str:
    package_root = resources.files("resume_renderer_mcp")
    return package_root.joinpath(relative_path).read_text(encoding="utf-8")


def _escape_text(value: str) -> str:
    return html.escape(value, quote=False)


def _escape_attr(value: str) -> str:
    return html.escape(value, quote=True)


def sanitize_path_component(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]+', "_", value.strip())
    cleaned = re.sub(r"\s+", "_", cleaned).strip(" ._")
    if not cleaned:
        raise ValueError("path component must not be empty")
    return cleaned
