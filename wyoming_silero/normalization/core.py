"""Small, local TTS front end. Resources are shared; request state is not."""
from __future__ import annotations

import html
import re
import threading
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from typing import Optional

import yaml
from num2words import num2words

from .cleanup import StreamingCleaner


@dataclass
class NormalizationConfig:
    profile: str = "legacy"
    cleanup: bool = True
    dictionary: bool = True
    abbreviations: bool = True
    numbers: bool = True
    dates: bool = True
    units: bool = True
    latin: bool = True
    stress: str = "model"
    dictionary_path: Optional[str] = None

    def __post_init__(self):
        if self.profile not in {"legacy", "enhanced"}:
            raise ValueError("profile must be 'legacy' or 'enhanced'")
        if self.stress not in {"model", "off", "silero-stress"}:
            raise ValueError("stress must be model, off, or silero-stress")
        if self.profile == "legacy" and self.stress == "silero-stress":
            raise ValueError("silero-stress requires the enhanced normalization profile")


_BRANDS = {
    "Home Assistant": "х+оум асс+истент", "Zigbee": "з+игби",
    "Z-Wave": "зэд в+эйв", "MQTT": "эм кью ти ти", "Wi-Fi": "вай фай",
    "WiFi": "вай фай", "Bluetooth": "бл+ютус", "USB": "ю эс би",
    "CPU": "си пи ю", "API": "эй пи ай", "HTTP": "эйч ти ти пи",
    "HTTPS": "эйч ти ти пи эс", "ESPHome": "и эс пи х+оум",
    "Xiaomi": "ся+оми", "Aqara": "ак+ара", "Docker": "д+окер",
    "ESP32": "и эс пи тридцать два", "ESP8266": "и эс пи восемьдесят два шестьдесят шесть",
    "GPIO": "джи пи ай оу", "ChatGPT": "чат джи пи ти",
}
_ABBREVIATIONS = {
    "т. е.": "то есть", "т.е.": "то есть", "т. д.": "так далее",
    "т.д.": "так далее", "т. п.": "тому подобное", "т.п.": "тому подобное",
    "напр.": "например", "т.к.": "так как", "т. к.": "так как",
}


def _dictionary(path):
    sections = {"pronunciations", "abbreviations", "stress"}
    if not path:
        return {name: {} for name in sections}
    try:
        with Path(path).open(encoding="utf-8") as stream:
            data = yaml.safe_load(stream)
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid pronunciation dictionary YAML: {exc}") from exc
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError("dictionary YAML root must be a mapping")
    if set(data) - sections:
        raise ValueError("unknown dictionary section(s): " + ", ".join(map(str, set(data) - sections)))
    result = {}
    for section in sections:
        values = data.get(section, {})
        if not isinstance(values, dict) or any(
            not isinstance(k, str) or not k.strip() or not isinstance(v, str) or not v.strip()
            for k, v in values.items()
        ):
            raise ValueError(f"dictionary section '{section}' must map non-empty strings to strings")
        result[section] = values
    return result


class _Replacer:
    def __init__(self, mapping):
        self.mapping = {k.casefold(): v for k, v in mapping.items()}
        keys = sorted(self.mapping, key=len, reverse=True)
        self.pattern = re.compile(r"(?<!\w)(?:" + "|".join(map(re.escape, keys)) + r")(?!\w)", re.I) if keys else None

    def apply(self, text, protect):
        if self.pattern is None:
            return text
        return self.pattern.sub(lambda m: protect(self.mapping.get(m[0].casefold(), m[0])), text)


def _acute(text):
    return re.sub(r"([аеёиоуыэюя])\u0301", r"+\1", text, flags=re.I)


def _num(value, gender="m"):
    value = value.replace("−", "-").replace(",", ".")
    try:
        # Passing a string preserves decimal scale, including trailing zeroes.
        Decimal(value)
        # num2words mishandles negative fractions (including -0.5), so spell
        # the magnitude first and preserve the explicit sign separately.
        sign = "минус " if value.startswith("-") else "плюс " if value.startswith("+") else ""
        return sign + num2words(value.lstrip("+-"), lang="ru", gender=gender)
    except (InvalidOperation, ValueError, OverflowError):
        return value


_VALUE = r"[−+\-]?\d+(?:[.,]\d+)?"
_NUMBER = re.compile(r"(?<![\w.,+−-])" + _VALUE + r"(?![\w]|[.,]\d)")
_DATE = re.compile(r"(?<![\w.])(?:\d{2}\.\d{2}\.\d{4}|\d{4}-\d{2}-\d{2})(?!\w|\.\d)")
_TIME = re.compile(r"(?<![\w.])(\d{1,2}):(\d{2})(?!\w|:\d)")
_MONTHS = "января февраля марта апреля мая июня июля августа сентября октября ноября декабря".split()
# Count forms (one/few/many), gender, fractional genitive singular.
_UNITS = {
    "°C": ("градус Цельсия", "градуса Цельсия", "градусов Цельсия", "m", "градуса Цельсия"),
    "°С": ("градус Цельсия", "градуса Цельсия", "градусов Цельсия", "m", "градуса Цельсия"),
    "°F": ("градус Фаренгейта", "градуса Фаренгейта", "градусов Фаренгейта", "m", "градуса Фаренгейта"),
    "°": ("градус", "градуса", "градусов", "m", "градуса"),
    "%": ("процент", "процента", "процентов", "m", "процента"),
    "мин": ("минута", "минуты", "минут", "f", "минуты"),
    "сек": ("секунда", "секунды", "секунд", "f", "секунды"),
    "ч": ("час", "часа", "часов", "m", "часа"),
    "час": ("час", "часа", "часов", "m", "часа"),
    "м²": ("квадратный метр", "квадратных метра", "квадратных метров", "m", "квадратного метра"),
}
for _symbol, _noun, _few, _many in (
    ("В", "вольт", "вольта", "вольт"), ("Вт", "ватт", "ватта", "ватт"),
    ("кВт", "киловатт", "киловатта", "киловатт"), ("МВт", "мегаватт", "мегаватта", "мегаватт"),
    ("Гц", "герц", "герца", "герц"), ("кГц", "килогерц", "килогерца", "килогерц"),
    ("кг", "килограмм", "килограмма", "килограммов"), ("г", "грамм", "грамма", "граммов"),
    ("мг", "миллиграмм", "миллиграмма", "миллиграммов"), ("л", "литр", "литра", "литров"),
    ("мл", "миллилитр", "миллилитра", "миллилитров"), ("м", "метр", "метра", "метров"),
    ("км", "километр", "километра", "километров"), ("см", "сантиметр", "сантиметра", "сантиметров"),
    ("мм", "миллиметр", "миллиметра", "миллиметров"),
    ("кВт·ч", "киловатт-час", "киловатт-часа", "киловатт-часов"),
):
    _UNITS[_symbol] = (_noun, _few, _many, "m", _few)
_UNITS["км/ч"] = ("километр в час", "километра в час", "километров в час", "m", "километра в час")
_UNITS["м/с"] = ("метр в секунду", "метра в секунду", "метров в секунду", "m", "метра в секунду")
for _alias, _canonical in {
    "W": "Вт", "kW": "кВт", "MW": "МВт", "kWh": "кВт·ч", "kW·h": "кВт·ч",
    "V": "В", "Hz": "Гц", "kHz": "кГц", "kg": "кг", "g": "г", "mg": "мг",
    "L": "л", "l": "л", "ml": "мл", "m": "м", "km": "км", "cm": "см", "mm": "мм",
    "m²": "м²", "km/h": "км/ч", "m/s": "м/с", "min": "мин", "s": "сек", "h": "ч",
    "минута": "мин", "минуты": "мин", "минут": "мин",
    "секунда": "сек", "секунды": "сек", "секунд": "сек",
    "часа": "ч", "часов": "ч",
}.items():
    _UNITS[_alias] = _UNITS[_canonical]
for _symbol, _one, _few, _many in (
    ("А", "ампер", "ампера", "ампер"), ("A", "ампер", "ампера", "ампер"),
    ("mA", "миллиампер", "миллиампера", "миллиампер"),
    ("Pa", "паскаль", "паскаля", "паскалей"), ("hPa", "гектопаскаль", "гектопаскаля", "гектопаскалей"),
    ("kPa", "килопаскаль", "килопаскаля", "килопаскалей"),
    ("lx", "люкс", "люкса", "люкс"), ("dB", "децибел", "децибела", "децибел"),
):
    _UNITS[_symbol] = (_one, _few, _many, "m", _few)
_UNIT = re.compile(
    r"(?<![\w.,])(" + _VALUE + r")\s*(" + "|".join(
        re.escape(k).replace("°", r"°\s*") for k in sorted(_UNITS, key=len, reverse=True)
    ) + r")(?!\w)"
)
# Preserve grouped numeric identifiers rather than partially reading their suffixes.
_IDENTIFIER = re.compile(r"(?<!\w)(?:[A-Za-zА-Яа-я]+[\w]*[-_]\d[\w.-]*|v?\d+(?:\.\d+){2,}|\d+(?:,\d+){2,})(?!\w)")
_MARKED_WORD = re.compile(r"[А-Яа-яЁё]*(?:\+[А-Яа-яЁё]+)+")


def _date_words(literal):
    try:
        if "." in literal:
            day, month, year = map(int, literal.split("."))
        else:
            year, month, day = map(int, literal.split("-"))
        date(year, month, day)
    except ValueError:
        return literal
    day_words = num2words(day, lang="ru", to="ordinal", gender="n")
    year_words = num2words(year, lang="ru", to="ordinal", case="genitive")
    return f"{day_words} {_MONTHS[month - 1]} {year_words} года"


def _quantity(value, symbol):
    forms = _UNITS[re.sub(r"\s", "", symbol)]
    magnitude = abs(Decimal(value.replace("−", "-").replace(",", ".")))
    fractional = "." in value or "," in value
    if fractional:
        noun = forms[4]
    else:
        n = int(magnitude)
        index = 0 if n % 10 == 1 and n % 100 != 11 else 1 if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else 2
        noun = forms[index]
    return f"{_num(value, forms[3])} {noun}"


@lru_cache(maxsize=1)
def _cmu_dict():
    import cmudict
    # Retain only the first pronunciation as a compact string, rather than
    # allocating nested lists and a separate Python string for every phoneme.
    result = {}
    with cmudict.dict_stream() as stream:
        for raw in stream:
            line = raw.decode("utf-8").partition("#")[0].strip()
            word, separator, phones = line.partition(" ")
            if separator:
                result.setdefault(word.partition("(")[0].casefold(), phones.strip())
    return result


_PHONES = dict(zip(
    "AA AE AH AO AW AY B CH D DH EH ER EY F G HH IH IY JH K L M N NG OW OY P R S SH T TH UH UW V W Y Z ZH".split(),
    "а э а о ау ай б ч д з э ёр эй ф г х и и дж к л м н нг оу ой п р с ш т с у у в в й з ж".split(),
))
_TRANSLIT = {
    "shch": "щ", "sh": "ш", "ch": "ч", "zh": "ж", "kh": "х", "ts": "ц",
    "th": "т", "ph": "ф", "oo": "у", "ee": "и", "ck": "к", "yo": "ё",
    "yu": "ю", "ya": "я", "ye": "е",
    **dict(zip("abcdefghijklmnopqrstuvwxyz", ("а", "б", "к", "д", "е", "ф", "г", "х", "и", "дж", "к", "л", "м", "н", "о", "п", "к", "р", "с", "т", "у", "в", "в", "кс", "и", "з"))),
}
_TRANSLIT_RE = re.compile("|".join(sorted(_TRANSLIT, key=len, reverse=True)))


@lru_cache(maxsize=4096)
def _transliterate(word):
    phones = _cmu_dict().get(word.casefold())
    if not phones:
        return _TRANSLIT_RE.sub(lambda m: _TRANSLIT[m[0]], word.casefold())
    result = []
    for phone in phones.split():
        base = phone.rstrip("012")
        value = _PHONES[base]
        if phone.endswith("1"):
            value = re.sub(r"[аеёиоуыэюя]", lambda m: "+" + m[0], value, count=1)
        result.append(value)
    return "".join(result)


_ACCENTOR = None
_ACCENT_LOCK = threading.Lock()


def _get_accentor():
    global _ACCENTOR
    with _ACCENT_LOCK:
        if _ACCENTOR is None:
            import torch
            threads = torch.get_num_threads()
            try:
                from silero_stress import load_accentor
                _ACCENTOR = load_accentor()
            except ImportError as exc:
                raise ValueError("silero-stress mode requires installation of requirements-stress.txt") from exc
            finally:
                torch.set_num_threads(threads)
    return _ACCENTOR


# Text-node rewriting without serializing/reformatting caller markup or attributes.
_XML_TOKEN = re.compile(r'''(<!--.*?-->|<(?:(?:"[^"]*"|'[^']*'|[^'">])*)>)''', re.S)
_ENTITY = re.compile(r"&(?:#[0-9]+|#x[0-9a-fA-F]+|[A-Za-z]+);")


def _normalize_ssml(text, normalize):
    skip = 0
    result = []
    for token in _XML_TOKEN.split(text):
        if token.startswith("<"):
            name = re.match(r"</?\s*([\w:-]+)", token)
            if name and name[1] in {"say-as", "sub", "phoneme"} and not token.rstrip().endswith("/>"):
                skip = max(0, skip - 1) if token.startswith("</") else skip + 1
            result.append(token)
        elif skip:
            result.append(token)
        else:
            # Entities are not Latin words (nor should &#...; be read as numbers).
            parts = re.split("(" + _ENTITY.pattern + ")", token)
            result.append("".join(p if _ENTITY.fullmatch(p) else html.escape(normalize(p), quote=False) for p in parts))
    return "".join(result)


class TextNormalizer:
    def __init__(self, config=None):
        self.config = config or NormalizationConfig()
        local = _dictionary(self.config.dictionary_path)
        mapping = {}
        if self.config.dictionary:
            mapping.update(local["stress"])
            mapping.update(local["pronunciations"])
        if self.config.abbreviations:
            mapping.update(local["abbreviations"])
        self.user = _Replacer(mapping)
        self.brands = _Replacer(_BRANDS)
        self.abbreviations = _Replacer(_ABBREVIATIONS)
        self.accentor = _get_accentor() if self.config.profile == "enhanced" and self.config.stress == "silero-stress" else None
        if self.config.profile == "enhanced" and self.config.latin:
            _cmu_dict()  # One-time loading before the server advertises readiness.

    def clean(self, text):
        if self.config.profile != "enhanced" or not self.config.cleanup:
            return text
        cleaner = StreamingCleaner()
        return cleaner.add(text) + cleaner.finish()

    def normalize(self, text, lang="ru", is_ssml=False):
        cfg = self.config
        if cfg.profile == "legacy" or lang.split("-")[0].split("_")[0].lower() != "ru":
            from ..text import normalize_numbers
            return normalize_numbers(text, lang, is_ssml=is_ssml) if cfg.numbers else text
        if is_ssml:
            return _normalize_ssml(text, lambda node: self.normalize(node, lang))
        protected = {}

        def protect(value):
            marker = chr(0xF0000 + len(protected))
            while marker in text or marker in protected:
                marker = chr(ord(marker) + 1)
            protected[marker] = _acute(value)
            return marker

        text = _acute(text)
        text = _MARKED_WORD.sub(lambda m: protect(m[0]), text)
        text = self.user.apply(text, protect)
        if cfg.abbreviations:
            text = self.abbreviations.apply(text, protect)
        if cfg.latin:
            text = self.brands.apply(text, protect)
        # Date-looking and identifier tokens are opaque to ordinary number rules,
        # even if date expansion is disabled or the calendar date is invalid.
        text = _DATE.sub(lambda m: protect(_date_words(m[0]) if cfg.dates else m[0]), text)
        text = _IDENTIFIER.sub(lambda m: protect(m[0]), text)
        if cfg.units:
            text = _UNIT.sub(lambda m: protect(_quantity(m[1], m[2])), text)
        if cfg.numbers:
            from ..text import format_time

            def clock(m):
                h, minute = int(m[1]), int(m[2])
                return protect(format_time(h, minute, "ru") if h < 24 and minute < 60 else m[0])

            text = _TIME.sub(clock, text)
            text = _NUMBER.sub(lambda m: _num(m[0]), text)
        if cfg.latin:
            text = re.sub(r"(?<!\w)[A-Za-z]+(?:'[A-Za-z]+)?(?!\w)", lambda m: protect(_transliterate(m[0].casefold())), text)
        # Ordinary numeric/date expansions still need stress. Protected values
        # with no manual accent are included, while manual exceptions stay exact.
        if self.accentor:
            for marker, value in protected.items():
                if "+" not in value and not re.search(r"[A-Za-z\d]", value):
                    protected[marker] = self.accentor(value)
            text = "".join(part if part in protected else self.accentor(part)
                           for part in re.split("([" + "".join(protected) + "])", text)) if protected else self.accentor(text)
        return "".join(protected.get(char, char) for char in text)


def clean_text(text, cleaner=None):
    return (cleaner or TextNormalizer()).clean(text)


def normalize_sentence(text, lang="ru", config=None, is_ssml=False):
    return TextNormalizer(config).normalize(text, lang, is_ssml)
