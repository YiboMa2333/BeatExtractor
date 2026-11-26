> Live Analysis now defaults to Windows system-audio loopback, with no file prompt. See [SYSTEM_AUDIO.md](SYSTEM_AUDIO.md). This document describes the retained optional local-file backend (--live-source file).

# Live audio analysis

Live mode is independent of offline librosa extraction, MP4 generation and all
decorative visual engines. It needs no GPU or microphone and works without an
internet connection after installing the optional dependencies.

## Architecture and dependencies

`cmd_app.py` retains the existing Video Export workflow and adds the four-choice
menu and JSON-only Offline Analysis. Existing `analyse.py`, `overlay.py` and
`video_export.py` continue to provide offline features and the ECG MP4.

Live components:

- `live_engine.py`: SoundFile decoder producer, one sounddevice **OutputStream**,
  bounded playback/analysis queues, sample clock, diagnostics and worker cleanup.
- `live_dsp.py`: one NumPy FFT per hop, signal/spectral features, causal flux onset
  detection and an explicitly heuristic regular-onset tempo/estimated-beat gate.
- `live_gui.py`: CPU Tk Canvas ECG and FFT bars; graph objects reused at 30 FPS by
  default. No plotting/OpenGL/GPU dependency is installed. GUI history uses bounded
  deques and queues independent of audio rate.
- `live_recording.py`: incremental NDJSON spooling, streaming final JSON writing
  and ijson validation, without retaining the full feature history in memory.

`requirements-live.txt` adds sounddevice and ijson to the existing requirements.
Tkinter/Tcl/Tk comes from the Python installer. Live dependencies are checked only
when live mode is selected, with an installation message if missing. Aubio is not
installed or used: this build uses the tested NumPy streaming detector and does
not claim an aubio integration. WAV and FLAC are decoded directly; MP3 depends on
installed SoundFile/libsndfile decoding support. No FFmpeg fallback is hidden.

Data flow: decoder -> bounded playback queue -> output callback -> bounded analysis
queue -> DSP + incremental recorder -> bounded GUI queue. Playback and analysis
receive the **same float32 PCM array**. Values are clipped once to [-1, 1] before
both consumers; files with more than two channels are averaged to stereo.

The callback performs buffer copies and non-blocking queue offers only. It does
not decode, FFT, write JSON, draw widgets, log text or wait for a worker. Python
queues and callbacks are best-effort, not a hard real-time scheduling guarantee.
Decoder backpressure uses bounded waits; these waits never determine event times.

## Features, events and detector limitations

Each hop produces peak amplitude, RMS, RMS dBFS, spectral centroid/bandwidth in Hz,
normalised positive spectral flux, and bass (<250 Hz), midrange (250鈥?000 Hz) and
treble (>=4000 Hz) energy. Band energy is mean-square energy derived from the
one-sided Hann-window spectrum with window-energy normalisation. A full FFT is
computed once per hop and the latest spectrum is visualised in 32 aggregated
bars; full per-hop spectra are not written to JSON to avoid excessive file sizes.
RMS/peak use the trailing FFT-sized mono window, zero-padded at startup. RMS dBFS
is `20*log10(RMS)`, not perceptual LUFS or acoustic sound-pressure level. Undefined
log/spectral quantities use JSON null, never NaN or Infinity.

An **onset** is a causal local peak in positive spectral flux above an adaptive
median-plus-MAD threshold with a 0.05 floor and a 120 ms refractory interval.
Local-maximum confirmation uses one hop of lookahead. Peak amplitude is a signal
measurement; it is not automatically classified as an onset or beat.

An **estimatedBeat** is a newly confirmed onset after at least five regular
inter-onset intervals establish a 60鈥?00 BPM tempo. Interval deviation must stay
within 10%, and regression-based confidence must be >=0.75. There are no invented
future events, no retrospective filling of warm-up beats and no assumption that
every transient is a beat. BPM remains null/"Estimating..." before confidence or
after rhythm loss. Detection parameters and event semantics are saved in JSON.

This is a limited regular-onset heuristic. Arpeggios/subdivisions can resemble a
musical pulse; syncopation, gradual tempo changes and missing onsets can reset it
or produce half/double tempo. It does not identify downbeats or guarantee musical
beat correctness. Dedicated streaming beat trackers can be evaluated later as
optional components, independently of the current playback/data pipeline.

## Source timestamps and output buffering

Feature timestamps use **source sample index / source sample rate**, with
`sampleIndex` referring to the end of the causal trailing feature window. Onset
event time is the centre of the previous trailing FFT window; `detectedAtSeconds`
records when the next source hop made local-maximum confirmation possible.
Neither timestamp is taken from Python computation-completion time.

The GUI's heard-content position uses PortAudio `outputBufferDacTime` and
`stream.time`. A bounded history of scheduled buffer segments prevents jumping
ahead to a queued buffer that has not reached its DAC time. The reported output
latency is recorded. Display data is gated by the playback position, rather than
showing analysis of future buffered audio. Event peaks can appear behind the
playback bar because confirmation is causal and incurs processing/lookahead delay.

Pause stops submission of new content. Already queued hardware buffers drain,
then the source timeline freezes. Resume continues at the next source sample,
preserving DSP state. Silence inserted for decoder underruns does not consume
source-content samples; the gap is recorded. Stop/window close aborts playback,
drains analysis, excludes measurements after the heard-content cutoff and saves
a partial recording. EOF drains output normally before finalisation. There is no
seeking or in-session restart; select Live mode again for a fresh decoder/DSP
state. Play starts the initial session automatically (and is available before
start); it is disabled once running, while Pause/Resume/Stop remain available.

Device/OS/DAC timestamps are reported timing, not an independent measurement of
physical speaker output. Analysis window size, confirmation delay, queue backlog,
driver buffering and GUI redraw all contribute to latency. Near-real-time live
analysis is **not proof of the IXN 25 ms alignment target**.

## Recording, bounds and failure reporting

`song_live_features.json` includes source/mode/sample rate, source and played
durations, completion flag, timestamp units, extraction/detector parameters,
feature rows, typed events, final supported BPM, output-latency information and
profiling/queue diagnostics. `features` are continuous measurements; `events`
distinguish onsets, estimated beats, analysis gaps and output/decoder underruns.
Events are in detection/write order; consumers should use `timeSeconds` for
source position, especially when diagnostic events are interleaved.

Playback and analysis queues default to 32 blocks each. GUI/diagnostic queues hold
256 entries, GUI pending rows at most 512 and visible events at most 2048; clock
history holds at most 4096 segments. Only one trailing FFT window and short onset
history are retained by DSP. The decoder reads incrementally rather than loading
the entire song. Long recordings grow on disk, not in Python feature arrays.

When analysis cannot keep up, playback continues, lost block/sample counts and
events are recorded, DSP rhythm state resets across missing blocks, and the GUI
warns of incomplete measurements. Decoder/output underruns and diagnostic/GUI
update losses have separate counters. Each row includes processing and queue-delay
measurements; aggregate maxima, mean processing time and budget overruns are
recorded. GUI update loss does not discard the underlying disk recording.

Final JSON is assembled from temporary NDJSON files, validated by streaming ijson
parsing, then published to a uniquely reserved filename. Existing files are never
overwritten. Temporary files are removed after success; a recording failure leaves
the spool for diagnosis. Native dependency, device and decoding failures are
reported to the CMD menu. Partial recordings are labelled; success is never used
to claim playback finished after an error.

## Validation

```bat
.venv\Scripts\python.exe -m unittest discover -s tests -v
.venv\Scripts\python.exe tools\validate_live.py
.venv\Scripts\python.exe tools\validate_live.py --hardware --report examples\live_validation.json
```

The last command intentionally plays a very quiet two-second test tone and a
short early-close test through the default output device, with a hidden Tk window.
Automated unit tests use fake output devices and require no speakers. Tests cover
metronome ground-truth matching, sine/silence, exact sample times, queue drops,
pause/resume, future-buffer latency, decoding errors, dependency errors, resource
cleanup, incremental JSON, menu routing and the preserved offline/video workflows.

[Recorded validation results](examples/live_validation.json) include a synthetic
eight-second 120 BPM track: 15/15 onsets matched, no extras, maximum absolute event
error about 22.4 ms; 10 estimated beats matched and 5 intentionally missed during
tempo warm-up. This tolerance-based synthetic result is not real-music accuracy
or end-to-end speaker/GUI alignment evidence. The hardware smoke test records the
actual output-device latency and underflow/drop counters, rather than assuming a
25 ms result. Physical speaker audibility was not independently measured.

The final local run verified WAV, FLAC and MP3 decoding. A real two-second output
session exercised Tk, pause/resume and JSON finalisation: zero dropped analysis
buffers, zero decoder/output underruns, mean DSP time about 1.19 ms per block,
maximum queue delay about 3.65 ms, and reported device latency about 92.9 ms.
Closing a second window during playback saved a partial session and released its
stream/workers. All 35 automated tests passed (0 failures); the 14 live-specific
tests were rerun after the final cleanup safeguards and also passed. Hardware
results are device-specific and the software detector metrics use synthetic audio.
