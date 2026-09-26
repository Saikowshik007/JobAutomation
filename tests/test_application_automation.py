from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str((Path(__file__).resolve().parent.parent / "mcp" / "application_automation_mcp" / "src").resolve()))

from application_automation_mcp.resolver import (
    ApplicationField,
    get_application_profile_yaml_template,
    parse_application_profile_yaml,
    resolve_application_fields,
)


def sample_profile_yaml() -> str:
    return """
personal:
  first_name: "Sai"
  last_name: "Ananthula"
  full_name: "Sai Kowshik Ananthula"
  email: "askowshik@outlook.com"
  phone: "+1 (470)-815-6620"
  location: "Austin, TX"
  linkedin_url: "https://linkedin.com/in/ask7"
  github_url: "https://github.com/saikowshik007"
  portfolio_url: "https://saikowshik007.github.io"
  website_url: "https://saikowshik007.github.io"
work_authorization:
  work_authorized_us: true
  require_visa_sponsorship: false
  eligible_for_clearance: false
  willing_to_relocate: true
  comfortable_remote: true
compensation:
  desired_base_salary_usd: "140000"
  notice_period: "2 weeks"
education:
  school: "Georgia State University"
  degree: "M.S. Computer Science"
  graduation_year: "2023"
documents:
  default_resume_path: "resume/OriginalResume/Base/Sai_Kowshik_Ananthula.pdf"
  default_cover_letter_path: ""
known_questions:
  "What is your favorite programming language?": "Python"
""".strip()


def test_get_application_profile_yaml_template_reads_template() -> None:
    workspace_root = Path(__file__).resolve().parent.parent

    result = get_application_profile_yaml_template(workspace_root=workspace_root)

    assert Path(result.template_path) == (workspace_root / "templates" / "application-profile-template.yaml").resolve()
    assert "work_authorization" in result.yaml_template


def test_parse_application_profile_yaml_validates_schema() -> None:
    profile = parse_application_profile_yaml(sample_profile_yaml())

    assert profile.personal.email == "askowshik@outlook.com"
    assert profile.work_authorization.work_authorized_us is True


class FakeEmbedder:
    def encode(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            normalized = text.lower()
            score_map = [
                5.0 if "first name" in normalized else 0.0,
                5.0 if "email" in normalized else 0.0,
                5.0 if "authorized" in normalized else 0.0,
                5.0 if "sponsorship" in normalized else 0.0,
                5.0 if "favorite programming language" in normalized else 0.0,
                1.0 if "book" in normalized else 0.0,
            ]
            vectors.append(score_map)
        return vectors


def test_resolve_application_fields_uses_local_embedding_model(monkeypatch) -> None:
    monkeypatch.setattr("application_automation_mcp.resolver._get_embedder", lambda: FakeEmbedder())
    result = resolve_application_fields(
        [
            ApplicationField(field_id="first_name", label="First Name"),
            ApplicationField(field_id="email", label="Email Address"),
            ApplicationField(field_id="auth", label="Are you legally authorized to work in the United States?"),
        ],
        sample_profile_yaml(),
    )

    assert len(result.resolved_fields) == 3
    assert result.resolved_fields[0].value == "Sai"
    assert result.resolved_fields[1].value == "askowshik@outlook.com"
    assert result.resolved_fields[2].value == "Yes"
    assert not result.unresolved_fields
    assert all(field.resolution_source == "local_model" for field in result.resolved_fields)


def test_resolve_application_fields_can_select_known_questions_via_local_model(monkeypatch) -> None:
    monkeypatch.setattr("application_automation_mcp.resolver._get_embedder", lambda: FakeEmbedder())
    result = resolve_application_fields(
        [ApplicationField(field_id="essay", label="What is your favorite programming language?")],
        sample_profile_yaml(),
    )

    assert result.resolved_fields[0].resolution_source == "local_model"
    assert result.resolved_fields[0].value == "Python"


def test_resolve_application_fields_maps_sponsorship_without_hardcoded_aliases(monkeypatch) -> None:
    monkeypatch.setattr("application_automation_mcp.resolver._get_embedder", lambda: FakeEmbedder())
    result = resolve_application_fields(
        [
            ApplicationField(
                field_id="sponsorship",
                label="Will you need employment sponsorship now or later?",
                field_type="select",
                options=["Yes", "No"],
            )
        ],
        sample_profile_yaml(),
    )

    assert result.resolved_fields[0].resolution_source == "local_model"
    assert result.resolved_fields[0].canonical_key == "work_authorization.require_visa_sponsorship"
    assert result.resolved_fields[0].value == "No"
    assert result.resolved_fields[0].confidence >= 0.58


def test_resolve_application_fields_leaves_unknown_fields_unresolved(monkeypatch) -> None:
    monkeypatch.setattr("application_automation_mcp.resolver._get_embedder", lambda: FakeEmbedder())
    result = resolve_application_fields(
        [ApplicationField(field_id="custom", label="Tell us about your favorite book in 200 words")],
        sample_profile_yaml(),
        similarity_threshold=0.95,
    )

    assert not result.resolved_fields
    assert result.unresolved_fields[0].resolution_source == "unresolved"
