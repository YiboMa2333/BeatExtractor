"""Manual capture/import boundary; no undocumented MilkDrop automation."""
from dataclasses import dataclass
import math
from pathlib import Path
import subprocess

CAPTURE_GUIDE = """
MilkDrop capture (no webcam needed):
1. Download MilkDrop 3 from https://www.milkdrop3.com/ and extract it.
2. Start MilkDrop, choose a preset using its preset browser (F1 shows help).
3. Play this exact WAV from the beginning in your usual audio player. Ensure
   MilkDrop reacts to the playback device you are using.
4. In OBS Studio add Window Capture for MilkDrop. Check the preview; if blank,
   try Game Capture. Record at 30 FPS, preferably at the final resolution.
5. Start recording BEFORE playing the WAV and stop AFTER the song ends.
   Desktop audio in the recording can help you find the WAV start in an editor.
6. Note the recording time where WAV time zero occurs. Enter that time below
   in milliseconds: e.g. WAV starts at recording 2.5s => offset +2500.
   Compare a distinct transient near the beginning AND end to check drift.
Only record local audio you are entitled to use. No protected-stream access.
"""


def input_path(raw):
    return Path(raw.strip().strip('"'))


def launch_milkdrop(raw):
    path = input_path(raw)
    if not path.is_file() or path.suffix.lower() != '.exe':
        raise ValueError('MilkDrop executable not found. Import an existing recording or use the built-in background.')
    subprocess.Popen([str(path.resolve())], cwd=path.parent,
                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def parse_offset(raw):
    try:
        value = float(raw)
    except ValueError as error:
        raise ValueError('Offset must be a finite number of milliseconds.') from error
    if not math.isfinite(value):
        raise ValueError('Offset must be a finite number of milliseconds.')
    return value / 1000


@dataclass(frozen=True)
class Recording:
    path: Path
    offset_seconds: float

    def validate(self, info, audio_duration, fps=30):
        if not self.path.is_file():
            raise ValueError('MilkDrop recording not found.')
        if not math.isfinite(self.offset_seconds):
            raise ValueError('Invalid recording offset.')
        video = next((s for s in info['streams'] if s.get('codec_type') == 'video'), None)
        if video is None:
            raise ValueError('Recording has no video stream.')
        length = float(video.get('duration') or info['format'].get('duration', 0))
        if not math.isfinite(length) or length <= 0:
            raise ValueError('Cannot determine recording duration.')
        if self.offset_seconds >= length or self.offset_seconds <= -audio_duration:
            raise ValueError('Offset leaves no overlapping recording footage.')
        if length - self.offset_seconds + 1 / fps < audio_duration:
            raise ValueError('Recording is too short after alignment. Capture the complete song; footage is not looped or stretched.')
        return length
