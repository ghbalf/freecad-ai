"""Example file_attach hook — convert documents to text for the LLM.

This is a SAMPLE hook. Copy it to your hooks directory to enable it:

    cp -r docs/hooks/file-attach-example/ \
          ~/.config/FreeCAD/FreeCADAI/hooks/file-attach/

Then install the tools you need (see below).

────────────────────────────────────────────────────────────────────
WHY A HOOK INSTEAD OF A BUILT-IN LIBRARY?
────────────────────────────────────────────────────────────────────

FreeCAD AI has a strict zero-external-dependencies policy. The workbench
runs inside FreeCAD's bundled Python and must not require pip-installed
packages. PDF/DOCX parsing needs external C libraries (poppler, libxml2)
that cannot be bundled portably.

Instead, we provide two extensibility paths:

1. **Hooks** (this file) — lightweight, user-installed shell commands.
   The hook calls whatever CLI tools the user has installed (pandoc,
   pdftotext, libreoffice, etc.). Plain text output only — embedded
   images in documents are not preserved. Best for simple conversions.

2. **MCP servers** — for rich conversion with images, tables, and
   structure preservation. Install an MCP server like `markdownify-mcp`
   or `mcp-pdf2md` in Settings → MCP Servers. The LLM can then call
   the server's conversion tools directly, getting full fidelity
   including embedded images.

Choose hooks for quick & simple, MCP for full-featured conversion.

────────────────────────────────────────────────────────────────────
REQUIREMENTS (install the ones you need)
────────────────────────────────────────────────────────────────────

PDF:   sudo apt install poppler-utils     (provides pdftotext)
 -or-  sudo apt install pandoc             (universal converter)

DOCX:  sudo apt install pandoc
 -or-  sudo apt install libreoffice-core   (provides soffice --convert-to)

XLSX:  sudo apt install gnumeric           (provides ssconvert)
 -or-  pip install xlsx2csv                (lightweight Python tool)

ODT:   sudo apt install pandoc

RTF:   sudo apt install pandoc
"""
import logging
import os
import shutil
import subprocess
import tempfile

from freecad_ai.utils.proc import hidden_process_kwargs

logger = logging.getLogger("freecad_ai.hooks.file_attach")

# Map of extension → list of converter strategies (tried in order)
# Each strategy: (required_command, converter_function)

def _convert_with_pdftotext(path):
    """Convert PDF to text using poppler's pdftotext."""
    result = subprocess.run(
        ["pdftotext", "-layout", path, "-"],
        capture_output=True, text=True, timeout=30,
        **hidden_process_kwargs(),
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout
    return None


def _convert_with_pandoc(path):
    """Convert any supported format to plain text via pandoc."""
    result = subprocess.run(
        ["pandoc", "-t", "plain", "--wrap=none", path],
        capture_output=True, text=True, timeout=30,
        **hidden_process_kwargs(),
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout
    return None


def _convert_xlsx_with_ssconvert(path):
    """Convert spreadsheet to CSV using gnumeric's ssconvert."""
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        result = subprocess.run(
            ["ssconvert", path, tmp_path],
            capture_output=True, text=True, timeout=30,
            **hidden_process_kwargs(),
        )
        if result.returncode == 0 and os.path.exists(tmp_path):
            with open(tmp_path, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
    return None


def _convert_with_libreoffice(path):
    """Convert document to text using LibreOffice headless mode."""
    with tempfile.TemporaryDirectory() as tmpdir:
        result = subprocess.run(
            ["soffice", "--headless", "--convert-to", "txt:Text", "--outdir", tmpdir, path],
            capture_output=True, text=True, timeout=60,
            **hidden_process_kwargs(),
        )
        if result.returncode == 0:
            txt_name = os.path.splitext(os.path.basename(path))[0] + ".txt"
            txt_path = os.path.join(tmpdir, txt_name)
            if os.path.exists(txt_path):
                with open(txt_path, "r", encoding="utf-8", errors="replace") as f:
                    return f.read()
    return None


# Converter strategies per extension, tried in order of preference
_CONVERTERS = {
    "pdf": [
        ("pdftotext", _convert_with_pdftotext),
        ("pandoc", _convert_with_pandoc),
    ],
    "docx": [
        ("pandoc", _convert_with_pandoc),
        ("soffice", _convert_with_libreoffice),
    ],
    "doc": [
        ("soffice", _convert_with_libreoffice),
    ],
    "xlsx": [
        ("ssconvert", _convert_xlsx_with_ssconvert),
        ("soffice", _convert_with_libreoffice),
    ],
    "xls": [
        ("ssconvert", _convert_xlsx_with_ssconvert),
        ("soffice", _convert_with_libreoffice),
    ],
    "odt": [
        ("pandoc", _convert_with_pandoc),
        ("soffice", _convert_with_libreoffice),
    ],
    "rtf": [
        ("pandoc", _convert_with_pandoc),
        ("soffice", _convert_with_libreoffice),
    ],
    "pptx": [
        ("soffice", _convert_with_libreoffice),
    ],
    "epub": [
        ("pandoc", _convert_with_pandoc),
    ],
}


def on_file_attach(context):
    """Convert attached file to text using available CLI tools.

    Args:
        context: dict with keys: path, filename, extension, mime_type

    Returns:
        dict with "text" key on success, empty dict if unhandled
    """
    ext = context.get("extension", "").lower()
    strategies = _CONVERTERS.get(ext)
    if not strategies:
        return {}  # Not a format we handle — let the system show its default message

    for cmd, converter in strategies:
        if not shutil.which(cmd):
            continue
        try:
            text = converter(context["path"])
            if text:
                logger.info("Converted %s using %s (%d chars)",
                            context["filename"], cmd, len(text))
                return {"text": text}
        except subprocess.TimeoutExpired:
            logger.warning("Timeout converting %s with %s", context["filename"], cmd)
        except Exception as e:
            logger.warning("Failed to convert %s with %s: %s",
                           context["filename"], cmd, e)

    # We recognize the format but no tools are installed
    tool_names = [cmd for cmd, _ in strategies]
    return {
        "block": True,
        "reason": (
            f"Cannot convert .{ext} — none of these tools are installed: "
            f"{', '.join(tool_names)}. "
            f"Install one with your package manager, or use an MCP server "
            f"like markdownify-mcp for rich conversion."
        ),
    }
