"""WASAPI speaker-loopback only. Never falls back to a microphone or replays captured PCM."""
import os
from queue import Full
import threading
from time import perf_counter
from types import SimpleNamespace

import numpy as np

from live_engine import LiveEngine, LiveSettings
from live_recording import LiveRecorder
from live_dsp import StreamingFeatures


def require_loopback_dependencies():
    if os.name != 'nt':
        raise RuntimeError('WASAPI capture requires Windows.')
    try:
        import pyaudiowpatch
        import ijson
        import tkinter
    except (ImportError, OSError) as error:
        raise RuntimeError('Install Windows loopback support with '
                           '.venv\\Scripts\\python.exe -m pip install -r requirements-live.txt '
                           'and include Tcl/Tk in Python.') from error
    return pyaudiowpatch


def select_loopback(client, api, index=None):
    try:
        device = (client.get_default_wasapi_loopback() if index is None
                  else client.get_device_info_by_index(index))
    except (OSError, LookupError) as error:
        raise RuntimeError('No usable speaker-loopback endpoint. Check the Windows output device; '
                           'use --list-loopback-devices and --device INDEX if needed.') from error
    if (not device.get('isLoopbackDevice') or device.get('maxInputChannels', 0) < 1
            or client.get_host_api_info_by_index(device['hostApi'])['type'] != api.paWASAPI):
        raise ValueError('Select a WASAPI speaker-loopback endpoint. Microphone devices are not accepted.')
    return device


def list_loopback_devices():
    api = require_loopback_dependencies()
    client = api.PyAudio()
    try:
        try:
            default = select_loopback(client, api)
        except RuntimeError:
            default = {}
        return [dict(index=d['index'], name=d['name'], sampleRate=d['defaultSampleRate'],
                     default=d['index'] == default.get('index'))
                for d in client.get_loopback_device_info_generator() if d.get('isLoopbackDevice')]
    finally:
        client.terminate()


class LoopbackEngine(LiveEngine):
    is_capture = True
    is_loopback = True
    is_microphone = False
    source_name = 'system_audio'
    source_type = 'windows.wasapi.loopback'

    def select_device(self, client, api, index):
        return select_loopback(client, api, index)

    def transform_capture_block(self,block):
        return block

    def capture_metadata(self):
        return {}

    def __init__(self, folder, settings=None, audio_api=None, export_mp4=True, video_mode='preview'):
        api = audio_api or require_loopback_dependencies()
        self.client = api.PyAudio()
        self.terminated = False
        try:
            selected = settings or LiveSettings()
            selected.validate()
            self.device_info = self.select_device(self.client, api, selected.device)
            sr = int(self.device_info['defaultSampleRate'])
            channels = int(self.device_info['maxInputChannels'])
            info = SimpleNamespace(samplerate=sr, channels=channels, frames=0, duration=0)
            super().__init__(self.source_name, folder, selected, api, _source_info=info)
            self.capture_label = self.device_info['name']
            self.capture_latency = None
            self.stream_released = False
            self.audio_capture = None
            self.export_mp4, self.video_mode = export_mp4, video_mode
            self.video_path = None
            self.export_status = ''
            self.stats.update(captureOverflows=0, analysisPausedSamples=0)
        except BaseException:
            self.client.terminate()
            self.terminated = True
            raise

    def start(self):
        if self.started:
            return
        self.recorder = LiveRecorder(self.folder)
        self.started = True
        try:
            from capture_audio import CaptureAudio
            self.audio_capture = CaptureAudio(self.folder, self.info.samplerate, self.info.channels,
                                              blocks=max(8,self.settings.queue_blocks*4))
            self.analysis_thread = threading.Thread(target=self._analyse, daemon=True)
            self.analysis_thread.start()
            self.stream = self.client.open(format=self.audio_api.paFloat32,
                channels=self.info.channels, rate=self.info.samplerate, input=True,
                input_device_index=self.device_info['index'], frames_per_buffer=self.settings.hop,
                stream_callback=self.capture_callback, start=False)
            self.capture_latency = float(self.stream.get_input_latency())
            self.stream.start_stream()
        except BaseException as error:
            self.error = f'WASAPI capture could not start: {error}'
            self.request_stop()
            self._release_capture()
            self.analysis_stop.set()
            if self.analysis_thread:
                self.analysis_thread.join(timeout=5)
            if self.audio_capture:
                try:
                    self.audio_capture.finish(self.frozen_position)
                except Exception:
                    pass  # Preserve the original start error and retained audio diagnostics.
            self.recorder.close()
            raise

    def capture_callback(self, in_data, frames, time_info, status):
        if self.stop_requested.is_set():
            return None, self.audio_api.paComplete
        start = self.submitted_samples
        if status:
            self.stats['lastCaptureStatusFlags'] = int(status)
            overflow = bool(status & self.audio_api.paInputOverflow)
            self.stats['captureOverflows'] += int(overflow)
            self._diagnostic('captureOverflow' if overflow else 'captureStatus', 0)
        if frames != self.settings.hop or len(in_data) != frames*self.info.channels*4:
            self.error = 'Unexpected WASAPI capture block size; capture stopped safely.'
            return None, self.audio_api.paAbort
        # A read-only view retains the callback bytes; no decoding, FFT or disk I/O here.
        block = np.frombuffer(in_data, dtype=np.float32).reshape(frames, self.info.channels)
        block = self.transform_capture_block(block)
        if self.audio_capture:
            # Independent of analysis queue/pauses; no captured audio is written in this callback.
            self.audio_capture.offer(start, block)
        if self.paused.is_set():
            self.submitted_samples += frames
            self.stats['analysisPausedSamples'] += frames
            return None, self.audio_api.paContinue
        try:
            self.analysis_queue.put_nowait((start, block, perf_counter()))
        except Full:
            self.stats['analysisDroppedBuffers'] += 1
            self.stats['analysisDroppedSamples'] += frames
            self._diagnostic('analysisDroppedBuffer', frames)
        self.submitted_samples += frames
        return None, self.audio_api.paContinue

    def position_samples(self):
        return self.frozen_position if self.finalized or self.stop_requested.is_set() else self.submitted_samples

    def pause(self):
        if not self.paused.is_set():
            self._diagnostic('analysisPaused')
        self.paused.set()

    def resume(self):
        if self.paused.is_set():
            self._diagnostic('analysisResumed')
        self.paused.clear()

    def poll_error(self):
        if self.audio_capture and self.audio_capture.error and not self.stop_requested.is_set():
            self.error = self.audio_capture.error
            self.request_stop()
        if self.started and self.stream and not self.stop_requested.is_set():
            try:
                active = self.stream.is_active()
            except Exception:
                active = False
            if not active:
                self.error = self.error or 'Capture device stopped. Check the selected Windows audio device, then start a new session.'
                self.request_stop()

    def _release_capture(self):
        if self.stream:
            try:
                if self.stream.is_active():
                    self.stream.stop_stream()
            except Exception as error:
                self.error = self.error or f'Loopback shutdown: {error}'
            finally:
                try:
                    self.stream.close()
                    self.stream_released = True
                except Exception as error:
                    self.error = self.error or f'Loopback close: {error}'
                self.stream = None
        if not self.terminated:
            try:
                self.client.terminate()
            except Exception as error:
                self.error = self.error or f'Loopback backend release: {error}'
            finally:
                self.terminated = True

    def finish(self, completed=False):
        if self.finalized:
            return self.result_path
        self.request_stop()
        self._release_capture()
        self.analysis_stop.set()
        if self.analysis_thread:
            self.analysis_thread.join(timeout=10)
            if self.analysis_thread.is_alive():
                raise RuntimeError('Analysis worker did not stop; recording spool retained.')
        self.finalized = True
        if self.recorder:
            captured_audio = None
            if self.audio_capture:
                try:
                    captured_audio = self.audio_capture.finish(self.frozen_position)
                except Exception as error:
                    self.error = self.error or str(error)
            self.stats['meanProcessingMs'] = self.stats['totalProcessingMs']/max(1, self.stats['analysedBlocks'])
            metadata = dict(source=self.source_name, mode='live', sourceType=self.source_type,
                sampleRate=self.info.samplerate, durationSeconds=self.frozen_position/self.info.samplerate,
                captureDurationSeconds=self.frozen_position/self.info.samplerate,
                completed=not bool(self.error), completionMeaning='capture session finalised; not song EOF',
                timestampUnits='seconds', eventOrder='detection/write order',
                analysis=dict(hopSize=self.settings.hop, fftSize=self.settings.fft_size,
                    guiFps=self.settings.gui_fps, queueBlocks=self.settings.queue_blocks,
                    featureTimestamp='end of received capture block / sample rate', detection=StreamingFeatures.settings()),
                capture=dict(deviceIndex=self.device_info['index'], deviceName=self.capture_label,
                    channels=self.info.channels, reportedInputLatencySeconds=self.capture_latency,
                    clock='received WASAPI PCM sample count; session-relative, not original song time',
                    pause='capture clock continues; analysis/recording pauses, external audio continues',
                    microphone=self.is_microphone, outputPlayback=False,
                    temporaryAudioPath=str(captured_audio) if captured_audio else None,
                    audioCleanup='removed after verified MP4; retained on video failure',
                    overflow='unknown lost hardware samples are reported, not fabricated',
                    **self.capture_metadata()),
                eventTypes=['onset', 'estimatedBeat', 'analysisGap', 'analysisDroppedBuffer',
                            'captureOverflow', 'captureStatus', 'analysisPaused', 'analysisResumed']+
                           (['microphoneGainChanged'] if self.is_microphone else []),
                diagnostics=dict(self.stats, audioRecording=dict(self.audio_capture.stats) if self.audio_capture else {}), error=self.error)
            self.result_path = self.recorder.finish(metadata)
            print(f'\nJSON saved and validated:\n{self.result_path}', flush=True)
            if self.export_mp4:
                try:
                    if not captured_audio or not self.frozen_position:
                        raise ValueError('No captured audio available for MP4 export.')
                    from video_export import export_video
                    self.export_status = f'JSON saved: {self.result_path}. Rendering ECG MP4...'
                    print('Rendering ECG MP4 from recorded live events and captured audio...', flush=True)
                    def progress(percent, message):
                        self.export_status = f'JSON saved. [{percent}%] {message}'
                        print(f'[{percent:3d}% ] {message}', flush=True)
                    self.video_path, report = export_video(captured_audio, self.result_path, self.folder,
                        mode=self.video_mode, progress=progress, source_format='live')
                    self.export_status = f'JSON and MP4 validated: {self.video_path}'
                    print(f'[100% ] Capture JSON and ECG MP4 completed.\nVIDEO:\n{self.video_path}', flush=True)
                    try:
                        self.audio_capture.cleanup()
                    except OSError as cleanup_error:
                        print(f'Both outputs are valid; temporary audio could not be removed: {cleanup_error}', flush=True)
                except Exception as error:
                    self.error = self.error or f'JSON saved; MP4 export failed: {error}'
                    self.export_status = str(self.error)
                    print(self.error, flush=True)
        return self.result_path
