from __future__ import annotations

import re
from pathlib import Path
from xml.sax.saxutils import escape

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from reportlab.lib.colors import black
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


RESUMES_DIRNAME = "resume"
DEFAULT_TEMPLATE_PATH = Path("templates") / "resume-template.yaml"
DEFAULT_OUTPUT_ROOT = Path(__file__).resolve().parents[4]
PAGE_WIDTH, _ = A4
PAGE_HORIZONTAL_PADDING = 40
PAGE_VERTICAL_PADDING = 30
CONTENT_WIDTH = PAGE_WIDTH - (PAGE_HORIZONTAL_PADDING * 2)
LEFT_COLUMN_WIDTH = 352
RIGHT_COLUMN_WIDTH = CONTENT_WIDTH - LEFT_COLUMN_WIDTH
BULLET_COLUMN_WIDTH = 8
BULLET_TEXT_WIDTH = CONTENT_WIDTH - BULLET_COLUMN_WIDTH


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


def render_resume_from_yaml(
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

    build_resume_pdf(data, pdf_path)

    if not pdf_path.exists():
        raise RuntimeError(f"PDF was not created at expected path: {pdf_path}")

    return ResumeRenderResult(
        yaml_path=str(yaml_path),
        pdf_path=str(pdf_path),
        artifact_dir=str(artifact_dir),
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


def build_resume_pdf(data: ResumeData, pdf_path: Path) -> None:
    styles = build_styles()
    story: list[object] = []

    story.append(Paragraph(escape(data.basic.name.upper()), styles["header"]))
    contact_parts = [
        data.basic.email,
        data.basic.phone,
        data.basic.address,
        *data.basic.websites,
    ]
    story.append(Paragraph(escape(" | ".join(filter(None, contact_parts))), styles["contact"]))
    story.append(Spacer(1, 0.12 * inch))

    if data.objective.strip():
        add_section(story, "Professional Summary", styles)
        story.append(Paragraph(escape(data.objective), styles["body"]))
        story.append(Spacer(1, 0.08 * inch))

    add_section(story, "Experience", styles)
    for experience in data.experiences:
        add_experience_block(story, experience, styles)

    add_section(story, "Projects", styles)
    for project in data.projects:
        add_project_block(story, project, styles)

    add_section(story, "Education", styles)
    for education in data.education:
        add_education_block(story, education, styles)

    add_section(story, "Skills", styles)
    for skill_category in data.skills:
        add_skills_block(story, skill_category, styles)

    try:
        document = SimpleDocTemplate(
            str(pdf_path),
            pagesize=A4,
            leftMargin=PAGE_HORIZONTAL_PADDING,
            rightMargin=PAGE_HORIZONTAL_PADDING,
            topMargin=PAGE_VERTICAL_PADDING,
            bottomMargin=PAGE_VERTICAL_PADDING,
        )
        document.build(story)
    except Exception as exc:  # pragma: no cover - reportlab exception types vary
        raise RuntimeError(f"Failed to build PDF: {exc}") from exc


def build_styles() -> dict[str, ParagraphStyle]:
    sample = getSampleStyleSheet()
    return {
        "header": ParagraphStyle(
            "ResumeHeader",
            parent=sample["Heading1"],
            fontName="Times-Bold",
            fontSize=13,
            leading=14,
            alignment=TA_CENTER,
            textColor=black,
            spaceAfter=2,
        ),
        "contact": ParagraphStyle(
            "ResumeContact",
            parent=sample["Normal"],
            fontName="Times-Roman",
            fontSize=9,
            leading=10,
            alignment=TA_CENTER,
            spaceAfter=5,
        ),
        "section": ParagraphStyle(
            "ResumeSection",
            parent=sample["Heading2"],
            fontName="Times-Bold",
            fontSize=10,
            leading=11,
            textColor=black,
            spaceBefore=8,
            spaceAfter=3,
            textTransform="uppercase",
        ),
        "body": ParagraphStyle(
            "ResumeBody",
            parent=sample["Normal"],
            fontName="Times-Roman",
            fontSize=10,
            leading=13,
            spaceAfter=3,
        ),
        "bodyBold": ParagraphStyle(
            "ResumeBodyBold",
            parent=sample["Normal"],
            fontName="Times-Bold",
            fontSize=10,
            leading=13,
            spaceAfter=2,
        ),
        "bodyItalic": ParagraphStyle(
            "ResumeBodyItalic",
            parent=sample["Normal"],
            fontName="Times-Italic",
            fontSize=10,
            leading=13,
            spaceAfter=1,
        ),
        "bullet": ParagraphStyle(
            "ResumeBullet",
            parent=sample["Normal"],
            fontName="Times-Roman",
            fontSize=10,
            leading=13,
            leftIndent=0,
            firstLineIndent=0,
            spaceAfter=1,
        ),
        "bulletSymbol": ParagraphStyle(
            "ResumeBulletSymbol",
            parent=sample["Normal"],
            fontName="Times-Roman",
            fontSize=10,
            leading=13,
            alignment=TA_CENTER,
        ),
        "rightMeta": ParagraphStyle(
            "ResumeRightMeta",
            parent=sample["Normal"],
            fontName="Times-Roman",
            fontSize=10,
            leading=13,
            alignment=2,
        ),
        "rightMetaItalic": ParagraphStyle(
            "ResumeRightMetaItalic",
            parent=sample["Normal"],
            fontName="Times-Italic",
            fontSize=10,
            leading=13,
            alignment=2,
        ),
    }


def add_section(story: list[object], title: str, styles: dict[str, ParagraphStyle]) -> None:
    story.append(Paragraph(escape(title), styles["section"]))
    story.append(
        HRFlowable(
            width="100%",
            thickness=1,
            color=black,
            spaceBefore=0,
            spaceAfter=3,
            lineCap="square",
        )
    )


def add_experience_block(
    story: list[object],
    experience: ExperienceEntry,
    styles: dict[str, ParagraphStyle],
) -> None:
    title = experience.titles[0]
    story.append(two_column_row(experience.company, f"{title.startdate} - {title.enddate}", styles))
    story.append(two_column_row(title.name, experience.location, styles, left_style="bodyItalic", right_style="rightMetaItalic"))
    for bullet in experience.highlights:
        story.append(bullet_row(bullet, styles))
    story.append(Spacer(1, 5))


def add_project_block(
    story: list[object],
    project: ProjectEntry,
    styles: dict[str, ParagraphStyle],
) -> None:
    heading = escape(project.name)
    if project.link.strip():
        heading = f"<link href='{escape(project.link)}' color='blue'>{heading}</link>"
    if project.technologies.strip():
        heading = f"{heading} <font name='Times-Italic'>({escape(project.technologies)})</font>"
    story.append(Paragraph(heading, styles["bodyBold"]))
    for bullet in project.highlights:
        story.append(bullet_row(bullet, styles))
    story.append(Spacer(1, 5))


def add_education_block(
    story: list[object],
    education: EducationEntry,
    styles: dict[str, ParagraphStyle],
) -> None:
    degree = education.degrees[0]
    degree_names = ", ".join(degree.names)
    heading = f"{education.school}, {degree_names}"
    if degree.gpa.strip():
        heading = f"{heading} (GPA: {degree.gpa})"
    story.append(two_column_row(heading, degree.dates, styles))
    story.append(Spacer(1, 5))


def add_skills_block(
    story: list[object],
    skill_category: SkillCategory,
    styles: dict[str, ParagraphStyle],
) -> None:
    if skill_category.subcategories:
        for subcategory in skill_category.subcategories:
            line = f"<font name='Times-Bold'>{escape(subcategory.name)}:</font> {escape(', '.join(subcategory.skills))}"
            story.append(Paragraph(line, styles["body"]))
    else:
        line = f"<font name='Times-Bold'>{escape(skill_category.category)}:</font> {escape(', '.join(skill_category.skills or []))}"
        story.append(Paragraph(line, styles["body"]))


def two_column_row(
    left: str,
    right: str,
    styles: dict[str, ParagraphStyle],
    *,
    left_style: str = "bodyBold",
    right_style: str = "rightMeta",
) -> Table:
    table = Table(
        [[Paragraph(escape(left), styles[left_style]), Paragraph(escape(right), styles[right_style])]],
        colWidths=[LEFT_COLUMN_WIDTH, RIGHT_COLUMN_WIDTH],
    )
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    return table


def bullet_row(text: str, styles: dict[str, ParagraphStyle]) -> Table:
    table = Table(
        [[Paragraph("•", styles["bulletSymbol"]), Paragraph(escape(text), styles["bullet"])]],
        colWidths=[BULLET_COLUMN_WIDTH, BULLET_TEXT_WIDTH],
    )
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    return table


def sanitize_path_component(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]+', "_", value.strip())
    cleaned = re.sub(r"\s+", "_", cleaned).strip(" ._")
    if not cleaned:
        raise ValueError("path component must not be empty")
    return cleaned
