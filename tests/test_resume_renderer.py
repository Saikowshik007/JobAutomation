from __future__ import annotations

from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str((Path(__file__).resolve().parent.parent / "mcp" / "resume_renderer_mcp" / "src").resolve()))

from resume_renderer_mcp.renderer import (
    get_resume_template_yaml,
    render_resume_from_yaml,
    sanitize_path_component,
)


def make_test_root() -> Path:
    path = (Path(__file__).resolve().parent.parent / "resume" / "test-output" / uuid4().hex[:8]).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def sample_resume_yaml() -> str:
    return """
basic:
  name: "Sai Kowshik Ananthula"
  address: "Austin, TX"
  email: "askowshik@outlook.com"
  phone: "+1 (470)-815-6620"
  websites:
    - "https://linkedin.com/in/ask7"
    - "https://saikowshik007.github.io"
objective: "Software engineer focused on backend systems, automation, and AI-assisted workflows."
education:
  - school: "Georgia State University"
    degrees:
      - names:
          - "M.S. Computer Science"
        gpa: "3.9"
        dates: "August 2021 - May 2023"
experiences:
  - company: "IBM"
    location: "Austin, TX"
    titles:
      - name: "Software Engineer"
        startdate: "January 2024"
        enddate: "February 2026"
    highlights:
      - "Built backend systems for enterprise identity and access workflows."
      - "Improved throughput and resilience of real-time event processing."
projects:
  - name: "Job Agent AI"
    technologies: "React, FastAPI, PostgreSQL, OpenAI"
    link: ""
    highlights:
      - "Built a job tracking and resume generation platform."
skills:
  - category: "Technical"
    skills:
      - "Java"
      - "Python"
      - "React"
      - "PostgreSQL"
""".strip()


def test_sanitize_path_component_normalizes_input() -> None:
    assert sanitize_path_component("  Staff Software Engineer / Platform  ") == "Staff_Software_Engineer___Platform"


def test_get_resume_template_yaml_reads_template() -> None:
    test_root = make_test_root()
    template_dir = test_root / "templates"
    template_dir.mkdir()
    template_path = template_dir / "resume-template.yaml"
    template_path.write_text("basic:\n  name: Test\n", encoding="utf-8")

    result = get_resume_template_yaml(workspace_root=test_root)

    assert Path(result.template_path) == template_path.resolve()
    assert result.yaml_template == "basic:\n  name: Test\n"


def test_render_resume_from_yaml_creates_pdf_and_yaml() -> None:
    test_root = make_test_root()

    result = render_resume_from_yaml(
        sample_resume_yaml(),
        output_root=test_root,
        company_name="OpenAI",
        position_title="Research Engineer",
    )

    yaml_path = Path(result.yaml_path)
    pdf_path = Path(result.pdf_path)
    assert yaml_path.exists()
    assert pdf_path.exists()
    assert yaml_path.name == "Sai_Kowshik_Ananthula.yaml"
    assert pdf_path.name == "Sai_Kowshik_Ananthula.pdf"
    assert pdf_path.parent == (test_root / "resume" / "OpenAI" / "Research_Engineer").resolve()


def test_render_original_resume_from_repo_template() -> None:
    workspace_root = Path(__file__).resolve().parent.parent
    template_yaml = (workspace_root / "templates" / "resume-template.yaml").read_text(encoding="utf-8")

    result = render_resume_from_yaml(
        template_yaml,
        output_root=workspace_root,
        company_name="OriginalResume",
        position_title="Base",
    )

    yaml_path = Path(result.yaml_path)
    pdf_path = Path(result.pdf_path)
    assert yaml_path.exists()
    assert pdf_path.exists()
    assert yaml_path == (workspace_root / "resume" / "OriginalResume" / "Base" / "Sai_Kowshik_Ananthula.yaml").resolve()
    assert pdf_path == (workspace_root / "resume" / "OriginalResume" / "Base" / "Sai_Kowshik_Ananthula.pdf").resolve()


def test_render_resume_from_yaml_raises_on_invalid_yaml() -> None:
    test_root = make_test_root()

    try:
        render_resume_from_yaml(
            "basic: [broken",
            output_root=test_root,
            company_name="OpenAI",
            position_title="Research Engineer",
        )
        raise AssertionError("Expected render_resume_from_yaml to raise on invalid YAML")
    except ValueError as exc:
        assert "Invalid YAML" in str(exc)


def test_render_resume_from_yaml_raises_on_schema_mismatch() -> None:
    test_root = make_test_root()

    try:
        render_resume_from_yaml(
            "basic:\n  name: ''\n",
            output_root=test_root,
            company_name="OpenAI",
            position_title="Research Engineer",
        )
        raise AssertionError("Expected render_resume_from_yaml to raise on schema mismatch")
    except ValueError as exc:
        assert "required schema" in str(exc)
