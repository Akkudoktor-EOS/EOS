"""Markdown rendering with MonsterUI HTML classes."""

from typing import Any, List, Optional, Union

from fasthtml.common import FT, Div, NotStr
from markdown_it import MarkdownIt
from markdown_it.renderer import RendererHTML
from markdown_it.token import Token
from monsterui.foundations import stringify


def render_heading(
    self: RendererHTML, tokens: List[Token], idx: int, options: dict, env: dict
) -> str:
    """Custom renderer for Markdown headings.

    Adds specific CSS classes based on the heading level.

    Parameters:
        self: The renderer instance.
        tokens: List of tokens to be rendered.
        idx: Index of the current token.
        options: Rendering options.
        env: Environment sandbox for plugins.

    Returns:
        The rendered token as a string.
    """
    if tokens[idx].markup == "#":
        tokens[idx].attrSet("class", "uk-heading-divider uk-h1 uk-margin")
    elif tokens[idx].markup == "##":
        tokens[idx].attrSet("class", "uk-heading-divider uk-h2 uk-margin")
    elif tokens[idx].markup == "###":
        tokens[idx].attrSet("class", "uk-heading-divider uk-h3 uk-margin")
    elif tokens[idx].markup == "####":
        tokens[idx].attrSet("class", "uk-heading-divider uk-h4 uk-margin")

    # pass token to default renderer.
    return self.renderToken(tokens, idx, options, env)


def render_paragraph(
    self: RendererHTML, tokens: List[Token], idx: int, options: dict, env: dict
) -> str:
    """Custom renderer for Markdown paragraphs.

    Adds specific CSS classes.

    Parameters:
        self: The renderer instance.
        tokens: List of tokens to be rendered.
        idx: Index of the current token.
        options: Rendering options.
        env: Environment sandbox for plugins.

    Returns:
        The rendered token as a string.
    """
    tokens[idx].attrSet("class", "uk-paragraph")

    # pass token to default renderer.
    return self.renderToken(tokens, idx, options, env)


def render_blockquote(
    self: RendererHTML, tokens: List[Token], idx: int, options: dict, env: dict
) -> str:
    """Custom renderer for Markdown blockquotes.

    Adds specific CSS classes.

    Parameters:
        self: The renderer instance.
        tokens: List of tokens to be rendered.
        idx: Index of the current token.
        options: Rendering options.
        env: Environment sandbox for plugins.

    Returns:
        The rendered token as a string.
    """
    tokens[idx].attrSet("class", "uk-blockquote")

    # pass token to default renderer.
    return self.renderToken(tokens, idx, options, env)


def render_link(self: RendererHTML, tokens: List[Token], idx: int, options: dict, env: dict) -> str:
    """Custom renderer for Markdown links.

    Adds the target attribute to open links in a new tab.

    Parameters:
        self: The renderer instance.
        tokens: List of tokens to be rendered.
        idx: Index of the current token.
        options: Rendering options.
        env: Environment sandbox for plugins.

    Returns:
        The rendered token as a string.
    """
    tokens[idx].attrSet("class", "uk-link")
    tokens[idx].attrSet("target", "_blank")

    # pass token to default renderer.
    return self.renderToken(tokens, idx, options, env)


markdown = MarkdownIt("gfm-like")
markdown.add_render_rule("heading_open", render_heading)
markdown.add_render_rule("paragraph_open", render_paragraph)
markdown.add_render_rule("blockquote_open", render_blockquote)
markdown.add_render_rule("link_open", render_link)


markdown_cls = "bg-background text-lg ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"


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
