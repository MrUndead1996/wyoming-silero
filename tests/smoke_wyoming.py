"""Opt-in live Wyoming test; optionally host a real v5_6_ru model locally."""
import argparse
import asyncio
import json
import time
from types import SimpleNamespace

from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.client import AsyncClient
from wyoming.error import Error
from wyoming.info import Attribution, Describe, Info, TtsProgram, TtsVoice
from wyoming.tts import (
    Synthesize, SynthesizeChunk, SynthesizeStart, SynthesizeStop,
    SynthesizeStopped, SynthesizeVoice,
)


async def _read(client):
    event = await client.read_event()
    if event is None:
        raise RuntimeError("Server closed before completing the request")
    if Error.is_type(event.type):
        raise RuntimeError(Error.from_event(event).text)
    return event


async def smoke(uri, speaker="kseniya", expected_rate=48000):
    results = {}
    async with AsyncClient.from_uri(uri, connect_timeout=10, read_timeout=120) as client:
        await client.write_event(Describe().event())
        info = Info.from_event(await _read(client))
        if not any(v.name == speaker for program in info.tts for v in program.voices):
            raise AssertionError(f"Voice {speaker} was not advertised")
        voice = SynthesizeVoice(name=speaker)
        await client.write_event(SynthesizeStart(voice=voice).event())
        started = time.perf_counter()
        await client.write_event(SynthesizeChunk(text="**В Home Assistant температура −0,5 °C.** ").event())
        audio_size, nonzero, starts = 0, False, 0

        def collect(event):
            nonlocal audio_size, nonzero, starts
            if AudioStart.is_type(event.type):
                start = AudioStart.from_event(event)
                assert (start.rate, start.width, start.channels) == (expected_rate, 2, 1)
                starts += 1
            if AudioChunk.is_type(event.type):
                audio = AudioChunk.from_event(event).audio
                audio_size += len(audio)
                nonzero = nonzero or any(audio)

        while audio_size == 0:
            event = await _read(client)
            assert not AudioStop.is_type(event.type), "No audio before the end of the stream"
            collect(event)
        results["first_audio_seconds"] = round(time.perf_counter() - started, 3)
        # Audio has arrived before both the remaining text and synthesize-stop.
        await client.write_event(SynthesizeChunk(text="<think>Это не надо читать.</think> Свет включён в 21:01.").event())
        await client.write_event(SynthesizeStop().event())
        audio_stops = 0
        while True:
            event = await _read(client)
            collect(event)
            audio_stops += int(AudioStop.is_type(event.type))
            if SynthesizeStopped.is_type(event.type):
                break
        assert starts == 1 and audio_stops == 1 and nonzero
        results["stream_audio_seconds"] = round(audio_size / (expected_rate * 2), 3)

        starts, audio_size, nonzero = 0, 0, False
        await client.write_event(Synthesize(text="Сегодня 10.11.2026. Включи Bluetooth и light.", voice=voice).event())
        while True:
            event = await _read(client)
            collect(event)
            if AudioStop.is_type(event.type):
                break
        assert starts == 1 and audio_size > 0 and nonzero
        results["one_shot_audio_seconds"] = round(audio_size / (expected_rate * 2), 3)
    print(json.dumps(results, ensure_ascii=False))


async def local_smoke(args):
    import torch
    from wyoming_silero.handler import SileroEventHandler
    from wyoming_silero.loader import load_silero_model
    from wyoming_silero.normalization import NormalizationConfig, TextNormalizer

    torch.set_num_threads(args.threads)
    model, _ = load_silero_model("ru", "v5_6_ru", args.model_dir, offline=True)
    assert args.speaker in model.speakers
    normalizer = TextNormalizer(NormalizationConfig(profile="enhanced", stress=args.stress_mode))
    attribution = Attribution(name="Silero", url="https://github.com/snakers4/silero-models")
    voice = TtsVoice(name=args.speaker, description="", attribution=attribution,
                     installed=True, version=None, languages=["ru"])
    info = Info(tts=[TtsProgram(name="silero-ru", description="", attribution=attribution,
                               installed=True, version="test", voices=[voice],
                               supports_synthesize_streaming=True)])
    settings = SimpleNamespace(max_chars=500, break_ms=0)
    lock = asyncio.Lock()

    async def connected(reader, writer):
        await SileroEventHandler(
            info, settings, model, args.sample_rate, args.speaker, model.speakers,
            "ru", {name: "ru" for name in model.speakers}, lock, reader, writer,
            normalizer=normalizer,
        ).run()

    async with await asyncio.start_server(connected, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        await smoke(f"tcp://127.0.0.1:{port}", args.speaker, args.sample_rate)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("uri", nargs="?", help="Running Wyoming server URI")
    parser.add_argument("--model-dir", help="Start a local model; download into this cache if absent")
    parser.add_argument("--stress-mode", choices=("model", "off", "silero-stress"), default="model")
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--speaker", default="kseniya")
    parser.add_argument("--sample-rate", type=int, default=48000)
    args = parser.parse_args()
    if not args.uri and not args.model_dir:
        parser.error("provide a server URI or --model-dir")
    asyncio.run(local_smoke(args) if args.model_dir else smoke(args.uri, args.speaker, args.sample_rate))
