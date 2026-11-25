"""The German translation file against the strings in the code."""

import ast
import importlib.util
import os
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TS = ROOT / "translations" / "freecad_ai_de.ts"
CALLS = {"translate", "QT_TRANSLATE_NOOP"}


def _calls():
    for path in [ROOT / "InitGui.py", *sorted((ROOT / "freecad_ai").rglob("*.py"))]:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call)
                    and getattr(node.func, "id", "") in CALLS
                    and len(node.args) >= 2):
                yield path, node


def _messages():
    for context in ET.parse(TS).getroot().iter("context"):
        for message in context.iter("message"):
            yield (context.findtext("name"), message.findtext("source"),
                   message.findtext("translation") or "")


def _placeholders(text):
    return Counter(re.findall(r"\{[^{}\s]*\}|%s", text))


def test_strings_are_not_built_before_they_are_looked_up():
    """An f-string's lookup key changes with its values: never translated."""
    assert [f"{path.name}:{node.lineno}" for path, node in _calls()
            if isinstance(node.args[1], ast.JoinedStr)] == []


@pytest.mark.skipif(not shutil.which("bash"), reason="needs bash")
def test_the_update_script_reads_every_file_with_strings():
    out = subprocess.run(
        ["bash", "update_translations.sh", "--list-sources"],
        cwd=TS.parent, capture_output=True, text=True, check=True).stdout
    listed = {os.path.realpath(TS.parent / line) for line in out.split("\n") if line}
    assert {str(path) for path, _ in _calls()} <= listed


def test_messages_are_unique():
    assert [key for key, n in Counter(m[:2] for m in _messages()).items()
            if n > 1] == []


@pytest.mark.parametrize("context, source, german", list(_messages()))
def test_placeholders_survive_translation(context, source, german):
    assert german
    # A message's own {ACCOUNT_ID} text counts too: it must stay as it is.
    assert _placeholders(german) == _placeholders(source)


def test_the_compiled_file_is_up_to_date(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "compile_ts", TS.parent / "compile_ts.py")
    compiler = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(compiler)
    fresh = tmp_path / "de.qm"
    compiler.compile_ts_to_qm(str(TS), str(fresh))
    assert fresh.read_bytes() == TS.with_suffix(".qm").read_bytes()
