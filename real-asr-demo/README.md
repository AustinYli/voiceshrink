# Real ASR demo

This scenario runs VoiceShrink against `faster-whisper` `tiny.en` on CPU.

The speech fixture is the 16 kHz JFK sample published in the [whisper.cpp sample directory](https://github.com/ggerganov/whisper.cpp/tree/master/samples). Its clean transcript contains:

> And so, my fellow Americans, ask not what your country can do for you; ask what you can do for your country.

Install the optional ASR dependency, then run:

```bash
python -m pip install -e ".[asr]"
voiceshrink baseline jfk-scenario.json
voiceshrink discover jfk-scenario.json
voiceshrink test --root .voiceshrink/regressions
```

The first run downloads `tiny.en` into `.models/`. Later runs can use the cached model offline.

The completed reference run found background noise at 29 dB SNR that changes “ask not” to “asked not.” VoiceShrink reduced the initially discovered 12 dB mutation to the weaker 29 dB mutation and saved a reproducing regression report.
