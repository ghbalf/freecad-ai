#!/bin/bash
# Re-extract translatable strings from Python sources into .ts files.
# Requires pylupdate5 (from pyqt5-dev-tools) or pyside2-lupdate.
#
# Usage: cd translations && bash update_translations.sh

set -e

SOURCES=(
    ../InitGui.py
    ../freecad_ai/i18n.py
    ../freecad_ai/ui/chat_widget.py
    ../freecad_ai/ui/settings_dialog.py
    ../freecad_ai/ui/code_review_dialog.py
    ../freecad_ai/ui/message_view.py
)

if command -v pylupdate5 &>/dev/null; then
    LUPDATE=pylupdate5
elif command -v pyside2-lupdate &>/dev/null; then
    LUPDATE=pyside2-lupdate
else
    echo "Error: neither pylupdate5 nor pyside2-lupdate found."
    echo "Install with: sudo apt install pyqt5-dev-tools"
    exit 1
fi

echo "Using: $LUPDATE"
$LUPDATE "${SOURCES[@]}" -ts freecad_ai_de.ts
echo "Done. Review freecad_ai_de.ts for new untranslated strings."
