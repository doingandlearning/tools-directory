#!/usr/bin/env python3
"""Build index.html, tools.json, and clean-URL tool pages from src/*.html sources."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC_DIR = ROOT / "src"
TEMPLATES_DIR = ROOT / "templates"
ASSETS_DIR = ROOT / "assets"
README_PATH = ROOT / "README.md"
TOOLS_JSON_PATH = ROOT / "tools.json"
INDEX_PATH = ROOT / "index.html"
REDIRECTS_PATH = ROOT / "_redirects"

BLOG_URL = "https://kevincunningham.co.uk/"
LOGO_URL = "https://kevincunningham.co.uk/images/brain-Circle.jpg"
SITE_TITLE = "Kevin Cunningham's Teaching Tools"


def render_template(name: str, **values: str) -> str:
    text = (TEMPLATES_DIR / name).read_text(encoding="utf-8")
    for key, value in values.items():
        text = text.replace(f"{{{{{key}}}}}", value)
    return text


def render_header() -> str:
    return render_template("header.html", blog_url=BLOG_URL, logo_url=LOGO_URL)


def render_footer() -> str:
    return render_template("footer.html", blog_url=BLOG_URL, year=str(datetime.now().year))


def render_page(title: str, content: str) -> str:
    return render_template(
        "index.html",
        title=title,
        content=content,
        header=render_header(),
        footer=render_footer(),
    )


def ordinal(value: int) -> str:
    if 10 <= value % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(value % 10, "th")
    return f"{value}{suffix}"


def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def format_display_date(dt: datetime) -> str:
    return f"{ordinal(dt.day)} {dt.strftime('%B %Y')}"


def get_file_commit_dates(file_path: Path) -> tuple[str | None, str | None]:
    try:
        result = subprocess.run(
            ["git", "log", "--format=%aI", "--", str(file_path)],
            capture_output=True,
            text=True,
            check=True,
            cwd=ROOT,
        )
        dates = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if dates:
            return dates[-1], dates[0]
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    try:
        mtime = datetime.fromtimestamp(file_path.stat().st_mtime).astimezone().isoformat()
        return mtime, mtime
    except OSError:
        return None, None


def extract_title(html_path: Path) -> str:
    try:
        content = html_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return html_path.stem.replace("-", " ").title()
    match = re.search(r"<title[^>]*>(.*?)</title>", content, re.IGNORECASE | re.DOTALL)
    if not match:
        return html_path.stem.replace("-", " ").title()
    return re.sub(r"\s+", " ", match.group(1)).strip()


def discover_tool_sources() -> list[Path]:
    return sorted(path for path in SRC_DIR.glob("*.html") if path.is_file())


def load_existing_tools() -> dict[str, dict]:
    if not TOOLS_JSON_PATH.exists():
        return {}
    try:
        tools = json.loads(TOOLS_JSON_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return {tool["slug"]: tool for tool in tools if tool.get("slug")}


def build_tools_json(sources: list[Path]) -> list[dict]:
    existing = load_existing_tools()
    tools: list[dict] = []
    for source in sources:
        slug = source.stem
        created, updated = get_file_commit_dates(source)
        prior = existing.get(slug, {})

        if prior.get("created"):
            created = prior["created"]
        elif created is None:
            created = datetime.now().astimezone().isoformat()

        if updated is None:
            updated = prior.get("updated") or created

        tools.append(
            {
                "filename": source.name,
                "slug": slug,
                "title": extract_title(source),
                "created": created,
                "updated": updated,
                "url": f"/{slug}",
            }
        )
    tools.sort(key=lambda tool: tool["title"].lower())
    return tools


def select_recent(tools: list[dict], key: str, limit: int, exclude: set[str] | None = None) -> list[dict]:
    excluded = exclude or set()
    dated = []
    for tool in tools:
        parsed = parse_iso_datetime(tool.get(key))
        if parsed is None or tool["slug"] in excluded:
            continue
        dated.append((tool, parsed))
    dated.sort(key=lambda item: item[1], reverse=True)
    return [{**tool, "parsed_date": parsed} for tool, parsed in dated[:limit]]


def render_tool_list(items: list[dict]) -> str:
    if not items:
        return "<li>No tools yet.</li>"
    rows = []
    for tool in items:
        date = tool.get("parsed_date")
        date_html = (
            f' <span class="recent-date">— {format_display_date(date)}</span>'
            if isinstance(date, datetime)
            else ""
        )
        rows.append(
            f'<li><a href="{escape(tool["url"])}">{escape(tool["slug"])}</a>{date_html}</li>'
        )
    return "\n      ".join(rows)


def markdown_to_html(markdown: str) -> str:
    lines = markdown.splitlines()
    html_parts: list[str] = []
    paragraph: list[str] = []

    def flush_paragraph() -> None:
        if not paragraph:
            return
        text = " ".join(paragraph).strip()
        paragraph.clear()
        if not text:
            return
        text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
        html_parts.append(f"<p>{text}</p>")

    for line in lines:
        stripped = line.strip()
        if not stripped:
            flush_paragraph()
            continue
        if stripped.startswith("# "):
            flush_paragraph()
            html_parts.append(f"<h1>{stripped[2:].strip()}</h1>")
            continue
        if stripped.startswith("<!--"):
            flush_paragraph()
            html_parts.append(stripped)
            continue
        paragraph.append(stripped)

    flush_paragraph()
    return "\n".join(html_parts)


def inject_recent_section(body_html: str, recent_html: str) -> str:
    start = "<!-- recently starts -->"
    end = "<!-- recently stops -->"
    if start in body_html and end in body_html:
        start_idx = body_html.index(start) + len(start)
        end_idx = body_html.index(end)
        return body_html[:start_idx] + "\n" + recent_html + "\n" + body_html[end_idx:]
    return recent_html + body_html


def build_index(tools: list[dict]) -> None:
    readme = README_PATH.read_text(encoding="utf-8") if README_PATH.exists() else f"# {SITE_TITLE}"
    body_html = markdown_to_html(readme)

    recently_added = select_recent(tools, "created", 10)
    added_slugs = {tool["slug"] for tool in recently_added}
    recently_updated = select_recent(tools, "updated", 10, exclude=added_slugs)

    recent_html = render_template(
        "recent-tools.html",
        recently_added=render_tool_list(recently_added),
        recently_updated=render_tool_list(recently_updated),
    )
    body_html = inject_recent_section(body_html, recent_html)

    if tools:
        items = "\n".join(
            f'<li><a href="{escape(tool["url"])}">{escape(tool["slug"])}</a>'
            f' <span class="tool-title">— {escape(tool["title"])}</span></li>'
            for tool in sorted(tools, key=lambda t: t["slug"])
        )
        body_html += "\n" + render_template("all-tools.html", items=items)

    INDEX_PATH.write_text(render_page(SITE_TITLE, body_html), encoding="utf-8")


def ensure_stylesheet_link(html: str) -> str:
    if 'href="/assets/css/site.css"' in html:
        return html
    link = '<link rel="icon" href="/favicon.ico">\n<link rel="stylesheet" href="/assets/css/site.css">\n'
    if "</head>" in html:
        return html.replace("</head>", f"{link}</head>", 1)
    return link + html


def inject_chrome_into_tool(html: str) -> str:
    if 'class="site-chrome"' in html:
        return html

    html = ensure_stylesheet_link(html)
    header = render_header()
    footer = render_footer()
    chrome_top = render_template("chrome-top.html", header=header)
    chrome_bottom = render_template("chrome-bottom.html", footer=footer)

    if re.search(r'<body[^>]*class="[^"]*tool-page', html, re.IGNORECASE):
        html = re.sub(r"<body[^>]*>", lambda match: match.group(0) + "\n" + chrome_top, html, count=1, flags=re.IGNORECASE)
    else:
        html = re.sub(
            r"<body([^>]*)>",
            lambda match: (
                f'<body class="tool-page"{match.group(1)}>\n{chrome_top}'
                if "class=" not in match.group(1).lower()
                else match.group(0) + "\n" + chrome_top
            ),
            html,
            count=1,
            flags=re.IGNORECASE,
        )

    html = re.sub(r"</body>", chrome_bottom + "\n</body>", html, count=1, flags=re.IGNORECASE)
    return html


def build_clean_url_pages(sources: list[Path]) -> None:
    for source in sources:
        slug = source.stem
        target_dir = ROOT / slug
        target_dir.mkdir(exist_ok=True)
        content = inject_chrome_into_tool(source.read_text(encoding="utf-8"))
        (target_dir / "index.html").write_text(content, encoding="utf-8")


def build_redirects(sources: list[Path]) -> None:
    lines = ["/index.html / 301"]
    for source in sources:
        slug = source.stem
        # Legacy links to flat .html files (no longer published).
        lines.append(f"/{slug}.html /{slug} 301")
    REDIRECTS_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def remove_stale_root_tool_html(sources: list[Path]) -> None:
    """Drop root-level tool .html files so hosts don't serve them instead of slug/index.html."""
    for source in sources:
        stale = ROOT / source.name
        if stale.exists():
            stale.unlink()
            print(f"Removed stale publish file: {stale.name}")


def copy_assets() -> None:
    favicon_src = ASSETS_DIR / "favicon.ico"
    favicon_dst = ROOT / "favicon.ico"
    if favicon_src.exists():
        shutil.copy2(favicon_src, favicon_dst)

    css_src = ASSETS_DIR / "css" / "site.css"
    css_dst = ROOT / "assets" / "css" / "site.css"
    css_dst.parent.mkdir(parents=True, exist_ok=True)
    if css_src.resolve() != css_dst.resolve():
        shutil.copy2(css_src, css_dst)


def main() -> None:
    if not TEMPLATES_DIR.exists():
        raise FileNotFoundError("templates/ directory is required")
    if not SRC_DIR.exists():
        raise FileNotFoundError("src/ directory is required")
    if not (ASSETS_DIR / "css" / "site.css").exists():
        raise FileNotFoundError("assets/css/site.css is required")

    copy_assets()
    sources = discover_tool_sources()
    tools = build_tools_json(sources)
    TOOLS_JSON_PATH.write_text(json.dumps(tools, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    build_index(tools)
    build_clean_url_pages(sources)
    remove_stale_root_tool_html(sources)
    build_redirects(sources)
    print(f"Built index with {len(tools)} tool(s).")
    for tool in tools:
        print(f"  /{tool['slug']} <- {tool['filename']}")


if __name__ == "__main__":
    main()
