import asyncio
import threading
from types import SimpleNamespace

import pytest
import torch

from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.error import Error
from wyoming.info import Attribution, Describe, Info, TtsProgram
from wyoming.tts import (
    Synthesize,
    SynthesizeChunk,
    SynthesizeStart,
    SynthesizeStop,
    SynthesizeStopped,
    SynthesizeVoice,
)

from wyoming_silero.handler import SileroEventHandler
from wyoming_silero.normalization import NormalizationConfig, TextNormalizer

SAMPLE_RATE = 24000


class FakeModel:
    """Stands in for a v3+ Silero model: 10 ms of audio per character."""

    def __init__(self, fail_on=None):
        self.calls = []
        self.fail_on = fail_on
        self.active = 0
        self.max_active = 0

    def apply_tts(self, text=None, speaker="kseniya", sample_rate=48000, ssml_text=None,
                  put_accent=True, put_yo=True):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            spoken = ssml_text if ssml_text is not None else text
            self.calls.append({"text": text, "ssml_text": ssml_text, "speaker": speaker,
                               "put_accent": put_accent, "put_yo": put_yo})
            if self.fail_on and self.fail_on in spoken:
                raise RuntimeError("model failure")
            # Give a concurrent request a chance to sneak in if unlocked.
            import time
            time.sleep(0.01)
            return torch.full((len(spoken) * sample_rate // 100,), 0.5)
        finally:
            self.active -= 1


class FakeWriter:
    def __init__(self):
        self.events = []

    def writelines(self, lines):
        pass

    def write(self, data):
        pass

    async def drain(self):
        pass

    def close(self):
        pass


def make_handler(model, lock=None, **cli):
    normalizer = cli.pop("normalizer", None)
    info = Info(tts=[TtsProgram(
        name="silero-ru", description="", attribution=Attribution(name="", url=""),
        installed=True, version="1", voices=[],
    )])
    args = SimpleNamespace(prosody_rate=None, prosody_pitch=None, break_ms=0, max_chars=500)
    for key, value in cli.items():
        setattr(args, key, value)
    handler = SileroEventHandler(
        info, args, model, SAMPLE_RATE, "kseniya", ["kseniya", "bel_larisa", "random"], "ru",
        {"kseniya": "ru", "bel_larisa": "be", "random": "ru"},
        lock or asyncio.Lock(),
        asyncio.StreamReader(), FakeWriter(),
        normalizer=normalizer,
    )
    events = []

    async def write_event(event):
        events.append(event)

    handler.write_event = write_event
    return handler, events


def types(events):
    return [e.type for e in events]


def audio_bytes(events):
    return sum(len(AudioChunk.from_event(e).audio) for e in events if AudioChunk.is_type(e.type))


@pytest.mark.asyncio
async def test_describe():
    handler, events = make_handler(FakeModel())
    await handler.handle_event(Describe().event())
    assert Info.is_type(events[0].type)


@pytest.mark.asyncio
async def test_synthesize_is_split_into_sentences():
    model = FakeModel()
    handler, events = make_handler(model)
    text = "Включаю свет в гостиной. Температура 20 градусов."
    await handler.handle_event(Synthesize(text=text).event())

    assert [c["text"] for c in model.calls] == ["Включаю свет в гостиной.", "Температура двадцать градусов."]
    assert AudioStart.is_type(events[0].type)
    assert AudioStop.is_type(events[-1].type)
    assert types(events).count("audio-start") == 1
    assert audio_bytes(events) == sum(len(c["text"]) for c in model.calls) * SAMPLE_RATE // 100 * 2


@pytest.mark.asyncio
async def test_streaming():
    model = FakeModel()
    handler, events = make_handler(model)

    await handler.handle_event(SynthesizeStart(voice=SynthesizeVoice(name="bel_larisa")).event())
    await handler.handle_event(SynthesizeChunk(text="Першы сказ гатовы цалкам. Дру").event())
    # The first sentence is spoken as soon as it is complete.
    assert len(model.calls) == 1
    await handler.handle_event(SynthesizeChunk(text="гі сказ 5").event())
    # The full text is sent for compatibility and must not be spoken twice.
    await handler.handle_event(Synthesize(text="Першы сказ гатовы цалкам. Другі сказ 5").event())
    await handler.handle_event(SynthesizeStop().event())

    assert [c["speaker"] for c in model.calls] == ["bel_larisa", "bel_larisa"]
    # Numbers are spelled out in the voice's language, not the server's.
    assert model.calls[1]["text"] == "Другі сказ пяць"
    assert SynthesizeStopped.is_type(events[-1].type)
    assert AudioStop.is_type(events[-2].type)
    assert types(events).count("audio-start") == 1


@pytest.mark.asyncio
async def test_streaming_empty():
    handler, events = make_handler(FakeModel())
    await handler.handle_event(SynthesizeStart().event())
    await handler.handle_event(SynthesizeStop().event())
    assert types(events) == ["audio-start", "audio-stop", "synthesize-stopped"]


@pytest.mark.asyncio
async def test_voice_by_language():
    model = FakeModel()
    handler, _ = make_handler(model)
    await handler.handle_event(Synthesize(text="Добры дзень.", voice=SynthesizeVoice(language="be")).event())
    assert model.calls[0]["speaker"] == "bel_larisa"


@pytest.mark.asyncio
async def test_unknown_voice_falls_back():
    model = FakeModel()
    handler, _ = make_handler(model)
    await handler.handle_event(Synthesize(text="Привет.", voice=SynthesizeVoice(name="nobody")).event())
    assert model.calls[0]["speaker"] == "kseniya"


@pytest.mark.asyncio
async def test_failed_sentence_is_skipped():
    model = FakeModel(fail_on="плохое")
    handler, events = make_handler(model)
    await handler.handle_event(Synthesize(text="Это плохое предложение. А это хорошее предложение.").event())
    assert not any(Error.is_type(e.type) for e in events)
    assert audio_bytes(events) > 0


@pytest.mark.asyncio
async def test_total_failure_reports_error():
    model = FakeModel(fail_on="плохое")
    handler, events = make_handler(model)
    await handler.handle_event(Synthesize(text="Только плохое предложение.").event())
    assert types(events) == ["error", "audio-start", "audio-stop"]


@pytest.mark.asyncio
async def test_prosody_and_break():
    model = FakeModel()
    handler, events = make_handler(model, prosody_rate="slow", break_ms=100)
    await handler.handle_event(Synthesize(text="Свет & музыка включены. Приятного вечера всем.").event())

    assert model.calls[0]["ssml_text"] == '<speak><prosody rate="slow">Свет &amp; музыка включены.</prosody></speak>'
    spoken = sum(len(c["ssml_text"]) for c in model.calls) * SAMPLE_RATE // 100 * 2
    assert audio_bytes(events) == spoken + SAMPLE_RATE // 10 * 2


@pytest.mark.asyncio
async def test_ssml_request_is_not_split():
    model = FakeModel()
    handler, _ = make_handler(model)
    ssml = "<speak>Первое предложение здесь. <break time=\"1s\"/> Второе предложение здесь.</speak>"
    await handler.handle_event(Synthesize(text=ssml).event())
    assert [c["ssml_text"] for c in model.calls] == [ssml]


@pytest.mark.asyncio
async def test_concurrent_requests_do_not_overlap():
    model = FakeModel()
    lock = asyncio.Lock()
    first, _ = make_handler(model, lock)
    second, _ = make_handler(model, lock)
    text = "Первое длинное предложение. Второе длинное предложение. Третье длинное предложение."
    await asyncio.gather(
        first.handle_event(Synthesize(text=text).event()),
        second.handle_event(Synthesize(text=text).event()),
    )
    assert len(model.calls) == 6
    assert model.max_active == 1


@pytest.mark.asyncio
async def test_enhanced_stream_cleanup_and_normalization_matches_one_shot():
    config = NormalizationConfig(profile="enhanced", latin=False)
    normalizer = TextNormalizer(config)
    source = "**Включи свет в большой гостиной.** <think>секрет.</think> Температура 5 °C."
    one_model = FakeModel()
    one, _ = make_handler(one_model, normalizer=normalizer)
    await one.handle_event(Synthesize(text=source).event())

    stream_model = FakeModel()
    stream, _ = make_handler(stream_model, normalizer=normalizer)
    await stream.handle_event(SynthesizeStart().event())
    for char in source:
        await stream.handle_event(SynthesizeChunk(text=char).event())
    await stream.handle_event(SynthesizeStop().event())
    assert [call["text"] for call in stream_model.calls] == [call["text"] for call in one_model.calls]
    assert stream_model.calls[0]["text"] == "Включи свет в большой гостиной."


@pytest.mark.asyncio
async def test_enhanced_stream_emits_completed_sentence_before_stop():
    config = NormalizationConfig(profile="enhanced")
    model = FakeModel()
    handler, _ = make_handler(model, normalizer=TextNormalizer(config))
    await handler.handle_event(SynthesizeStart().event())
    await handler.handle_event(SynthesizeChunk(text="**Включи свет в большой гостиной.** ").event())
    assert model.calls


@pytest.mark.asyncio
async def test_stress_off_disables_model_stress_flags():
    config = NormalizationConfig(profile="enhanced", stress="off")
    model = FakeModel()
    handler, _ = make_handler(model, normalizer=TextNormalizer(config))
    await handler.handle_event(Synthesize(text="Привет.").event())
    assert model.calls[0]["put_accent"] is False
    assert model.calls[0]["put_yo"] is False


@pytest.mark.asyncio
async def test_expanded_text_is_split_again_before_synthesis():
    model = FakeModel()
    handler, _ = make_handler(model, normalizer=TextNormalizer(NormalizationConfig(profile="enhanced", latin=False)), max_chars=50)
    await handler.handle_event(Synthesize(text="Значения: 12345. 67890.").event())
    assert model.calls
    assert all(len(call["text"]) <= 50 for call in model.calls)


@pytest.mark.asyncio
async def test_text_normalization_runs_outside_event_loop_thread():
    class ThreadRecordingNormalizer(TextNormalizer):
        def __init__(self):
            super().__init__(NormalizationConfig(profile="legacy"))
            self.thread_id = None

        def normalize(self, text, lang="ru", is_ssml=False):
            self.thread_id = threading.get_ident()
            return super().normalize(text, lang, is_ssml)

    normalizer = ThreadRecordingNormalizer()
    model = FakeModel()
    handler, _ = make_handler(model, normalizer=normalizer)
    event_thread = threading.get_ident()
    await handler.handle_event(Synthesize(text="Привет 5.").event())
    assert normalizer.thread_id != event_thread


@pytest.mark.asyncio
async def test_legacy_numbers_toggle_is_honored():
    normalizer = TextNormalizer(NormalizationConfig(profile="legacy", numbers=False))
    model = FakeModel()
    handler, _ = make_handler(model, normalizer=normalizer)
    await handler.handle_event(Synthesize(text="Уровень 5.").event())
    assert model.calls[0]["text"] == "Уровень 5."


@pytest.mark.asyncio
async def test_external_stress_mode_disables_supported_model_flags():
    normalizer = TextNormalizer(NormalizationConfig(profile="enhanced", stress="off"))
    normalizer.config.stress = "silero-stress"  # Avoid optional model load; exercise API integration only.
    normalizer.normalize = lambda text, lang="ru", is_ssml=False: text
    model = FakeModel()
    handler, _ = make_handler(model, normalizer=normalizer)
    await handler.handle_event(Synthesize(text="Привет.").event())
    assert model.calls[0]["put_accent"] is False
    assert model.calls[0]["put_yo"] is False
