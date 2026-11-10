"""Reproducible front-end benchmark, excluding TTS and model downloads."""
import argparse
import json
import os
from pathlib import Path
import statistics
import time

from wyoming_silero.normalization import NormalizationConfig, TextNormalizer

TEXT = "В Home Assistant температура −0,5 °C, влажность 51 %, мощность 2,5 кВт. Сегодня 10.11.2026, время 21:01. Включи Bluetooth и light."


def benchmark(profile, stress, iterations):
    def rss():
        if not Path("/proc/self/statm").exists():
            return 0
        return int(Path("/proc/self/statm").read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE")

    before = rss()
    started = time.perf_counter()
    normalizer = TextNormalizer(NormalizationConfig(profile=profile, stress=stress))
    startup = time.perf_counter() - started
    normalizer.normalize(TEXT)
    samples = []
    for _ in range(iterations):
        started = time.perf_counter()
        normalizer.normalize(TEXT)
        samples.append((time.perf_counter() - started) * 1000)
    return {
        "profile": profile, "stress": stress, "chars": len(TEXT), "iterations": iterations,
        "init_seconds": round(startup, 3),
        "median_ms": round(statistics.median(samples), 3),
        "p95_ms": round(sorted(samples)[int(len(samples) * 0.95)], 3),
        "rss_delta_mib_linux": round((rss() - before) / (1024 * 1024), 2),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("legacy", "enhanced"), default="enhanced")
    parser.add_argument("--stress-mode", choices=("model", "off", "silero-stress"), default="model")
    parser.add_argument("--iterations", type=int, default=1000)
    args = parser.parse_args()
    if args.iterations < 1:
        parser.error("iterations must be positive")
    if args.stress_mode == "silero-stress":
        import torch
        torch.set_num_threads(2)
    print(json.dumps(benchmark(args.profile, args.stress_mode, args.iterations)))
