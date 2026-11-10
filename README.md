# Wyoming Silero TTS

Wyoming protocol server for Silero Text-to-Speech models.
This server integrates [Silero TTS](https://github.com/snakers4/silero-models) with [Home Assistant](https://www.home-assistant.io/) via the [Wyoming protocol](https://github.com/OHF-Voice/wyoming).

## Features

- 🎙️ **Multiple Languages**: Supports Russian, English, German, Spanish, French, Ukrainian, Belarusian, and CIS languages.
- 🗣️ **Multiple Voices**: Automatically advertised to Home Assistant.
- 🔄 **Dynamic Voice Switching**: Change voices per synthesis request via Home Assistant UI.
- 🌍 **Language Mapping**: Automatic mapping of internal codes (e.g., `ua`) to ISO-639-1 codes (`uk`) for correct Home Assistant integration.
- 📦 **Dynamic Model Loading**: Automatically downloads the latest `models.yml` configuration and models from the official Silero repository.
- 🧱 **Multi-Container Support**: Runs as a unique service per language (e.g., `silero-ru`, `silero-de`), allowing multiple instances on the same host.
- 🚀 **CPU Optimized**: The Docker image is optimized for performance on CPU, using a specific CPU-only build of PyTorch.
- ⚡ **Streaming Synthesis**: Supports Wyoming text streaming, so Home Assistant starts speaking an LLM answer while it is still being generated. Long texts are split into sentences, so they never hit Silero's input length limit.
- 📡 **Optional Zeroconf Discovery**: Can announce itself to Home Assistant over mDNS (host networking only).
- 🗣️ **Optional Russian text normalization**: Enhanced profile supports cleanup, local pronunciations, abbreviations, numbers, dates, units, Latin terms, and selectable stress handling.

## Quick Start

### Using Docker Compose

1. Configure `docker-compose.yaml`.

   **Note**: The server requires **Internet Access** for the first run to download the model configuration and the selected model. They are stored in the `./silero-models` directory, which the container makes writable for its own user on start.

```yaml
services:
  wyoming-silero:
    image: ghcr.io/mrundead1996/wyoming-silero:edge
    container_name: wyoming-silero
    restart: unless-stopped
    ports:
      - "10200:10200"
    environment:
      # Silero TTS Configuration
      SILERO_LANGUAGE: "ru"           # Internal code (ru, en, de, ua, etc.)
      SILERO_MODEL: "v5_6_ru"         # Model name
      SILERO_SPEAKER: "kseniya"       # Default speaker
      SILERO_SAMPLE_RATE: "48000"     # Sample rate
      # Optional text normalization (legacy is default, preserving current behavior)
      SILERO_NORMALIZATION_PROFILE: "enhanced" # legacy restores the old behavior
      # SILERO_STRESS_MODE: "model"  # model, off, silero-stress
      # SILERO_DICTIONARY_PATH: "/config/silero-dictionary.yaml"
      # Global SSML Configuration (optional)
      # SILERO_PROSODY_RATE: "medium"    # Speech rate: x-slow, slow, medium, fast, x-fast
      # SILERO_PROSODY_PITCH: "medium"   # Pitch: x-low, low, medium, high, x-high
      # SILERO_BREAK_TIME: "1s"          # Pause duration (e.g., "500ms", "1s")
      # SILERO_BREAK_STRENGTH: "medium"  # Pause strength: x-weak, weak, medium, strong, x-strong
      # Wyoming Server Configuration
      WYOMING_PORT: "10200"    # Port
      # WYOMING_DEBUG: "true"  # Uncomment for debug logging
    volumes:
      - ./silero-models:/app/silero/model  # Persist models and config
      # - ./silero-dictionary.yaml:/config/silero-dictionary.yaml:ro
```

2. Start the server:
```bash
docker-compose up -d
```

3. In Home Assistant, go to **Settings → Devices & services → Add integration → Wyoming Protocol** and enter the host's IP address and port `10200`.

### Automatic Discovery (optional)

With `WYOMING_ZEROCONF: "true"` the server announces itself over mDNS and Home Assistant offers to add it under **Discovered**.
mDNS does not cross a Docker bridge network, so this only works with `network_mode: host` (then drop the `ports:` section). With the default bridge network nothing breaks: discovery simply does not happen and you add the integration manually as above.

If the host has several network interfaces, set `WYOMING_ZEROCONF_HOST` to the IP Home Assistant should connect to.

### Running Multiple Languages

To support multiple languages simultaneously (e.g., Russian and German), run separate containers for each on different ports.
Home Assistant will detect them as distinct services (`silero-ru`, `silero-de`).

```yaml
services:
  silero-ru:
    image: ghcr.io/mrundead1996/wyoming-silero:edge
    ports:
      - "10200:10200"
    environment:
      SILERO_LANGUAGE: "ru"
      SILERO_MODEL: "v5_1_ru"

  silero-de:
    image: ghcr.io/mrundead1996/wyoming-silero:edge
    ports:
      - "10201:10200"
    environment:
      SILERO_LANGUAGE: "de"
      SILERO_MODEL: "v3_de"
```

## Russian text normalization

The default `SILERO_NORMALIZATION_PROFILE=legacy` preserves the existing behavior: digits are normalized as before, without markdown cleanup or the added dictionaries and Russian-specific rules. Set the profile to `enhanced` to enable independently configurable text cleanup, local dictionary replacement, abbreviations, numbers, dates, units, and Latin handling. Cleanup is applied to Russian plain-text requests before sentence splitting; SSML markup is kept intact. The built-in user dictionary format and all environment/CLI options are documented in [ENV.md](ENV.md).

Example local dictionary (`silero-dictionary.yaml`):

```yaml
pronunciations:
  Home Assistant: х+оум асс+истент
abbreviations:
  т. е.: то есть
stress:
  замок: за+мок
```

Mount it read-only and set `SILERO_DICTIONARY_PATH`. The `model` stress mode uses the selected TTS model's own behavior; `off` disables supported Silero accent/ё flags; `silero-stress` enables the optional external accentor (available in the default Docker image). The local CMU pronunciation dictionary fallback is English-oriented and Latin transliteration remains approximate for words absent from CMUdict. Russian unit forms come from a curated table rather than a full morphology engine.

Normalization implementation uses [num2words](https://github.com/savoirfairelinux/num2words) and [CMUdict](https://github.com/cmusphinx/cmudict); CMUdict entries are distributed under their upstream BSD-style license. See the linked upstream projects for license details.

The ready-to-use Compose example selects `enhanced`, `v5_6_ru`, `kseniya`, and 48 kHz. Existing deployments that do not set a profile retain `legacy`. Copy [silero-dictionary.example.yaml](silero-dictionary.example.yaml) to `silero-dictionary.yaml` to customize pronunciation. Longest phrases win, replacements are literal and non-recursive, and manually marked stress (`з+амок` or `за́мок`) is preserved. Dictionary changes take effect after restarting the server.

English words use local CMUdict phonemes converted to Cyrillic with stress; unknown words use transliteration. No HTTP calls or extra neural model run in the standard path. The compact English lexicon loads once before readiness, and word conversions have a bounded cache. `silero-stress` is optional at runtime; `docker build --build-arg INSTALL_STRESS=false -t wyoming-silero .` omits its package. The default `model` mode is recommended for `v5_6_ru` to avoid accentuating twice.

Dates (`DD.MM.YYYY` and `YYYY-MM-DD`), signed decimal quantities, percentages, and common units are supported. Unit symbols are case-sensitive (`В` is volts, `в` remains a preposition). Version/IP identifiers and invalid dates are kept intact. The rules do not attempt full grammatical case inference from arbitrary surrounding prose. Code fences, HTML comments and `<think>` blocks are omitted, while link labels and inline code text are retained. Unclosed hidden blocks stay hidden; buffers are bounded. SSML is processed as text nodes only, preserving attributes and explicit `sub`, `say-as`, and `phoneme` content.

### Build and publication

```bash
docker build -t ghcr.io/mrundead1996/wyoming-silero:edge .
docker compose up -d --pull never
```

GitHub Actions tests and builds pull requests without publishing. A push to `main` publishes `edge` and SHA tags to `ghcr.io/mrundead1996/wyoming-silero`; version tags publish semantic-version tags (`latest` for stable releases). Manual workflow runs also execute the real-model smoke test. CPU-only PyTorch/torchaudio versions are paired in `requirements-cpu.txt`. Models are cached on the persistent volume, not baked into the image.

## Supported Models

Models are dynamically defined in the upstream [models.yml](https://github.com/snakers4/silero-models/blob/master/models.yml). Common examples:

- **Russian (`ru`)**: `v5_6_ru`, `v5_1_ru`, `v5_ru`, `v4_ru`
- **English (`en`)**: `v3_en`
- **German (`de`)**: `v3_de`
- **Ukrainian (`ua`)**: `v4_ua` (Advertised as `uk` to Home Assistant)
- **CIS Multi (`multi`)**: `v5_cis_base` (Supports `bel`, `ru`, `ua` voices)
- **Legacy multi-language (`multi`)**: `multi_v2` (16 kHz, no SSML; each voice is advertised with its own language)

Models distributed as `.jit` packages are rejected at startup with a clear message.

## Troubleshooting

- **No Voices in Home Assistant**: Use `Reload` on the Wyoming integration or delete and re-add it. Home Assistant caches device configurations aggressively.
- **Offline Mode**: Ensure the first run has internet access to populate the `silero-models` volume. Subsequent runs can operate offline using cached files.
- **Unsupported Sample Rate**: The server checks `SILERO_SAMPLE_RATE` against the rates the model declares and falls back to the highest supported one, logging a warning.
- **Synthesis Errors**: A sentence that fails is logged with a traceback and skipped, so the rest of the answer is still spoken. If nothing could be synthesized, the client gets a Wyoming `Error` event instead of silent audio.
- **Permission denied in the models directory**: The container fixes the ownership of `./silero-models` on start. If you run it with an explicit `user:`, make sure that user can write to the directory.
- **Long SSML documents**: Plain text is split into sentences automatically, but SSML is sent to the model as a whole and must stay within Silero's length limit.
- **Health check**: `docker ps` shows the container as `healthy` once it answers a Wyoming `Describe` request. The first start can take a few minutes while the model downloads.

### Live smoke test

After starting a Russian `v5_6_ru` instance with the `kseniya` voice at 48 kHz, run:

```bash
python -m tests.smoke_wyoming tcp://127.0.0.1:10200
# Or start a temporary local server; download/cache the model on first use:
python -m tests.smoke_wyoming --model-dir /tmp/silero-models
python -m tests.smoke_wyoming --model-dir /tmp/silero-models --stress-mode silero-stress
```

The test checks voice advertisement, non-silent 48 kHz mono PCM, ordinary requests, and audio arriving before the rest of the text and the stop event. Run front-end benchmarks separately with `python -m tests.benchmark_normalization --profile enhanced` (or `legacy`); use `--stress-mode silero-stress --iterations 100` to measure the external accentor. Timings depend on the host and exclude TTS inference and model downloads.

Reference measurements in the development Linux environment (Python 3.12, 130-character mixed Russian/English sensor report, warm word cache):

| Front end | Median | p95 | Additional resident memory |
| --- | ---: | ---: | ---: |
| Legacy | 0.035 ms | 0.055 ms | negligible |
| Enhanced, model stress | 0.119 ms | 0.147 ms | about 22 MiB |
| Enhanced, external stress (2 torch threads) | 8.493 ms | 8.945 ms | about 228 MiB |

The English lexicon initialized in about 80 ms; the optional external accentor in about 2.1 seconds. These costs occur at startup. The real-model TCP smoke test also passed on Python 3.14 with paired CPU torch/torchaudio 2.9.1, `v5_6_ru`, and `kseniya` at 48 kHz; first streamed audio arrived in about 0.84 seconds for the test sentence. These are observations, not hardware-independent latency guarantees.

## Credits

- [Silero Models](https://github.com/snakers4/silero-models)
- [Wyoming Protocol](https://github.com/OHF-Voice/wyoming)
