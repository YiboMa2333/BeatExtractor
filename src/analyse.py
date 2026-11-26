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
FRAME_LENGTH = 2048


def beat_features(samples, sr, frames, onset):
    """Evaluate only the selected grid frames, in small batches rather than full-song spectra.

    The centered 2048-sample windows are identical to the corresponding columns
    of a full centered STFT. RMS uses unwindowed samples; timbre uses Hann windows.
    No-energy spectral moments and log(0) are undefined, so we export null.
    These are signal descriptors, not perceptual loudness or timbre identification.
    """
    beats = []
    for start in range(0, len(frames), 128):
        selected = frames[start:start + 128]
        windows = np.zeros((len(selected), FRAME_LENGTH), dtype=np.float32)
        for row, frame in enumerate(selected):
            left = int(frame) * HOP_LENGTH - FRAME_LENGTH // 2
            lo, hi = max(0, left), min(len(samples), left + FRAME_LENGTH)
            windows[row, lo-left:hi-left] = samples[lo:hi]
        rms = librosa.feature.rms(y=windows, frame_length=FRAME_LENGTH,
                                 hop_length=FRAME_LENGTH, center=False)[:, 0, 0]
        magnitude = np.abs(librosa.stft(windows, n_fft=FRAME_LENGTH,
                                        hop_length=FRAME_LENGTH, center=False))[:, :, 0].T
        centroids = librosa.feature.spectral_centroid(S=magnitude, sr=sr)
        bandwidths = librosa.feature.spectral_bandwidth(S=magnitude, sr=sr, centroid=centroids)
        for row, frame in enumerate(selected):
            amplitude = float(rms[row])
            has_energy = float(magnitude[:, row].sum()) > 1e-12
            beats.append(dict(beatIndex=len(beats) + 1,
                              timeSeconds=float(frame * HOP_LENGTH / sr),
                              amplitude=amplitude,
                              loudnessDb=float(20 * np.log10(amplitude)) if amplitude > 0 else None,
                              timbreCentroid=float(centroids[0, row]) if has_energy else None,
                              timbreBandwidth=float(bandwidths[0, row]) if has_energy else None,
                              beatStrength=float(onset[int(frame)])))
    return beats


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
    # Let beat_track estimate tempo once; avoid a second identical tempo pass.
    tempo, frames = librosa.beat.beat_track(onset_envelope=onset, sr=sr,
        hop_length=HOP_LENGTH) if active else (0.0, np.array([], dtype=int))
    tempo = float(np.asarray(tempo).flat[0])
    frames = sorted(set(int(f) for f in frames if 0 <= f < len(onset)
                        and f * HOP_LENGTH / sr <= duration))
    if len(frames) < 2:
        frames, tempo = [], 0.0
    progress(65, 'Estimated tempo and detected beat positions.')
    beats = beat_features(samples, sr, frames, onset)
    progress(80, 'Mapped RMS, onset strength and spectral features to beat frames.')
    result = dict(source=path.name, analysisMethod='librosa.offline.features.v1',
                  estimatedBpm=tempo, durationSeconds=duration,
                  beatCount=len(beats), beats=beats,
                  analysisParameters=dict(sampleRate=sr, hopLength=HOP_LENGTH,
                                          featureFrameLength=FRAME_LENGTH, beatIndexBase=1))
    progress(90, 'Prepared JSON output.')
    return result


def save_result(result, folder):
    folder.mkdir(parents=True, exist_ok=True)
    stem = Path(result['source']).stem + '_features'
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
