"""Bounded-memory PCM streaming and an honest, automatic procedural fallback."""
import ctypes.util
import importlib.util
import json
import math
from pathlib import Path
import subprocess
import sys

import numpy as np
from PIL import Image
import soundfile as sf


def inspect_environment():
    root = Path(__file__).resolve().parents[1]
    libraries = [str(path) for path in (root / 'tools' / 'projectm').glob('**/*')
                 if path.suffix.lower() in ('.dll', '.so', '.dylib') and 'projectm' in path.name.lower()]
    for name in ('projectM-4', 'projectM'):
        found = ctypes.util.find_library(name)
        if found and found not in libraries:
            libraries.append(found)
    bindings = [name for name in ('projectm', 'pyprojectm') if importlib.util.find_spec(name)]
    presets = list((root / 'tools' / 'projectm').glob('**/*.milk'))
    try:
        run = subprocess.run([sys.executable, '-B', str(Path(__file__).with_name('opengl_probe.py'))],
                             capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=15)
        opengl = json.loads(run.stdout) if run.returncode == 0 else {
            'available': False, 'reason': f'WGL probe exited {run.returncode}.'}
    except (OSError, subprocess.TimeoutExpired, ValueError) as error:
        opengl = {'available': False, 'reason': str(error)}
    reason = ('No installed libprojectM found in tools/projectm or library search path.' if not libraries
              else 'Native projectM found, but no validated offline projectM backend is enabled in this build.')
    return dict(renderer='builtin.procedural', fallbackReason=reason, projectMLibraries=libraries,
                pythonBindings=bindings, localMilkPresetCount=len(presets), opengl=opengl)


class PCMFrames:
    """One interval per video frame, indexed with integer sample arithmetic."""
    def __init__(self, path, fps=30):
        self.audio = sf.SoundFile(path)
        self.fps, self.next_frame = fps, 0
        self.sample_rate, self.sample_count = self.audio.samplerate, len(self.audio)

    def samples(self, n):
        if n != self.next_frame:
            raise ValueError('PCM frames must be consumed sequentially from frame zero.')
        start = min(self.sample_count, n * self.sample_rate // self.fps)
        end = min(self.sample_count, (n + 1) * self.sample_rate // self.fps)
        self.next_frame += 1
        samples = self.audio.read(end - start, dtype='float32', always_2d=True)
        if not np.all(np.isfinite(samples)):
            raise ValueError('Invalid PCM sample in WAV during rendering.')
        return samples

    def close(self):
        self.audio.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class ProceduralBackground:
    """Fixed equations, no random presets, no extra beat detector or wall clock."""
    name = 'builtin.procedural'

    def __init__(self, size, fps=30):
        self.size, self.fps = size, fps
        self.level, self.bass = 0.0, 0.0
        # Evaluate a small field and scale a single frame to the output dimensions.
        width, height = 480, 270
        x, y = np.meshgrid(np.linspace(-1.78, 1.78, width, dtype=np.float32),
                           np.linspace(-1, 1, height, dtype=np.float32))
        self.radius, self.angle = np.sqrt(x*x + y*y), np.arctan2(y, x)
        self.x, self.y = x, y

    def frame(self, n, pcm, sample_rate):
        t = n / self.fps
        mono = pcm.mean(axis=1) if pcm.size else np.zeros(1, dtype=np.float32)
        level = min(1.0, float(np.sqrt(np.mean(mono * mono))) * 3)
        spectrum = abs(np.fft.rfft(mono * np.hanning(len(mono))))
        frequencies = np.fft.rfftfreq(len(mono), 1 / sample_rate)
        bass = min(1.0, float(spectrum[frequencies < 250].sum()) / max(1, len(mono)) * 4)
        # Fixed 30FPS smoothing limits abrupt brightness changes; no onset/beat logic.
        self.level += .15 * (level - self.level)
        self.bass += .12 * (bass - self.bass)
        r, a = self.radius, self.angle
        petals = np.sin(6*a + .65*np.sin(t*.23) + 2*r)
        waves = np.sin(8*r - t*.55 + 1.1*petals + self.bass*.45)
        ribbons = np.exp(-8 * (waves - .35*np.sin(3*a + t*.13))**2)
        halo = np.exp(-2*r*r) * (.45 + .15*np.sin(4*r - t*.3))
        intensity = (ribbons * .6 + halo) * (.55 + .3*self.level)
        channels = []
        for phase, base in ((0, 8), (2.1, 13), (4.2, 25)):
            color = .55 + .35*np.sin(t*.09 + r*1.8 + petals*.4 + phase)
            channels.append(np.clip(base + intensity*color*125, 0, 160))
        array = np.stack(channels, axis=-1).astype(np.uint8)
        return Image.fromarray(array).resize(self.size, Image.Resampling.BILINEAR).convert('RGBA')


def create_renderer(size, fps=30):
    # Do not make speculative ctypes calls into an unvalidated native projectM DLL.
    return ProceduralBackground(size, fps), inspect_environment()
