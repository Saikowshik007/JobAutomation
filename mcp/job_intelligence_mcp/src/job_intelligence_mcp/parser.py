from __future__ import annotations

import asyncio
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from bs4 import BeautifulSoup
from pydantic import BaseModel
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
)

SECTION_PATTERNS = {
    "requirements": (
        "requirements",
        "qualifications",
        "what you bring",
        "who you are",
        "skills",
        "must have",
    ),
    "responsibilities": (
        "responsibilities",
        "what you'll do",
        "what you will do",
        "duties",
        "day to day",
    ),
}


@dataclass
class RenderedPage:
    final_url: str
    html: str
    title: str | None


@dataclass
class ParsedJob:
    url: str
    platform: str | None = None
    title: str | None = None
    company: str | None = None
    location: str | None = None
    employment_type: str | None = None
    apply_url: str | None = None
    description_text: str = ""
    description_html: str | None = None
    llm_input_text: str = ""
    sections: dict[str, list[str]] = field(default_factory=dict)
    requirements: list[str] = field(default_factory=list)
    responsibilities: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _prune_empty(asdict(self))


class LlmTextResult(BaseModel):
    llm_input_text: str


async def parse_job_posting_async(url: str, timeout_seconds: float = 30.0) -> dict[str, Any]:
    rendered = await _render_page(url, timeout_seconds=timeout_seconds)
    parsed = _parse_rendered_content(url, rendered)
    return parsed.to_dict()


async def get_llm_text_for_url_async(url: str, timeout_seconds: float = 30.0) -> LlmTextResult:
    parsed = await parse_job_posting_async(url, timeout_seconds=timeout_seconds)
    return LlmTextResult(llm_input_text=parsed.get("llm_input_text", ""))


def parse_job_posting(url: str, timeout_seconds: float = 30.0) -> dict[str, Any]:
    return asyncio.run(parse_job_posting_async(url, timeout_seconds=timeout_seconds))


def get_llm_text_for_url(url: str, timeout_seconds: float = 30.0) -> LlmTextResult:
    return asyncio.run(get_llm_text_for_url_async(url, timeout_seconds=timeout_seconds))


async def _render_page(url: str, timeout_seconds: float) -> RenderedPage:
    timeout_ms = int(timeout_seconds * 1000)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            context = await browser.new_context(
                user_agent=USER_AGENT,
                java_script_enabled=True,
                viewport={"width": 1440, "height": 2200},
            )
            page = await context.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            await _stabilize_page(page, timeout_ms)
            return RenderedPage(
                final_url=page.url,
                html=await page.content(),
                title=await page.title(),
            )
        finally:
            await browser.close()


async def _stabilize_page(page: Any, timeout_ms: int) -> None:
    try:
        await page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except PlaywrightTimeoutError:
        pass

    likely_job_selectors = [
        "[data-automation-id='jobPostingDescription']",
        ".posting-page",
        ".section-wrapper",
        "#content",
        "main",
        "article",
    ]
    for selector in likely_job_selectors:
        try:
            await page.locator(selector).first.wait_for(state="attached", timeout=3000)
            break
        except PlaywrightTimeoutError:
            continue


def _parse_rendered_content(source_url: str, rendered: RenderedPage) -> ParsedJob:
    soup = BeautifulSoup(rendered.html, "html.parser")
    platform = _detect_platform(rendered.final_url, soup)
    json_ld = _extract_jobposting_jsonld(soup)
    full_text = soup.get_text("\n", strip=True)

    title = _pick_first(
        _jsonld_value(json_ld, "title"),
        _jsonld_value(json_ld, "jobTitle"),
        _meta_content(soup, "og:title"),
        rendered.title,
        _document_title(soup),
    )
    company = _pick_first(
        _jsonld_hiring_org(json_ld),
        _meta_content(soup, "og:site_name"),
    )
    location = _pick_first(
        _jsonld_location(json_ld),
        _find_location_from_text(full_text),
    )
    employment_type = _pick_first(
        _jsonld_value(json_ld, "employmentType"),
        _find_employment_type(full_text),
    )
    apply_url = _pick_first(
        _jsonld_value(json_ld, "url"),
        rendered.final_url,
    )

    description_html = _extract_description_html(soup, json_ld, platform)
    description_text = _extract_description_text(soup, description_html)
    sections = _extract_sections(description_html, description_text)
    llm_input_text = _build_llm_input_text(
        title=title,
        company=company,
        location=location,
        employment_type=employment_type,
        description_text=description_text,
        requirements=sections.get("requirements", []),
        responsibilities=sections.get("responsibilities", []),
    )

    return ParsedJob(
        url=source_url,
        platform=platform,
        title=title,
        company=company,
        location=location,
        employment_type=employment_type,
        apply_url=apply_url,
        description_text=description_text,
        description_html=description_html,
        llm_input_text=llm_input_text,
        sections=sections,
        requirements=sections.get("requirements", []),
        responsibilities=sections.get("responsibilities", []),
        metadata={
            "source": {
                "fetch_mode": "playwright",
                "rendered_url": rendered.final_url,
                "has_jsonld": bool(json_ld),
            }
        },
    )


def _detect_platform(url: str, soup: BeautifulSoup) -> str | None:
    text = " ".join(
        filter(
            None,
            [
                url.lower(),
                soup.get_text(" ", strip=True).lower()[:3000],
                " ".join(meta.get("content", "") for meta in soup.find_all("meta")),
            ],
        )
    )
    if "boards.greenhouse.io" in text or "greenhouse" in text:
        return "greenhouse"
    if "jobs.lever.co" in text or "lever.co" in text:
        return "lever"
    if "myworkdayjobs.com" in text or "workday" in text:
        return "workday"
    if "ashbyhq.com" in text or "ashby" in text:
        return "ashby"
    return None


def _extract_jobposting_jsonld(soup: BeautifulSoup) -> dict[str, Any] | None:
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text(strip=True)
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        job_posting = _find_jobposting_node(payload)
        if job_posting:
            return job_posting
    return None


def _find_jobposting_node(payload: Any) -> dict[str, Any] | None:
    if isinstance(payload, dict):
        payload_type = payload.get("@type")
        if payload_type == "JobPosting" or (
            isinstance(payload_type, list) and "JobPosting" in payload_type
        ):
            return payload
        for key in ("@graph", "graph", "itemListElement"):
            nested = payload.get(key)
            if nested:
                result = _find_jobposting_node(nested)
                if result:
                    return result
    if isinstance(payload, list):
        for item in payload:
            result = _find_jobposting_node(item)
            if result:
                return result
    return None


def _jsonld_value(jobposting: dict[str, Any] | None, key: str) -> str | None:
    if not jobposting:
        return None
    value = jobposting.get(key)
    if isinstance(value, str):
        return _clean_text(value)
    return None


def _jsonld_hiring_org(jobposting: dict[str, Any] | None) -> str | None:
    if not jobposting:
        return None
    org = jobposting.get("hiringOrganization")
    if isinstance(org, dict):
        return _clean_text(org.get("name"))
    return None


def _jsonld_location(jobposting: dict[str, Any] | None) -> str | None:
    if not jobposting:
        return None
    location = jobposting.get("jobLocation")
    if isinstance(location, list) and location:
        location = location[0]
    if isinstance(location, dict):
        address = location.get("address", {})
        if isinstance(address, dict):
            parts = [
                _jsonld_scalar(address.get("addressLocality")),
                _jsonld_scalar(address.get("addressRegion")),
                _jsonld_scalar(address.get("addressCountry")),
            ]
            return _clean_text(", ".join(part for part in parts if part))
    return None


def _jsonld_scalar(value: Any) -> str | None:
    if isinstance(value, str):
        return _clean_text(value)
    if isinstance(value, dict):
        for key in ("name", "@value", "value"):
            nested = value.get(key)
            if isinstance(nested, str):
                return _clean_text(nested)
    return None


def _extract_description_html(
    soup: BeautifulSoup,
    jobposting: dict[str, Any] | None,
    platform: str | None,
) -> str | None:
    if jobposting and isinstance(jobposting.get("description"), str):
        description = jobposting["description"]
        if _looks_like_html(description):
            return description
        return None

    selectors = []
    if platform == "greenhouse":
        selectors.extend(["#content", ".content", ".opening"])
    elif platform == "lever":
        selectors.extend([".section-wrapper", ".posting-page", ".posting"])
    elif platform == "workday":
        selectors.extend(["[data-automation-id='jobPostingDescription']", "#mainContent"])

    selectors.extend(
        [
            "[data-automation-id='jobPostingDescription']",
            ".job-description",
            ".description",
            ".posting-page",
            ".section-wrapper",
            "main",
            "article",
            "[role='main']",
        ]
    )
    for selector in selectors:
        node = soup.select_one(selector)
        if node:
            cleaned = _clean_candidate_node(node)
            if cleaned:
                return cleaned
    best = _find_best_content_block(soup)
    return str(best) if best else None


def _extract_description_text(soup: BeautifulSoup, description_html: str | None) -> str:
    if description_html:
        description_soup = BeautifulSoup(description_html, "html.parser")
        extracted = _normalize_description_text(description_soup.get_text("\n", strip=True))
        if extracted:
            return extracted
    best = _find_best_content_block(soup)
    if best:
        return _normalize_description_text(best.get_text("\n", strip=True)) or ""
    return _normalize_description_text(soup.get_text("\n", strip=True)) or ""


def _extract_sections(description_html: str | None, description_text: str) -> dict[str, list[str]]:
    if description_html:
        section_map = _extract_sections_from_html(description_html)
        if section_map:
            return section_map
    return _extract_sections_from_text(description_text)


def _extract_sections_from_html(description_html: str) -> dict[str, list[str]]:
    soup = BeautifulSoup(description_html, "html.parser")
    headers = soup.find_all(re.compile("^h[1-6]$"))
    if not headers:
        return {}

    section_map: dict[str, list[str]] = {}
    for header in headers:
        header_text = (_clean_text(header.get_text(" ", strip=True)) or "").lower()
        matched_name = _match_section_name(header_text)
        if not matched_name:
            continue

        items: list[str] = []
        sibling = header.find_next_sibling()
        while sibling and sibling.name not in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            if sibling.name in {"ul", "ol"}:
                items.extend(_collect_list_items(sibling))
            elif sibling.name == "p":
                text = _clean_text(sibling.get_text(" ", strip=True))
                if text:
                    items.append(text)
            sibling = sibling.find_next_sibling()

        if items:
            section_map[matched_name] = items
    return section_map


def _extract_sections_from_text(description_text: str) -> dict[str, list[str]]:
    lines = [line.strip() for line in description_text.splitlines() if line.strip()]
    section_map: dict[str, list[str]] = {}
    current_name: str | None = None

    for line in lines:
        matched_name = _match_section_name(line.lower())
        if matched_name and len(line) < 80:
            current_name = matched_name
            section_map.setdefault(current_name, [])
            continue
        if current_name and _looks_like_bullet(line):
            normalized = _normalize_bullet(line)
            if normalized:
                section_map[current_name].append(normalized)

    return {name: items for name, items in section_map.items() if items}


def _match_section_name(header_text: str) -> str | None:
    for section_name, patterns in SECTION_PATTERNS.items():
        if any(pattern in header_text for pattern in patterns):
            return section_name
    return None


def _collect_list_items(node: Any) -> list[str]:
    items: list[str] = []
    for li in node.find_all("li", recursive=False):
        text = _clean_text(li.get_text(" ", strip=True))
        if text:
            items.append(text)
    return items


def _meta_content(soup: BeautifulSoup, property_name: str) -> str | None:
    meta = soup.find("meta", attrs={"property": property_name}) or soup.find(
        "meta", attrs={"name": property_name}
    )
    if meta and meta.get("content"):
        return _clean_text(meta["content"])
    return None


def _document_title(soup: BeautifulSoup) -> str | None:
    if soup.title and soup.title.string:
        return _clean_text(soup.title.string)
    return None


def _find_location_from_text(text: str) -> str | None:
    match = re.search(
        r"\b(remote|hybrid|onsite|on-site)\b|([A-Z][a-zA-Z]+,\s?[A-Z]{2})",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    return _clean_text(match.group(0))


def _find_employment_type(text: str) -> str | None:
    patterns = (
        "full-time",
        "part-time",
        "contract",
        "internship",
        "temporary",
    )
    lowered = text.lower()
    for pattern in patterns:
        if pattern in lowered:
            return pattern
    return None


def _looks_like_bullet(line: str) -> bool:
    return bool(re.match(r"^([-*•]|[0-9]+\.)\s+", line))


def _normalize_bullet(line: str) -> str | None:
    return _clean_text(re.sub(r"^([-*•]|[0-9]+\.)\s+", "", line))


def _build_llm_input_text(
    *,
    title: str | None,
    company: str | None,
    location: str | None,
    employment_type: str | None,
    description_text: str,
    requirements: list[str],
    responsibilities: list[str],
) -> str:
    parts: list[str] = []
    if title:
        parts.append(f"Title: {title}")
    if company:
        parts.append(f"Company: {company}")
    if location:
        parts.append(f"Location: {location}")
    if employment_type:
        parts.append(f"Employment Type: {employment_type}")
    if responsibilities:
        parts.append("Responsibilities:")
        parts.extend(f"- {item}" for item in responsibilities)
    if requirements:
        parts.append("Requirements:")
        parts.extend(f"- {item}" for item in requirements)
    if description_text:
        parts.append("Full Description:")
        parts.append(description_text)
    return "\n".join(parts)


def _looks_like_html(value: str) -> bool:
    return bool(re.search(r"<[a-zA-Z][^>]*>", value))


def _clean_candidate_node(node: Any) -> str | None:
    cloned = BeautifulSoup(str(node), "html.parser")
    for bad in cloned.select(
        "script, style, noscript, nav, footer, header, aside, form, button, svg, img, "
        ".share-widget, .job-actions, .similar-jobs, .related-jobs, .apply-now, .referral"
    ):
        bad.decompose()
    cleaned = str(cloned)
    text = _normalize_description_text(cloned.get_text("\n", strip=True))
    if not text or len(text) < 200:
        return None
    return cleaned


def _find_best_content_block(soup: BeautifulSoup) -> Any:
    candidates = soup.select("main, article, section, div")
    best_node = None
    best_score = 0.0
    for node in candidates:
        score = _score_content_node(node)
        if score > best_score:
            best_score = score
            best_node = node
    return best_node


def _score_content_node(node: Any) -> float:
    text = _normalize_description_text(node.get_text("\n", strip=True)) or ""
    if len(text) < 200:
        return 0.0
    lower = text.lower()
    if "apply now" in lower and len(text) < 600:
        return 0.0

    paragraphs = len(node.find_all("p"))
    lists = len(node.find_all(["ul", "ol"]))
    items = len(node.find_all("li"))
    headings = len(node.find_all(re.compile("^h[1-6]$")))
    penalties = len(
        node.find_all(
            [
                "nav",
                "footer",
                "header",
                "aside",
                "form",
                "button",
            ]
        )
    )
    bonus_terms = (
        "responsibilities",
        "qualifications",
        "requirements",
        "job description",
        "about the role",
        "what you'll do",
    )
    bonuses = sum(1 for term in bonus_terms if term in lower)
    return (
        len(text)
        + paragraphs * 80
        + lists * 120
        + items * 25
        + headings * 100
        + bonuses * 250
        - penalties * 200
    )


def _normalize_description_text(value: str | None) -> str | None:
    cleaned = _clean_text(value)
    if not cleaned:
        return None
    cleaned = re.sub(r"\b(View More Jobs|Similar Jobs|See More Jobs)\b", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\b(Apply Now|Request a referral from an .*?employee\.)\b", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    return re.sub(r"\s+", " ", value).strip()


def _pick_first(*values: str | None) -> str | None:
    for value in values:
        if value:
            return value
    return None


def _prune_empty(value: Any) -> Any:
    if isinstance(value, dict):
        pruned: dict[str, Any] = {}
        for key, nested in value.items():
            cleaned = _prune_empty(nested)
            if cleaned is None:
                continue
            if cleaned == "":
                continue
            if cleaned == []:
                continue
            if cleaned == {}:
                continue
            pruned[key] = cleaned
        return pruned
    if isinstance(value, list):
        pruned_list = [_prune_empty(item) for item in value]
        return [item for item in pruned_list if item not in (None, "", [], {})]
    return value
