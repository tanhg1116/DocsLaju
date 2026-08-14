"""Safe server-side Markdown compilation for live preview and export."""

from __future__ import annotations

import html
import re
import secrets

import markdown


MATH_EXPRESSION_RE = re.compile(
    r"\$\$.*?\$\$|\\\[.*?\\\]|\\\(.*?\\\)|"
    r"(?<!\$)\$(?![\s$])[^\n$]*?(?<!\s)\$(?![\d$])",
    re.DOTALL,
)
ASSET_REFERENCE_RE = re.compile(r"(!\[[^\]]*\]\()assets/([^\s)]+)(\))")


def compile_markdown(source: str, asset_urls: dict[str, str] | None = None) -> str:
    """Compile Markdown while preserving LaTeX for the browser KaTeX pass."""
    math_expressions: list[tuple[str, str]] = []
    placeholder_prefix = f"DOCSLAJUMATH{secrets.token_hex(12).upper()}"

    def stash_math(match: re.Match[str]) -> str:
        placeholder = f"{placeholder_prefix}{len(math_expressions)}END"
        expression = match.group(0)
        if expression.startswith("$") and not expression.startswith("$$"):
            expression = rf"\({expression[1:-1]}\)"
        math_expressions.append((placeholder, expression))
        return placeholder

    if asset_urls:
        source = ASSET_REFERENCE_RE.sub(
            lambda match: (
                f"{match.group(1)}{asset_urls.get(match.group(2), match.group(0))}{match.group(3)}"
                if match.group(2) in asset_urls
                else match.group(0)
            ),
            source,
        )
    protected_source = MATH_EXPRESSION_RE.sub(stash_math, source)
    safe_source = html.escape(protected_source, quote=False)
    rendered = markdown.markdown(
        safe_source,
        extensions=["tables", "fenced_code", "sane_lists"],
    )
    for placeholder, expression in math_expressions:
        rendered = rendered.replace(placeholder, html.escape(expression, quote=False))
    return rendered
