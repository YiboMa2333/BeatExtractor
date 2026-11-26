# Song-feature / ECG example

The current example is an original eight-second 120 BPM synthetic percussion WAV
with varying pulse levels. All JSON values come from the real librosa extractor.

- [Input WAV](synthetic_features_120bpm.wav)
- [Per-beat features JSON](synthetic_features_120bpm_features.json)
- [720p / 24 FPS ECG MP4 with original audio](synthetic_features_120bpm_ecg.mp4)

Short or weak hits may not be detected, and estimated BPM can differ slightly
from 120 because of the discrete onset grid. The stronger/weaker measured events
control spike height. This is an offline musical visualisation, not a medical ECG.

This run estimated **117.45 BPM** and detected **14 beats**. The 720p MP4 contains
**192 frames at 24 FPS**. Measured analysis/JSON time was **3.35 s**, followed by
**0.86 s** for rendering and full verification. Maximum beat-to-frame error was
**20.136 ms** (mean **15.293 ms**). Times exclude initial interpreter/module startup
and are machine-specific.

To generate fresh outputs without changing existing examples:

```bat
.venv\Scripts\python.exe tools\make_example.py --output-dir examples-new
```

The `synthetic_120bpm_*` files are retained earlier demos using the old beat-only
schema and procedural background; they are not the current feature/ECG outputs.
