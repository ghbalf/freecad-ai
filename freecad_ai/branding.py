"""Host application and product names, resolved from the host's branding.

The names are read from the host's branding.xml through FreeCAD.ConfigGet so
this addon carries no hardcoded brand: it displays whatever application it is
running inside. Unbranded hosts -- stock FreeCAD, dev builds started from the
build tree, headless test runs -- fall back to "FreeCAD".

See the host's src/App/Branding.cpp for the key whitelist, and
src/Gui/CommandStd.cpp for the %1-placeholder convention translate_branded()
in i18n.py mirrors.
"""

DEFAULT_APP_NAME = "FreeCAD"


def _resolve_app_name() -> str:
    """Read the branded application name, or fall back to the FreeCAD default."""
    try:
        import FreeCAD
    except Exception:
        return DEFAULT_APP_NAME
    # ConfigGet returns "" for an unknown key instead of raising, so test for
    # truthiness. ExeName is populated in GUI, console and module run modes;
    # Application only exists when a branding.xml was loaded.
    for key in ("ExeName", "Application"):
        try:
            value = (FreeCAD.ConfigGet(key) or "").strip()
        except Exception:
            continue
        if value:
            return value
    return DEFAULT_APP_NAME


APP_NAME = _resolve_app_name()
PRODUCT_NAME = "{} AI".format(APP_NAME)
IS_BRANDED = APP_NAME != DEFAULT_APP_NAME
