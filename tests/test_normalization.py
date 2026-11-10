import pytest

from wyoming_silero.normalization import NormalizationConfig, StreamingCleaner, TextNormalizer


def test_legacy_has_no_enhanced_replacements_or_cleanup():
    n = TextNormalizer()
    assert n.normalize("Home Assistant 5", "en") == "Home Assistant five"
    assert n.clean("**Hello**") == "**Hello**"
    assert TextNormalizer(NormalizationConfig(cleanup=False)).clean("**Hello**") == "**Hello**"


def test_enhanced_numbers_dates_units_and_identifiers():
    n = TextNormalizer(NormalizationConfig(profile="enhanced", latin=False))
    assert n.normalize("31.12.2024") == "тридцать первое декабря две тысячи двадцать четвёртого года"
    assert n.normalize("31.02.2024") == "31.02.2024"
    assert n.normalize("192.168.1.1 v1.2.3 A-1") == "192.168.1.1 v1.2.3 A-1"
    assert n.normalize("1 °C, 2 °C, 5 °C") == "один градус Цельсия, два градуса Цельсия, пять градусов Цельсия"
    assert n.normalize("-1,25 Вт") == "минус одна целая двадцать пять сотых ватта"


def test_dictionary_user_priority_and_protected_replacements(tmp_path):
    path = tmp_path / "words.yaml"
    path.write_text("pronunciations:\n  MQTT: МКТТ\n  HA: хоум MQTT\nabbreviations:\n  т. е.: это\nstress:\n  замок: за+мок\n", encoding="utf-8")
    cfg = NormalizationConfig(profile="enhanced", dictionary_path=str(path))
    n = TextNormalizer(cfg)
    assert n.normalize("MQTT HA т. е. замок") == "МКТТ хоум MQTT это за+мок"
    assert n.normalize("за́мок") == "з+амок"


def test_independent_toggles_and_non_russian():
    n = TextNormalizer(NormalizationConfig(profile="enhanced", dictionary=False, abbreviations=False, latin=False))
    assert n.normalize("Home Assistant 5", "ru") == "Home Assistant пять"
    assert n.normalize("31.12.2024 5", "en") == "31.12.2024 five"


def test_ssml_attrs_unchanged_and_content_normalized():
    n = TextNormalizer(NormalizationConfig(profile="enhanced", latin=False))
    source = '<say-as format="dd.mm.yyyy">31.12.2024</say-as><speak>5</speak>'
    assert n.normalize(source, is_ssml=True) == '<say-as format="dd.mm.yyyy">31.12.2024</say-as><speak>пять</speak>'


def test_streaming_cleaner_emits_plain_text_and_handles_split_markup():
    cleaner = StreamingCleaner()
    assert cleaner.add("ordinary ") == "ordinary "
    assert cleaner.add("[useful") == ""
    assert cleaner.add("](https://example.test) next **bold") == "useful next bold"
    assert cleaner.add(" text** <thi") == " text "
    assert cleaner.add("nk>hidden</think> done") == "  done"
    assert cleaner.finish() == ""


def test_stream_cleaner_all_chunk_boundaries():
    source = "Say **hello** and [world](https://x)"
    expected = "Say hello and world"
    for size in range(1, len(source) + 1):
        cleaner = StreamingCleaner()
        pieces = [cleaner.add(source[i:i + size]) for i in range(0, len(source), size)]
        result = "".join(pieces) + cleaner.finish()
        assert result == expected


@pytest.mark.parametrize("yaml_text", ["[]", "unknown: {}", "stress: []", "pronunciations:\n  x: 1"])
def test_dictionary_validation(tmp_path, yaml_text):
    path = tmp_path / "bad.yaml"
    path.write_text(yaml_text, encoding="utf-8")
    with pytest.raises(ValueError):
        TextNormalizer(NormalizationConfig(dictionary_path=str(path)))


@pytest.mark.parametrize("source, expected", [
    ("2026-11-10", "десятое ноября две тысячи двадцать шестого года"),
    ("01.01.2000", "первое января двухтысячного года"),
    ("31.12.1999", "тридцать первое декабря тысяча девятьсот девяносто девятого года"),
    ("2026-02-30", "2026-02-30"),
    ("21 мин", "двадцать одна минута"),
    ("2 сек", "две секунды"),
    ("11 ч", "одиннадцать часов"),
    ("−0,5 °С", "минус ноль целых пять десятых градуса Цельсия"),
    ("+1 °F", "плюс один градус Фаренгейта"),
    ("1,250 кг", "одна целая двести пятьдесят тысячных килограмма"),
    ("2,5 м²", "две целых пять десятых квадратного метра"),
    ("21:01", "двадцать один час одна минута"),
    ("24:61", "24:61"),
    ("Уровень 5.", "Уровень пять."),
    ("-0,5", "минус ноль целых пять десятых"),
    ("+2", "плюс два"),
    ("21 min", "двадцать одна минута"),
    ("21 минута", "двадцать одна минута"),
    ("2.5 kWh", "две целых пять десятых киловатт-часа"),
    ("230 V", "двести тридцать вольт"),
    ("1000 hPa", "одна тысяча гектопаскалей"),
    ("30 km/h", "тридцать километров в час"),
])
def test_russian_spoken_forms(source, expected):
    assert TextNormalizer(NormalizationConfig(profile="enhanced", latin=False)).normalize(source) == expected


@pytest.mark.parametrize("source, expected", [
    ("Перед ```python\nprint('секрет')\n``` после.", "Перед   после."),
    ("До <think>секрет</think> после <think>ещё</think> конец", "До   после   конец"),
    ("# Заголовок\n- первый\n2. второй", " Заголовок\n первый\n второй"),
    ("Свет **включён** и `датчик` готов.", "Свет включён и датчик готов."),
    ("[текст](https://example.test/a(b)) конец", "текст конец"),
    ("Начало <!-- секрет --> конец", "Начало   конец"),
])
def test_cleanup_arbitrary_chunk_boundaries(source, expected):
    for size in range(1, len(source) + 1):
        cleaner = StreamingCleaner()
        actual = "".join(cleaner.add(source[i:i + size]) for i in range(0, len(source), size)) + cleaner.finish()
        assert actual == expected, (size, actual)


def test_hidden_content_and_unclosed_link_buffers_are_bounded():
    for opening in ("```", "<think>", "[", "[text]("):
        cleaner = StreamingCleaner(max_buffer=64)
        cleaner.add(opening)
        for _ in range(100):
            cleaner.add("x" * 1000)
            assert len(cleaner._pending) <= 128
            assert len(cleaner._label) <= 64
        cleaner.finish()


def test_dictionary_is_loaded_once_and_matching_is_longest_first(tmp_path):
    path = tmp_path / "words.yml"
    path.write_text('pronunciations:\n  Home: дом\n  Home Assistant: мой ассистент\n  дом: другое\n', encoding="utf-8")
    n = TextNormalizer(NormalizationConfig(profile="enhanced", dictionary_path=str(path)))
    path.unlink()
    assert n.normalize("Home Assistant Home") == "мой ассистент дом"


@pytest.mark.parametrize("flags, source, expected", [
    ({"dates": False}, "2026-11-10", "2026-11-10"),
    ({"units": False}, "21 мин", "двадцать один мин"),
    ({"numbers": False, "units": False}, "5", "5"),
    ({"numbers": False}, "2 мин", "две минуты"),
    ({"abbreviations": False}, "т. е.", "т. е."),
    ({"dictionary": False, "latin": True}, "Home Assistant", "х+оум асс+истент"),
])
def test_stages_are_independently_switchable(flags, source, expected):
    config = NormalizationConfig(profile="enhanced", latin=False)
    for name, value in flags.items():
        setattr(config, name, value)
    assert TextNormalizer(config).normalize(source) == expected


def test_cmu_pronunciations_are_phonetic_and_resource_cached():
    from wyoming_silero.normalization.core import _cmu_dict
    n = TextNormalizer(NormalizationConfig(profile="enhanced"))
    loaded = _cmu_dict.cache_info().misses
    assert n.normalize("light") == "л+айт"
    assert n.normalize("night") == "н+айт"
    assert _cmu_dict.cache_info().misses == loaded


def test_ssml_quoted_angles_and_entities_are_not_processed():
    n = TextNormalizer(NormalizationConfig(profile="enhanced"))
    source = '<speak><prosody rate="1 > 0">5 &amp; &#49; light</prosody><sub alias="Wi-Fi">WiFi</sub></speak>'
    expected = '<speak><prosody rate="1 > 0">пять &amp; &#49; л+айт</prosody><sub alias="Wi-Fi">WiFi</sub></speak>'
    assert n.normalize(source, is_ssml=True) == expected


def test_external_stress_preserves_manual_exceptions(monkeypatch, tmp_path):
    from wyoming_silero.normalization import core
    monkeypatch.setattr(core, "_get_accentor", lambda: lambda text: text.replace("замок", "зам+ок"))
    path = tmp_path / "dict.yaml"
    path.write_text('stress:\n  замок: з+амок\n', encoding="utf-8")
    n = TextNormalizer(NormalizationConfig(profile="enhanced", stress="silero-stress", dictionary_path=str(path)))
    assert n.normalize("замок за+мок за́мок") == "з+амок за+мок з+амок"


def test_external_stress_requires_enhanced_profile():
    with pytest.raises(ValueError, match="requires the enhanced"):
        NormalizationConfig(stress="silero-stress")


def test_ssml_dictionary_values_remain_literal_text(tmp_path):
    path = tmp_path / "dict.yaml"
    path.write_text('pronunciations:\n  alias: "Свет & музыка <включены>"\n', encoding="utf-8")
    n = TextNormalizer(NormalizationConfig(profile="enhanced", dictionary_path=str(path)))
    assert n.normalize("<speak>alias</speak>", is_ssml=True) == "<speak>Свет &amp; музыка &lt;включены&gt;</speak>"
