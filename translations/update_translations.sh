#!/bin/bash
# Re-extract translatable strings from Python sources into .ts files,
# then compile .ts -> .qm for runtime use.
#
# Usage: cd translations && bash update_translations.sh

set -e
cd "$(dirname "$0")"

SOURCES=(
    ../InitGui.py
    ../freecad_ai/i18n.py
    ../freecad_ai/ui/chat_widget.py
    ../freecad_ai/ui/settings_dialog.py
    ../freecad_ai/ui/code_review_dialog.py
    ../freecad_ai/ui/message_view.py
)

# Step 1: Extract strings (optional — requires pylupdate5)
if command -v pylupdate5 &>/dev/null; then
    echo "Extracting strings with pylupdate5..."
    pylupdate5 "${SOURCES[@]}" -ts freecad_ai_de.ts
elif command -v pyside2-lupdate &>/dev/null; then
    echo "Extracting strings with pyside2-lupdate..."
    pyside2-lupdate "${SOURCES[@]}" -ts freecad_ai_de.ts
else
    echo "Note: pylupdate5 not found, skipping string extraction."
    echo "Install with: sudo apt install pyqt5-dev-tools"
fi

# Step 2: Compile .ts -> .qm
echo "Compiling .ts -> .qm..."
if command -v lrelease &>/dev/null; then
    lrelease freecad_ai_de.ts
elif command -v lrelease-qt5 &>/dev/null; then
    lrelease-qt5 freecad_ai_de.ts
else
    # Fallback: use bundled Python compiler
    python3 compile_ts.py freecad_ai_de.ts
fi

echo "Done."
