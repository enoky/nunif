"""
Translations for the strings iw3_ext adds.

iw3's own table is consulted for anything missing here, so a word it already
translates (Depth, Save, Cancel) reads the same as it does in the rest of the
GUI, and anything neither table has falls through to English.

To add a language, drop a .yml next to this file using the same "_LOCALE" name
as the matching file in iw3/locales.
"""
from os import path
import iw3.gui as iw3_gui
from iw3.locales import LOCALES as IW3_LOCALES, load_locales, load_language_setting


EXT_LOCALES = load_locales(path.dirname(__file__))
_locale_dict = None


def resolve_locale_dict():
    # iw3.gui.LOCALE_DICT is one of the dicts in iw3.locales.LOCALES, so the
    # active language is the one it was taken from
    for name, table in IW3_LOCALES.items():
        if table is iw3_gui.LOCALE_DICT:
            return EXT_LOCALES.get(name, {})

    config_path = getattr(iw3_gui, "LANG_CONFIG_PATH", None)
    if config_path is not None:
        saved = load_language_setting(config_path)
        if saved:
            return EXT_LOCALES.get(saved, {})

    return {}


def T(s):
    """The language is fixed at startup, so the table is resolved once."""
    global _locale_dict
    if _locale_dict is None:
        _locale_dict = resolve_locale_dict()
    if s in _locale_dict:
        return _locale_dict[s]
    return iw3_gui.T(s)
