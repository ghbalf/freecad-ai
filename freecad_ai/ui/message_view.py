"""Message rendering helpers for the chat widget.

Converts chat messages (with markdown-ish formatting and code blocks)
into HTML suitable for display in a QTextBrowser.
"""

import html
import re

# Match ```python ... ``` code blocks
CODE_BLOCK_RE = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)

# Match <think>...</think> blocks
THINK_BLOCK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL)

# Match inline `code`
INLINE_CODE_RE = re.compile(r"`([^`]+)`")

# Match **bold**
BOLD_RE = re.compile(r"\*\*(.+?)\*\*")

# Match *italic*
ITALIC_RE = re.compile(r"\*(.+?)\*")


def render_message(role: str, content: str) -> str:
    """Render a single chat message as an HTML block.

    Args:
        role: "user", "assistant", or "system"
        content: The message text (may contain markdown code blocks)

    Returns:
        HTML string for insertion into QTextBrowser
    """
    if role == "user":
        label = "You"
        bg_color = "#e3f2fd"
        label_color = "#1565c0"
    elif role == "assistant":
        label = "AI"
        bg_color = "#f5f5f5"
        label_color = "#2e7d32"
    else:
        label = "System"
        bg_color = "#fff3e0"
        label_color = "#e65100"

    formatted_content = _format_content(content)

    return (
        f'<div style="margin: 8px 0; padding: 8px 12px; '
        f'background-color: {bg_color}; border-radius: 6px;">'
        f'<div style="font-weight: bold; color: {label_color}; '
        f'margin-bottom: 4px;">{label}</div>'
        f'<div style="white-space: pre-wrap;">{formatted_content}</div>'
        f'</div>'
    )


def render_code_block(code: str, language: str = "python") -> str:
    """Render a code block as a standalone HTML element with a copy-friendly format."""
    escaped = html.escape(code.strip())
    return (
        f'<div style="margin: 6px 0; background-color: #1e1e1e; '
        f'border-radius: 4px; padding: 2px 0;">'
        f'<div style="padding: 2px 8px; font-size: 11px; color: #888;">{language}</div>'
        f'<pre style="margin: 0; padding: 8px; color: #d4d4d4; '
        f'font-family: monospace; font-size: 13px; overflow-x: auto;">'
        f'{escaped}</pre></div>'
    )


def render_execution_result(success: bool, stdout: str, stderr: str) -> str:
    """Render code execution results."""
    if success:
        icon = "&#10003;"  # checkmark
        color = "#2e7d32"
        status = "Code executed successfully"
    else:
        icon = "&#10007;"  # X
        color = "#c62828"
        status = "Execution failed"

    parts = [
        f'<div style="margin: 6px 0; padding: 8px 12px; '
        f'border-left: 3px solid {color}; background-color: #fafafa; '
        f'border-radius: 0 4px 4px 0;">'
        f'<span style="color: {color}; font-weight: bold;">'
        f'{icon} {status}</span>'
    ]

    if stdout.strip():
        escaped_out = html.escape(stdout.strip())
        parts.append(
            f'<pre style="margin: 4px 0 0 0; padding: 4px 8px; '
            f'background-color: #f0f0f0; font-size: 12px; '
            f'font-family: monospace; color: #333;">{escaped_out}</pre>'
        )

    if stderr.strip():
        escaped_err = html.escape(stderr.strip())
        parts.append(
            f'<pre style="margin: 4px 0 0 0; padding: 4px 8px; '
            f'background-color: #fce4ec; font-size: 12px; '
            f'font-family: monospace; color: #b71c1c;">{escaped_err}</pre>'
        )

    parts.append('</div>')
    return "".join(parts)


def render_tool_call(tool_name: str, call_id: str, started: bool = True,
                     success: bool = True, output: str = "") -> str:
    """Render a tool call indicator in the chat.

    Args:
        tool_name: Name of the tool being called
        call_id: Unique ID of the tool call
        started: True for "calling..." state, False for completed
        success: Whether the tool call succeeded (only used when started=False)
        output: Tool result output (only used when started=False)
    """
    if started:
        return (
            f'<div style="margin: 4px 0; padding: 6px 10px; '
            f'background-color: #e8f5e9; border-left: 3px solid #4caf50; '
            f'border-radius: 0 4px 4px 0; font-size: 12px;">'
            f'<span style="color: #2e7d32;">&#9881; Calling <b>{html.escape(tool_name)}</b>...</span>'
            f'</div>'
        )
    else:
        if success:
            icon = "&#10003;"
            color = "#2e7d32"
            bg = "#e8f5e9"
            border_color = "#4caf50"
        else:
            icon = "&#10007;"
            color = "#c62828"
            bg = "#fce4ec"
            border_color = "#ef5350"

        parts = [
            f'<div style="margin: 4px 0; padding: 6px 10px; '
            f'background-color: {bg}; border-left: 3px solid {border_color}; '
            f'border-radius: 0 4px 4px 0; font-size: 12px;">'
            f'<span style="color: {color};">{icon} <b>{html.escape(tool_name)}</b></span>'
        ]

        if output:
            escaped_output = html.escape(output.strip())
            # Truncate very long output
            if len(escaped_output) > 500:
                escaped_output = escaped_output[:500] + "..."
            parts.append(
                f'<pre style="margin: 4px 0 0 0; padding: 4px 8px; '
                f'background-color: rgba(0,0,0,0.05); font-size: 11px; '
                f'font-family: monospace; color: #333;">{escaped_output}</pre>'
            )

        parts.append('</div>')
        return "".join(parts)


def _render_thinking_block(thinking_text: str) -> str:
    """Render a <think> block as a dimmed, collapsible-style block."""
    escaped = html.escape(thinking_text.strip())
    # Truncate very long thinking
    if len(escaped) > 2000:
        escaped = escaped[:2000] + "..."
    return (
        '<div style="margin: 4px 0; padding: 4px 8px; '
        'background-color: #f0f0f0; border-left: 2px solid #ccc; '
        'font-size: 11px; color: #999; font-style: italic;">'
        '<span style="color: #aaa;">Thinking</span><br>'
        f'{escaped}</div>'
    )


def _format_content(text: str) -> str:
    """Convert markdown-ish text to HTML, handling code blocks and think blocks."""
    # First strip <think> blocks
    parts = []
    last_end = 0

    # Combine code blocks and think blocks into a single pass
    # by finding all special blocks and processing in order
    code_matches = list(CODE_BLOCK_RE.finditer(text))
    think_matches = list(THINK_BLOCK_RE.finditer(text))

    # Merge and sort all matches by start position
    all_matches = [(m, "code") for m in code_matches] + [(m, "think") for m in think_matches]
    all_matches.sort(key=lambda x: x[0].start())

    for match, match_type in all_matches:
        if match.start() < last_end:
            continue  # Skip overlapping matches

        # Process text before this block
        before = text[last_end:match.start()]
        if before:
            parts.append(_format_inline(html.escape(before)))

        if match_type == "code":
            language = match.group(1) or "python"
            code = match.group(2)
            parts.append(render_code_block(code, language))
        elif match_type == "think":
            parts.append(_render_thinking_block(match.group(1)))

        last_end = match.end()

    # Process remaining text after last block
    remaining = text[last_end:]
    if remaining:
        parts.append(_format_inline(html.escape(remaining)))

    return "".join(parts)


def _format_inline(text: str) -> str:
    """Apply inline formatting (bold, italic, inline code) to already-escaped HTML text."""
    # Inline code
    text = INLINE_CODE_RE.sub(
        r'<code style="background-color: #e0e0e0; padding: 1px 4px; '
        r'border-radius: 3px; font-family: monospace;">\1</code>',
        text
    )
    # Bold
    text = BOLD_RE.sub(r"<b>\1</b>", text)
    # Italic
    text = ITALIC_RE.sub(r"<i>\1</i>", text)
    return text
