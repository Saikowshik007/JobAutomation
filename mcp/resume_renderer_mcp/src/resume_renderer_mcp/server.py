from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from resume_renderer_mcp.renderer import (
    ResumeBaseYamlResult,
    ResumeRenderResult,
    ResumeTemplateYamlResult,
    get_base_resume_yaml as get_base_resume,
    get_resume_template_yaml as get_template,
    render_resume_from_yaml_async as render_resume_async,
)


mcp = FastMCP("resume-renderer-mcp")


@mcp.tool(structured_output=True)
def get_resume_template_yaml() -> ResumeTemplateYamlResult:
    """Return the canonical YAML resume template for fixed-structure editing."""
    return get_template()


@mcp.tool(structured_output=True)
def get_base_resume_yaml() -> ResumeBaseYamlResult:
    """Return the base YAML resume used as the starting point for tailoring."""
    return get_base_resume()


@mcp.tool(structured_output=True)
async def render_resume_from_yaml(
    resume_yaml: str,
    company_name: str,
    position_title: str,
) -> ResumeRenderResult:
    """Render a fixed-structure resume PDF from validated YAML content."""
    return await render_resume_async(
        resume_yaml,
        company_name=company_name,
        position_title=position_title,
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
