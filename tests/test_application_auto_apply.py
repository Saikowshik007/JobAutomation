from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str((Path(__file__).resolve().parent.parent / "mcp" / "application_automation_mcp" / "src").resolve()))

from application_automation_mcp.auto_apply import (
    BrowserField,
    _apply_confidence_policy,
    _auth_state_path,
    _choose_next_field,
    _detect_platform_from_url,
    _field_signature,
    _policy_threshold,
    _resolve_upload_path,
)
from application_automation_mcp.resolver import FieldResolutionResult, ResolvedField


def test_policy_thresholds_are_ordered() -> None:
    assert _policy_threshold("strict") > _policy_threshold("balanced") > _policy_threshold("aggressive")


def test_apply_confidence_policy_filters_low_confidence_similarity_matches() -> None:
    resolution = FieldResolutionResult(
        resolved_fields=[
            ResolvedField(
                field_id="salary",
                field_label="Expected compensation",
                canonical_key="compensation.desired_base_salary_usd",
                value="140000",
                confidence=0.6,
                resolution_source="local_model",
                explanation="test",
            ),
            ResolvedField(
                field_id="email",
                field_label="Email",
                canonical_key="personal.email",
                value="askowshik@outlook.com",
                confidence=1.0,
                resolution_source="local_model",
                explanation="test",
            ),
        ],
        unresolved_fields=[],
    )

    filtered = _apply_confidence_policy(resolution, "balanced")

    assert len(filtered.resolved_fields) == 1
    assert filtered.resolved_fields[0].field_id == "email"
    assert len(filtered.unresolved_fields) == 1
    assert filtered.unresolved_fields[0].field_id == "salary"


def test_resolve_upload_path_returns_existing_repo_file() -> None:
    result = _resolve_upload_path("templates/application-profile-template.yaml")

    assert result is not None
    assert result.name == "application-profile-template.yaml"


def test_choose_next_field_skips_seen_signatures() -> None:
    browser_field = BrowserField(
        field_id="email",
        label="Email Address",
        field_type="email",
        selector="#email",
        tag_name="input",
    )
    resolved_field = ResolvedField(
        field_id="email",
        field_label="Email Address",
        canonical_key="personal.email",
        value="askowshik@outlook.com",
        confidence=0.8,
        resolution_source="local_model",
        explanation="test",
    )
    resolution = FieldResolutionResult(
        resolved_fields=[resolved_field],
        unresolved_fields=[],
    )

    seen = {_field_signature(browser_field, resolved_field)}

    assert _choose_next_field([browser_field], resolution, seen) is None


def test_choose_next_field_returns_unseen_resolved_field() -> None:
    browser_field = BrowserField(
        field_id="email",
        label="Email Address",
        field_type="email",
        selector="#email",
        tag_name="input",
    )
    resolved_field = ResolvedField(
        field_id="email",
        field_label="Email Address",
        canonical_key="personal.email",
        value="askowshik@outlook.com",
        confidence=0.8,
        resolution_source="local_model",
        explanation="test",
    )
    resolution = FieldResolutionResult(
        resolved_fields=[resolved_field],
        unresolved_fields=[],
    )

    chosen = _choose_next_field([browser_field], resolution, set())

    assert chosen is not None
    assert chosen[0].field_id == "email"
    assert chosen[1].value == "askowshik@outlook.com"


def test_detect_platform_from_url_recognizes_greenhouse() -> None:
    assert _detect_platform_from_url("https://boards.greenhouse.io/company/jobs/123") == "greenhouse"


def test_auth_state_path_is_platform_scoped() -> None:
    root = Path("D:/projects/JobAutomation")
    path = _auth_state_path(root, "lever")

    assert path.name == "lever.json"
    assert ".auth" in str(path)
