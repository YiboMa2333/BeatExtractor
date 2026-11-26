"""Offline librosa analysis and verified, collision-safe JSON export."""
import ctypes
import json
import os
from pathlib import Path
import uuid

import librosa
import numpy as np

SAMPLE_RATE = 22050
HOP_LENGTH = 512


def validate_path(raw):
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        value = value[1:-1]
    if not value:
        raise ValueError("Please enter a WAV file path.")
    path = Path(value)
    if not path.is_file():
        raise ValueError("File not found.")
    if path.suffix.lower() != '.wav':
        raise ValueError("Please select a file with a .wav extension.")
    if path.stat().st_size == 0:
        raise ValueError("The WAV file is empty.")
    return path


def downloads_folder():
    """Resolve redirected Downloads via Windows; fall back to ~/Downloads."""
    try:
        return _known_downloads() or Path.home() / 'Downloads'
    except (AttributeError, OSError):
        return Path.home() / 'Downloads'


def _known_downloads():
    if os.name == 'nt':
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [('data1', wintypes.DWORD), ('data2', wintypes.WORD),
                        ('data3', wintypes.WORD), ('data4', ctypes.c_ubyte * 8)]

        folder_id = GUID.from_buffer_copy(uuid.UUID(
            '374DE290-123F-4565-9164-39C4925E467B').bytes_le)
        pointer = ctypes.c_void_p()
        shell = ctypes.windll.shell32.SHGetKnownFolderPath
        shell.argtypes = [ctypes.POINTER(GUID), wintypes.DWORD, wintypes.HANDLE,
                          ctypes.POINTER(ctypes.c_void_p)]
        shell.restype = ctypes.c_long
        try:
            if shell(ctypes.byref(folder_id), 0, None, ctypes.byref(pointer)) == 0:
                return Path(ctypes.wstring_at(pointer))
        finally:
            if pointer.value:
                free = ctypes.windll.ole32.CoTaskMemFree
                free.argtypes = [ctypes.c_void_p]
                free(pointer)
    return None


def analyse(path, progress):
    progress(0, 'Starting analysis. Percentages mark completed stages.')
    samples, sr = librosa.load(path, sr=SAMPLE_RATE, mono=True)
    progress(10, 'Loaded WAV file.')
    if samples.size == 0 or not np.all(np.isfinite(samples)):
        raise ValueError('WAV contains empty or invalid audio samples.')
    duration = len(samples) / sr
    progress(25, 'Prepared audio samples.')
    onset = librosa.onset.onset_strength(y=samples, sr=sr, hop_length=HOP_LENGTH)
    progress(45, 'Calculated onset strength.')
    active = np.max(np.abs(samples)) > 1e-8 and np.any(onset > 1e-8)
    tempo = float(np.asarray(librosa.feature.tempo(
        onset_envelope=onset, sr=sr, hop_length=HOP_LENGTH)).flat[0]) if active else 0.0
    progress(65, 'Estimated tempo.')
    frames = librosa.beat.beat_track(onset_envelope=onset, sr=sr,
        hop_length=HOP_LENGTH, bpm=tempo)[1] if active else np.array([])
    times = librosa.frames_to_time(frames, sr=sr, hop_length=HOP_LENGTH)
    times = sorted(set(float(t) for t in times if np.isfinite(t) and 0 <= t <= duration))
    if len(times) < 2:
        times, tempo = [], 0.0
    progress(80, 'Detected beat positions and converted timestamps.')
    result = dict(source=path.name, analysisMethod='librosa.offline',
                  estimatedBpm=tempo, durationSeconds=duration,
                  beatCount=len(times), beatTimesSeconds=times)
    progress(90, 'Prepared JSON output.')
    return result


def save_result(result, folder):
    folder.mkdir(parents=True, exist_ok=True)
    stem = Path(result['source']).stem + '_beats'
    number = 0
    while True:
        path = folder / (stem + (f' ({number})' if number else '') + '.json')
        try:
            output = path.open('x', encoding='utf-8')
            break
        except FileExistsError:
            number += 1
    try:
        with output:
            json.dump(result, output, indent=2, allow_nan=False)
            output.write('\n')
        with path.open(encoding='utf-8') as saved:
            if json.load(saved) != result:
                raise ValueError('Saved JSON did not match the analysis.')
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path
