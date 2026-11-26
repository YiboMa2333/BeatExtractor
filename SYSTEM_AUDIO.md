# Live Windows system audio (default Live Analysis mode)

For computer microphone input, choose the separate **Microphone Analysis** option
**4**; see README.md. This system-audio mode remains option **2**. Exit is **5**.

Choose **2** after launching `run_beat_tracker.bat`. No local file is requested.
The window opens **stopped**. Open your music source, then click **Start recording**.
The application captures the default Windows WASAPI speaker/headphone output
using PyAudioWPatch's loopback endpoint. Play music in any other application.
There is no microphone capture, duplicate playback or decorative visual engine.
When stopped, this workflow saves JSON first, then creates an ECG MP4 containing
the recorded Windows audio. All outputs default to this project's `download/`.

Install/update live dependencies using the project's own environment:

```bat
.venv\Scripts\python.exe -m pip install -r requirements-live.txt
```

PyAudioWPatch 0.2.12.9 was installed and tested on Windows/Python 3.11. Existing
sounddevice support remains for the optional local-file playback backend and
hardware test-tone generation; loopback capture itself uses no output stream.
Tkinter is supplied by the Windows Python installer. Offline analysis and video
export are unchanged and do not import loopback dependencies.

## Capture and controls

The selected endpoint is shown in the GUI. The default follows the Windows
default output at session creation; it does not silently switch devices during
capture. To choose a specific endpoint:

```bat
.venv\Scripts\python.exe src\cmd_app.py --list-loopback-devices
.venv\Scripts\python.exe src\cmd_app.py --action live --device INDEX
```

Only WASAPI loopback devices are accepted. A missing endpoint is an explicit
error, never a fallback to a microphone. Audio sent to another output device will
not be captured. All system sounds on the selected endpoint are included, not
just a particular application/song. Some drivers deliver no buffers while idle;
the graph can wait until audio starts. If an endpoint stops or is unplugged, the
session reports an error and saves available data; start a new session after
fixing routing.

- **Start recording** begins capture and changes the same button to **Stop recording**.
- **Stop recording** or window close releases capture, writes JSON first, then renders
  and validates the ECG MP4 with captured audio. Wait for rendering to finish.
  Capture has no known total duration; post-capture video progress uses actual frames.
- While saving/rendering, the button is disabled. After completion, **Start recording**
  begins a fresh session with reset feature/rhythm state and unique output names.
- Closing the window before starting creates no output. The capture does not start
  automatically when the window opens and never controls external music playback.

The GUI uses the same bounded feature/ECG/spectrum pipeline as file-based live
analysis. Yellow dashed events are onsets; taller cyan events are estimated
beats after regular-onset tempo warm-up. BPM remains "Estimating..." when the
heuristic lacks confidence. This is not a medical ECG or a robust downbeat tracker.

## Data, clock and limitations

The outputs are **download/system_audio_live_features.json** and
**download/system_audio_live_ecg.mp4**, with numbered alternatives instead of
overwrites. Source metadata says `system_audio` and
`sourceType: windows.wasapi.loopback`; no song filename is guessed. Capture
metadata records endpoint name/index, native sample rate/channel count, reported
input latency, microphone/output-playback flags and pause semantics.

Feature and event times are based on **received captured sample count / native
sample rate**, relative to session start. They are not source-file timestamps or
Python processing-completion times. The capture clock continues through analysis
pauses, preserving gaps. Capture overflows are counted and recorded; unknown lost
hardware sample counts are not invented. Thus hardware losses can make the
received-sample timeline differ from elapsed real time. Driver-reported input
latency is not independent speaker-to-graph alignment measurement.

`completed: true` means the capture was stopped and finalised successfully, not
that a song reached EOF. `durationSeconds` and `captureDurationSeconds` refer to
captured PCM duration. The existing incremental writer validates final JSON
without retaining unbounded histories. Capture/analysis losses, processing times
and queue delays appear in diagnostics. FFT/onset/tempo settings are also saved.
Neither a low input-buffer latency nor a synthetic detector result establishes
the IXN 25 ms end-to-end alignment target.

## Implementation and verification

`loopback_engine.py` selects and validates a speaker-loopback endpoint, then
reuses the existing analysis worker, NumPy DSP, bounded queues and incremental
recorder. Capture callbacks only retain a PCM view and offer it to a bounded
queues; FFT, GUI and disk work remain outside callbacks. A separate audio writer
records received PCM to a temporary RF64 WAV. JSON is published before video
rendering; the video reads those same onset/estimated-beat events, never another
detector's guesses. Audio recording spans analysis pauses, preserving the shared
sample timeline. Temporary audio is deleted only after verified MP4 success and
retained for recovery on failure; see [OUTPUTS.md](OUTPUTS.md). Captured audio is
never replayed live. Optional file playback remains available using
`--action live --live-source file` with a local filename; its details are in
[LIVE_ANALYSIS.md](LIVE_ANALYSIS.md).

Run the regression suite or the explicit real-device smoke test:

```bat
.venv\Scripts\python.exe -m unittest discover -s tests -v
.venv\Scripts\python.exe tools\validate_loopback.py --hardware --report examples\loopback_validation.json
```

The smoke test intentionally plays a quiet two-second output tone, captures it
through WASAPI, exercises the hidden GUI and pause/resume, checks nonzero measured
audio, and validates recording and shutdown. Its temporary recording is removed;
the report contains metrics. Test-tone playback is a test utility, not part of
normal loopback operation.

All 45 automated tests passed. A real Senary Audio speaker-loopback session at
48000 Hz was verified: nonzero captured audio, zero capture overflows or dropped analysis buffers, reported
input latency 22 ms, and released stream/backend/worker. These values are one
device-specific run; see [the measured report](examples/loopback_validation.json).
