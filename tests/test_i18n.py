import pytest

from app.core.i18n import LANGUAGES, STRINGS, t


def test_every_key_exists_in_every_language() -> None:
    missing = {key: set(LANGUAGES) - set(entry) for key, entry in STRINGS.items()}

    assert {key: langs for key, langs in missing.items() if langs} == {}


def test_placeholders_agree_across_languages() -> None:
    import string

    formatter = string.Formatter()
    for key, entry in STRINGS.items():
        names = {
            frozenset(field for _, field, _, _ in formatter.parse(text) if field)
            for text in entry.values()
        }
        assert len(names) == 1, f"{key} uses different placeholders per language"


def test_t_fills_placeholders() -> None:
    assert t("unknown_command", "fr", command="plan") == "Je ne connais pas /plan. Essaie /help."


def test_unknown_language_falls_back_to_english() -> None:
    assert t("help_header", "de") == t("help_header", "en")


def test_unknown_key_is_a_bug() -> None:
    with pytest.raises(KeyError):
        t("no_such_string", "en")
