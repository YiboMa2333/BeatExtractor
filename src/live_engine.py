"""One decoded PCM pipeline: bounded playback -> callback -> bounded analysis."""
from dataclasses import dataclass
from collections import deque
import math
from pathlib import Path
from queue import Queue, Empty, Full
import threading
from time import perf_counter

import numpy as np
import soundfile as sf

from live_dsp import StreamingFeatures
from live_recording import LiveRecorder


def require_live_dependencies():
    try:
        import sounddevice
        import ijson
        import tkinter
    except (ImportError, OSError) as error:
        raise RuntimeError('Live mode requires sounddevice, ijson and Tkinter. Run '
                           '.venv\\Scripts\\python.exe -m pip install -r requirements-live.txt; '
                           'include Tcl/Tk in your Python installation.') from error
    return sounddevice


def inspect_audio(raw):
    path = Path(str(raw).strip().strip('"'))
    if not path.is_file():
        raise ValueError('Audio file not found.')
    if path.suffix.lower() not in ('.wav', '.flac', '.mp3'):
        raise ValueError('Live mode supports WAV, FLAC and decoder-supported MP3 files.')
    try:
        info = sf.info(path)
    except (RuntimeError, OSError) as error:
        raise ValueError('Cannot decode this audio with the installed SoundFile/libsndfile. '
                         'Try WAV/FLAC or convert the file to WAV first.') from error
    if info.frames <= 0:
        raise ValueError('Audio file is empty.')
    return path, info


@dataclass
class LiveSettings:
    hop: int = 512
    fft_size: int = 2048
    gui_fps: int = 30
    queue_blocks: int = 32
    device: int | None = None

    def validate(self):
        if not (64 <= self.hop <= 8192 and self.hop <= self.fft_size <= 32768
                and self.fft_size & (self.fft_size-1) == 0):
            raise ValueError('Use hop 64..8192 and a power-of-two FFT >= hop and <=32768.')
        if not 1 <= self.gui_fps <= 60 or not 2 <= self.queue_blocks <= 1024:
            raise ValueError('GUI FPS must be 1..60 and queue blocks 2..1024.')


class LiveEngine:
    def __init__(self, path, folder, settings=None, audio_api=None, _source_info=None):
        self.path, self.info = (Path(path), _source_info) if _source_info is not None else inspect_audio(path)
        self.folder, self.settings = Path(folder), settings or LiveSettings()
        self.settings.validate()
        self.audio_api = audio_api
        self.playback_queue = Queue(self.settings.queue_blocks)
        self.analysis_queue = Queue(self.settings.queue_blocks)
        self.gui_queue = Queue(256)
        self.diagnostics = Queue(256)
        self.stop_requested = threading.Event()
        self.analysis_stop = threading.Event()
        self.paused = threading.Event()
        self.decoder_ready = threading.Event()
        self.playback_finished = threading.Event()
        self.stream = self.recorder = None
        self.decoder_thread = self.analysis_thread = None
        self.started = self.finalized = False
        self.submitted_samples = 0
        self.clock_segment = (0, 0, float('inf'))
        self.clock_segments = deque(maxlen=4096)
        self.frozen_position = 0
        self.error = None
        self.result_path = None
        self.output_latency = None
        self.stats = dict(analysisDroppedBuffers=0, analysisDroppedSamples=0,
                          playbackUnderruns=0, decoderUnderruns=0, diagnosticDrops=0,
                          guiDroppedUpdates=0, analysedBlocks=0, maxQueueDelayMs=0.0,
                          totalProcessingMs=0.0, maxProcessingMs=0.0, processingOverruns=0)
        self.dsp = StreamingFeatures(self.info.samplerate, self.settings.hop, self.settings.fft_size)

    def _diagnostic(self, kind, count=0):
        item = dict(type=kind, timeSeconds=self.submitted_samples/self.info.samplerate,
                    sampleIndex=self.submitted_samples, sampleCount=count)
        try:
            self.diagnostics.put_nowait(item)
        except Full:
            self.stats['diagnosticDrops'] += 1

    def start(self):
        if self.started:
            return
        self.audio_api = self.audio_api or require_live_dependencies()
        self.recorder = LiveRecorder(self.folder)
        self.started = True
        try:
            self.decoder_thread = threading.Thread(target=self._decode, daemon=True)
            self.analysis_thread = threading.Thread(target=self._analyse, daemon=True)
            self.decoder_thread.start()
            self.analysis_thread.start()
            if not self.decoder_ready.wait(10) or self.error:
                raise RuntimeError(self.error or 'Decoder did not start.')
            self.stream = self.audio_api.OutputStream(
                samplerate=self.info.samplerate, blocksize=self.settings.hop,
                channels=min(2, self.info.channels), dtype='float32', device=self.settings.device,
                callback=self.callback, finished_callback=self.playback_finished.set, latency='low')
            self.stream.start()
            self.output_latency = float(self.stream.latency)
        except BaseException as error:
            self.error = f'Playback could not start: {error}'
            self.request_stop()
            self.analysis_stop.set()
            if self.stream:
                try:
                    self.stream.close()
                except Exception:
                    pass  # Preserve the original start failure while still joining workers.
            for thread in (self.decoder_thread, self.analysis_thread):
                if thread:
                    thread.join(timeout=5)
            self.recorder.close()
            raise

    def _put_playback(self, value):
        while not self.stop_requested.is_set():
            try:
                self.playback_queue.put(value, timeout=.05)
                return True
            except Full:
                pass
        return False

    def _decode(self):
        try:
            with sf.SoundFile(self.path) as source:
                while not self.stop_requested.is_set():
                    block = source.read(self.settings.hop, dtype='float32', always_2d=True)
                    if not len(block):
                        self._put_playback(None)
                        break
                    if not np.all(np.isfinite(block)):
                        raise ValueError('Audio contains non-finite PCM samples.')
                    if block.shape[1] > 2:
                        block = np.repeat(block.mean(axis=1, keepdims=True), 2, axis=1)
                    # Clip ONCE, before playback and analysis, so both receive identical PCM.
                    block = np.ascontiguousarray(np.clip(block, -1, 1), dtype=np.float32)
                    if not self._put_playback(block):
                        break
                    self.decoder_ready.set()
        except Exception as error:
            self.error = f'Decoder failed: {error}'
            self.request_stop()
        finally:
            self.decoder_ready.set()

    def callback(self, outdata, frames, time_info, status):
        """No decoding, FFT, disk I/O, logging or GUI calls; only copies and queue offers."""
        outdata.fill(0)
        if status.output_underflow:
            self.stats['playbackUnderruns'] += 1
            self._diagnostic('playbackUnderrun')
        if self.stop_requested.is_set():
            raise self.audio_api.CallbackStop
        if self.paused.is_set():
            return
        if frames != self.settings.hop:
            self.error = 'Audio driver returned an unexpected block size.'
            raise self.audio_api.CallbackStop
        try:
            block = self.playback_queue.get_nowait()
        except Empty:
            self.stats['decoderUnderruns'] += 1
            self._diagnostic('decoderUnderrun', frames)
            return  # Output silence, preserving the source-content sample timeline.
        if block is None:
            raise self.audio_api.CallbackStop
        count = len(block)
        outdata[:count] = block
        start = self.submitted_samples
        self.clock_segment = (start, count, float(time_info.outputBufferDacTime))
        self.clock_segments.append(self.clock_segment)
        try:
            self.analysis_queue.put_nowait((start, block, perf_counter()))
        except Full:
            self.stats['analysisDroppedBuffers'] += 1
            self.stats['analysisDroppedSamples'] += count
            self._diagnostic('analysisDroppedBuffer', count)
        self.submitted_samples += count

    def _analyse(self):
        try:
            while not self.analysis_stop.is_set() or not self.analysis_queue.empty():
                try:
                    start, block, queued_at = self.analysis_queue.get(timeout=.05)
                except Empty:
                    self._flush_diagnostics()
                    continue
                began = perf_counter()
                delay_ms = (began-queued_at)*1000
                row, events, spectrum = self.dsp.process(block, start)
                elapsed_ms = (perf_counter()-began)*1000
                if hasattr(self, 'audio_capture') and self.audio_capture:
                    row['recordedAudioSampleIndex'] = start+len(block)
                self.stats['analysedBlocks'] += 1
                self.stats['maxQueueDelayMs'] = max(self.stats['maxQueueDelayMs'], delay_ms)
                self.stats['maxProcessingMs'] = max(self.stats['maxProcessingMs'], elapsed_ms)
                self.stats['totalProcessingMs'] += elapsed_ms
                if elapsed_ms > len(block)/self.info.samplerate*1000:
                    self.stats['processingOverruns'] += 1
                row['processingMs'], row['queueDelayMs'] = elapsed_ms, delay_ms
                self.recorder.append('features', row)
                for event in events:
                    self.recorder.append('events', event)
                self._flush_diagnostics()
                try:
                    self.gui_queue.put_nowait((row, events, spectrum))
                except Full:
                    self.stats['guiDroppedUpdates'] += 1
                self.analysis_queue.task_done()
            self._flush_diagnostics()
        except Exception as error:
            self.error = f'Analysis/recording failed: {error}'
            self.request_stop()

    def _flush_diagnostics(self):
        while True:
            try:
                self.recorder.append('events', self.diagnostics.get_nowait())
            except Empty:
                return

    def position_samples(self):
        if self.finalized or self.stop_requested.is_set():
            return self.frozen_position
        if not self.stream:
            return 0
        now = self.stream.time
        # Callbacks run AHEAD of audible output. Do not jump to a future queued block.
        for start, count, dac in reversed(tuple(self.clock_segments)):
            if math.isfinite(dac) and now >= dac:
                return start+max(0, min(count, int((now-dac)*self.info.samplerate)))
        return 0

    def pause(self):
        self.paused.set()  # Submitted buffers drain; no content samples consumed while paused.

    def resume(self):
        self.paused.clear()  # Same DSP/timeline state, no restart or duplicated samples.

    def request_stop(self):
        self.frozen_position = self.position_samples()
        self.stop_requested.set()

    def finish(self, completed=False):
        if self.finalized:
            return self.result_path
        if completed:
            self.frozen_position = self.info.frames
        else:
            self.frozen_position = self.position_samples()
        self.stop_requested.set()
        if self.stream:
            try:
                if not completed:
                    self.stream.abort()
                else:
                    self.stream.stop()
            except Exception as error:
                self.error = self.error or f'Playback shutdown: {error}'
            finally:
                try:
                    self.stream.close()
                except Exception as error:
                    self.error = self.error or f'Playback close: {error}'
        self.analysis_stop.set()
        for thread in (self.decoder_thread, self.analysis_thread):
            if thread:
                thread.join(timeout=10)
                if thread.is_alive():
                    raise RuntimeError('Audio worker did not stop; recording spool retained.')
        self.finalized = True
        if self.recorder:
            self.stats['meanProcessingMs'] = self.stats['totalProcessingMs']/max(1, self.stats['analysedBlocks'])
            metadata = dict(source=self.path.name, mode='live', sampleRate=self.info.samplerate,
                            durationSeconds=self.info.frames/self.info.samplerate,
                            playedDurationSeconds=self.frozen_position/self.info.samplerate,
                            completed=bool(completed and not self.error), timestampUnits='seconds',
                            eventOrder='detection/write order; use timeSeconds for source-content position',
                            analysis=dict(hopSize=self.settings.hop, fftSize=self.settings.fft_size,
                                          guiFps=self.settings.gui_fps, queueBlocks=self.settings.queue_blocks,
                                          window='hann', featureTimestamp='end sample of causal trailing window',
                                          spectrum='full FFT computed once per hop; latest spectrum in GUI, not stored per hop',
                                          detection=StreamingFeatures.settings()),
                            eventTypes=['onset', 'estimatedBeat', 'analysisGap', 'analysisDroppedBuffer',
                                        'playbackUnderrun', 'decoderUnderrun'],
                            playback=dict(reportedOutputLatencySeconds=self.output_latency,
                                          clock='PortAudio outputBufferDacTime + stream.time; content sample timeline',
                                          channels=min(2, self.info.channels),
                                          pcm='float32 clipped to [-1,1]; >2 channels averaged to stereo'),
                            diagnostics=dict(self.stats), error=self.error)
            self.result_path = self.recorder.finish(metadata)
        return self.result_path
