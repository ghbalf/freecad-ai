"""Regression tests for SettingsDialog._on_provider_changed.

Issue #12 (xtc0r): switching the provider combo to "custom" was wiping the
user's gateway URL and model, because the "custom" preset ships empty
strings and the dialog applied them unconditionally. After v0.14.3 the
dialog only overwrites a field when the preset has a concrete value.

These tests exercise the method via the unbound-method-with-fake-self
pattern — no QApplication required.
"""

from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock, patch

import pytest

# settings_dialog imports through ui/compat.py which needs PySide6 or PySide2.
# In dev venvs without either, skip the entire module — the dialog can't be
# imported. Inside FreeCAD it's always available.
try:
    import PySide6  # noqa: F401
except ImportError:
    try:
        import PySide2  # noqa: F401
    except ImportError:
        pytest.skip("PySide6/PySide2 not available", allow_module_level=True)

from freecad_ai.config import PROVIDER_PRESETS  # noqa: E402
from freecad_ai.llm.providers import get_provider_names  # noqa: E402
from freecad_ai.ui.settings_dialog import SettingsDialog  # noqa: E402


def _make_fake_dialog(base_url="http://gateway.example/v1", model="my-model"):
    """Build a fake dialog with just the attributes _on_provider_changed touches."""
    base_url_edit = MagicMock()
    base_url_edit.text.return_value = base_url
    model_edit = MagicMock()
    model_edit.text.return_value = model
    return SimpleNamespace(
        base_url_edit=base_url_edit,
        model_edit=model_edit,
        _load_model_params_table=MagicMock(),
        _rerank_at_factory_defaults=MagicMock(return_value=False),
        _apply_rerank_defaults=MagicMock(),
        _commit_profile_fields=MagicMock(),
    )


def test_switch_to_custom_preserves_fields():
    """Custom preset has empty base_url/default_model — must NOT overwrite."""
    assert PROVIDER_PRESETS["custom"]["base_url"] == ""
    assert PROVIDER_PRESETS["custom"]["default_model"] == ""

    fake = _make_fake_dialog()
    custom_idx = get_provider_names().index("custom")
    with patch("freecad_ai.ui.settings_dialog.get_config", return_value=MagicMock()):
        SettingsDialog._on_provider_changed(cast(SettingsDialog, fake),custom_idx)

    fake.base_url_edit.setText.assert_not_called()
    fake.model_edit.setText.assert_not_called()
    # _load_model_params_table is called with whatever's in the field, not "".
    fake._load_model_params_table.assert_called_once()
    args, _ = fake._load_model_params_table.call_args
    assert args[0] == "my-model"


def test_switch_to_real_provider_applies_preset():
    """Anthropic (or any non-custom provider) overwrites fields as before."""
    fake = _make_fake_dialog()
    anthropic_idx = get_provider_names().index("anthropic")
    with patch("freecad_ai.ui.settings_dialog.get_config", return_value=MagicMock()):
        SettingsDialog._on_provider_changed(cast(SettingsDialog, fake),anthropic_idx)

    fake.base_url_edit.setText.assert_called_once_with(
        PROVIDER_PRESETS["anthropic"]["base_url"])
    fake.model_edit.setText.assert_called_once_with(
        PROVIDER_PRESETS["anthropic"]["default_model"])


def test_invalid_index_is_noop():
    """Out-of-range index leaves all widgets untouched."""
    fake = _make_fake_dialog()
    SettingsDialog._on_provider_changed(cast(SettingsDialog, fake),-1)
    SettingsDialog._on_provider_changed(cast(SettingsDialog, fake),9999)
    fake.base_url_edit.setText.assert_not_called()
    fake.model_edit.setText.assert_not_called()
    fake._load_model_params_table.assert_not_called()
