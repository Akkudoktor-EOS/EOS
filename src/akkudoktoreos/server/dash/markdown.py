"""Markdown rendering with MonsterUI HTML classes."""

import base64
import mimetypes
from pathlib import Path
from typing import Any, List, Optional, Union

from fasthtml.common import FT, Div, NotStr
from markdown_it import MarkdownIt
from markdown_it.renderer import RendererHTML
from markdown_it.token import Token
from monsterui.foundations import stringify

# Where to find the static data assets
ASSETS_DIR = Path(__file__).parent / "assets"

ASSETS_PREFIX = "/eosdash/assets/"
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico"}


def file_to_data_uri(file_path: Path) -> str:
    """Convert a file to a data URI.

    Args:
        file_path: Path to the file to convert.

    Returns:
        str: Data URI string with format data:mime/type;base64,encoded_data
    """
    ext = file_path.suffix.lower()

    # Determine MIME type
    mime, _ = mimetypes.guess_type(str(file_path))
    if mime is None:
        mime = f"image/{ext.lstrip('.')}"

    # Read file as bytes and encode to base64
    raw = file_path.read_bytes()
    encoded = base64.b64encode(raw).decode("ascii")

    return f"data:{mime};base64,{encoded}"


def render_heading(
    self: RendererHTML, tokens: List[Token], idx: int, options: dict, env: dict
) -> str:
    """Custom renderer for Markdown headings with MonsterUI styling."""
    if tokens[idx].markup == "#":
        tokens[idx].attrSet(
            "class",
            "scroll-m-20 text-4xl font-extrabold tracking-tight lg:text-5xl mt-8 mb-4 border-b pb-2",
        )
    elif tokens[idx].markup == "##":
        tokens[idx].attrSet(
            "class", "scroll-m-20 border-b pb-2 text-3xl font-semibold tracking-tight mt-6 mb-3"
        )
    elif tokens[idx].markup == "###":
        tokens[idx].attrSet("class", "scroll-m-20 text-2xl font-semibold tracking-tight mt-5 mb-2")
    elif tokens[idx].markup == "####":
        tokens[idx].attrSet("class", "scroll-m-20 text-xl font-semibold tracking-tight mt-4 mb-2")

    return self.renderToken(tokens, idx, options, env)


def render_paragraph(
    self: RendererHTML, tokens: List[Token], idx: int, options: dict, env: dict
) -> str:
    """Custom renderer for Markdown paragraphs with MonsterUI styling."""
    tokens[idx].attrSet("class", "leading-7 [&:not(:first-child)]:mt-6")
    return self.renderToken(tokens, idx, options, env)


def render_blockquote(
    self: RendererHTML, tokens: List[Token], idx: int, options: dict, env: dict
) -> str:
    """Custom renderer for Markdown blockquotes with MonsterUI styling."""
    tokens[idx].attrSet("class", "mt-6 border-l-2 pl-6 italic border-primary")
    return self.renderToken(tokens, idx, options, env)


def render_list(self: RendererHTML, tokens: List[Token], idx: int, options: dict, env: dict) -> str:
    """Custom renderer for lists with MonsterUI styling."""
    tokens[idx].attrSet("class", "my-6 ml-6 list-disc [&>li]:mt-2")
    return self.renderToken(tokens, idx, options, env)


def render_image(
    self: RendererHTML, tokens: List[Token], idx: int, options: dict, env: dict
) -> str:
    """Custom renderer for Markdown images with MonsterUI styling."""
    token = tokens[idx]
    src = token.attrGet("src")
    alt = token.content or ""

    if src:
        pos = src.find(ASSETS_PREFIX)
        if pos != -1:
            asset_rel = src[pos + len(ASSETS_PREFIX) :]
            fs_path = ASSETS_DIR / asset_rel

            if fs_path.exists():
                data_uri = file_to_data_uri(fs_path)
                token.attrSet("src", data_uri)
                # MonsterUI/shadcn styling for images
                token.attrSet("class", "rounded-lg border my-6 max-w-full h-auto")

    return self.renderToken(tokens, idx, options, env)


def render_link(self: RendererHTML, tokens: List[Token], idx: int, options: dict, env: dict) -> str:
    """Custom renderer for Markdown links with MonsterUI styling."""
    token = tokens[idx]
    href = token.attrGet("href")

    if href:
        pos = href.find(ASSETS_PREFIX)
        if pos != -1:
            asset_rel = href[pos + len(ASSETS_PREFIX) :]
            key = asset_rel.rsplit(".", 1)[0]
            if key in env:
                return str(env[key])

    # MonsterUI link styling
    token.attrSet(
        "class", "font-medium text-primary underline underline-offset-4 hover:text-primary/80"
    )
    token.attrSet("target", "_blank")
    return self.renderToken(tokens, idx, options, env)


# Register all renderers
markdown = MarkdownIt("gfm-like")
markdown.add_render_rule("heading_open", render_heading)
markdown.add_render_rule("paragraph_open", render_paragraph)
markdown.add_render_rule("blockquote_open", render_blockquote)
markdown.add_render_rule("link_open", render_link)
markdown.add_render_rule("image", render_image)
markdown.add_render_rule("bullet_list_open", render_list)
markdown.add_render_rule("ordered_list_open", render_list)


# Updated wrapper class to match shadcn/ui theme
markdown_cls = "text-foreground space-y-4"

# markdown_cls = "bg-background text-lg ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"


def Markdown(*c: Any, cls: Optional[Union[str, tuple]] = None, **kwargs: Any) -> FT:
    """Component to render Markdown content with custom styling.

    Parameters:
        c: Markdown content to be rendered.
        cls: Optional additional CSS classes to be added.
        kwargs: Additional keyword arguments for the Div component.

    Returns:
        An FT object representing the rendered HTML content wrapped in a Div component.
    """
    new_cls = markdown_cls
    if cls:
        new_cls += f" {stringify(cls)}"
    kwargs["cls"] = new_cls
    md_html = markdown.render(*c)
    return Div(NotStr(md_html), **kwargs)
