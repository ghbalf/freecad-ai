#!/bin/bash
# Re-extract translatable strings from Python sources into .ts files,
# then compile .ts -> .qm for runtime use.
#
# Usage: cd translations && bash update_translations.sh [--list-sources]

set -e
cd "$(dirname "$0")"

# Every file with translatable strings, so a new one can't be forgotten.
mapfile -t SOURCES < <(grep -rlE --include='*.py' \
    '\b(translate|QT_TRANSLATE_NOOP)\(' ../InitGui.py ../freecad_ai | sort)

if [ "$1" = "--list-sources" ]; then
    printf '%s\n' "${SOURCES[@]}"
    exit 0
fi

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
