"""Bounded background PCM writer. Missing queued audio becomes marked silence, not time drift."""
from pathlib import Path
from queue import Queue, Empty, Full
import shutil
import tempfile
import threading

import numpy as np
import soundfile as sf


class CaptureAudio:
    def __init__(self, folder, sample_rate, channels, blocks=128):
        self.work = Path(tempfile.mkdtemp(prefix='.capture-audio-', dir=folder))
        self.path = self.work/'captured.wav'
        self.sr, self.channels = sample_rate, channels
        self.queue = Queue(blocks)
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.target_frames = 0
        self.error = None
        self.stats = dict(droppedBuffers=0, droppedSamples=0, silenceReplacementSamples=0)
        self.thread = threading.Thread(target=self._write, daemon=True)
        self.thread.start()
        if not self.ready.wait(10) or self.error:
            self.stop.set()
            self.thread.join(timeout=5)
            raise RuntimeError(self.error or 'Audio recording worker did not start.')

    def offer(self, start, block):
        try:
            self.queue.put_nowait((start, block))
        except Full:
            self.stats['droppedBuffers'] += 1
            self.stats['droppedSamples'] += len(block)

    def _silence(self, output, count):
        silence = np.zeros((4096, self.channels), dtype=np.float32)
        while count:
            size = min(count, len(silence))
            output.write(silence[:size])
            count -= size
            self.stats['silenceReplacementSamples'] += size

    def _write(self):
        try:
            # RF64 avoids the ordinary WAV 4GB limit for long system-audio sessions.
            with sf.SoundFile(self.path, 'w', samplerate=self.sr, channels=self.channels,
                              format='RF64', subtype='FLOAT') as output:
                self.ready.set()
                while not self.stop.is_set() or not self.queue.empty():
                    try:
                        start, block = self.queue.get(timeout=.05)
                    except Empty:
                        continue
                    if not np.all(np.isfinite(block)):
                        raise ValueError('Capture contains invalid PCM samples.')
                    gap = start-output.tell()
                    if gap < 0:
                        raise ValueError('Captured audio blocks arrived out of order.')
                    self._silence(output, gap)
                    output.write(block)
                gap = self.target_frames-output.tell()
                if gap > 0:
                    self._silence(output, gap)
                elif gap < 0:
                    output.truncate(self.target_frames)
                output.flush()
        except Exception as error:
            self.error = f'Captured audio recording failed: {error}'
        finally:
            self.ready.set()

    def finish(self, frames):
        self.target_frames = frames
        self.stop.set()
        self.thread.join(timeout=30)
        if self.thread.is_alive():
            raise RuntimeError('Captured audio writer did not stop.')
        if self.error:
            raise RuntimeError(self.error)
        return self.path

    def cleanup(self):
        if self.thread.is_alive():
            raise RuntimeError('Cannot clean up an active audio recording.')
        shutil.rmtree(self.work)
