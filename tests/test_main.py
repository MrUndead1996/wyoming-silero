import pytest

from wyoming_silero.__main__ import describe_voice, resolve_voice_language, _normalization_arguments, _normalization_config
import argparse


@pytest.mark.parametrize("name, lang, expected", [
    ("kseniya", "ru", "Kseniya (ru)"),
    ("bel_larisa", "be", "Larisa (be)"),
    ("en_12", "en", "en_12 (en)"),
    ("v3_speaker", "ru", "V3 Speaker (ru)"),
])
def test_describe_voice(name, lang, expected):
    assert describe_voice(name, lang) == expected


def test_voice_language_from_prefix():
    assert resolve_voice_language("ua_mykyta", {}, "ru") == "uk"


def test_voice_language_from_models_yml():
    assert resolve_voice_language("aidar", {"speakers": {"aidar": {"lang": "ru"}}}, "multi") == "ru"


def test_voice_language_override():
    assert resolve_voice_language("bel_larisa", {}, "ru", "ru") == "ru"


def test_normalization_cli_boolean_flags_and_profile(monkeypatch):
    monkeypatch.setenv("SILERO_NORMALIZE_NUMBERS", "no")
    parser = argparse.ArgumentParser()
    _normalization_arguments(parser)
    args = parser.parse_args(["--normalization-profile", "enhanced", "--no-normalize-cleanup", "--stress-mode", "off"])
    config = _normalization_config(args)
    assert config.profile == "enhanced"
    assert config.numbers is False
    assert config.cleanup is False
    assert config.stress == "off"


def test_normalization_env_rejects_ambiguous_boolean(monkeypatch):
    monkeypatch.setenv("SILERO_NORMALIZE_UNITS", "sometimes")
    parser = argparse.ArgumentParser()
    with pytest.raises(SystemExit) as exc:
        _normalization_arguments(parser)
    assert exc.value.code == 2
