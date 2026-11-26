"""CPU-only ECG RGB streaming, FFmpeg encoding, and verified output publication."""
from fractions import Fraction
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading

from overlay import FPS, MODES, Overlay, load_features, quantisation_report


def find_tools():
    local = Path(__file__).resolve().parents[1] / 'tools' / 'ffmpeg' / 'bin'
    found = []
    for name in ('ffmpeg', 'ffprobe'):
        candidate = local / (name + ('.exe' if os.name == 'nt' else ''))
        value = str(candidate) if candidate.is_file() else shutil.which(name)
        if not value:
            raise RuntimeError('FFmpeg and FFprobe are required. Install both on PATH or in tools/ffmpeg/bin; see README.')
        found.append(value)
    return tuple(found)


def probe(path, ffprobe):
    run = subprocess.run([ffprobe, '-v', 'error', '-show_streams', '-show_format',
                          '-of', 'json', str(path)], capture_output=True, text=True,
                         encoding='utf-8', errors='replace', timeout=60)
    if run.returncode:
        raise ValueError(f'Cannot read media: {path.name}: {run.stderr[-1500:]}')
    return json.loads(run.stdout)


def validate_mp4(path, duration, tools, size=None, fps=FPS):
    ffmpeg, ffprobe = tools
    info = probe(path, ffprobe)
    video = next((s for s in info['streams'] if s['codec_type'] == 'video'), {})
    audio = next((s for s in info['streams'] if s['codec_type'] == 'audio'), {})
    if video.get('codec_name') != 'h264' or audio.get('codec_name') != 'aac':
        raise ValueError('Output must contain H.264 video and AAC audio.')
    if size and (video.get('width'), video.get('height')) != size:
        raise ValueError('Output has the wrong dimensions.')
    for stream in (video, audio, info['format']):
        length = float(stream.get('duration', 0))
        if not math.isfinite(length) or abs(length-duration) > 1/fps + .025:
            raise ValueError('Output duration does not match WAV within frame/AAC rounding tolerance.')
    if Fraction(video.get('avg_frame_rate', '0/1')) != fps:
        raise ValueError(f'Output must be {fps} FPS.')
    run = subprocess.run([ffmpeg, '-nostdin', '-v', 'error', '-xerror', '-i', str(path),
                          '-map', '0:v:0', '-map', '0:a:0', '-f', 'null', '-'],
                         capture_output=True, text=True, encoding='utf-8', errors='replace',
                         timeout=max(120, duration*4))
    if run.returncode or run.stderr.strip():
        raise ValueError(f'Output decode validation failed: {run.stderr[-1500:]}')
    return info


def reserve_video(folder, source):
    stem = Path(source).stem + '_ecg'
    number = 0
    while True:
        path = folder / (stem + (f' ({number})' if number else '') + '.mp4')
        try:
            with path.open('xb'):
                pass
            return path
        except FileExistsError:
            number += 1


class EncodingProgress:
    """Drain FFmpeg's actual progress while binary RGB frames travel on stdin."""
    def __init__(self, stream):
        self.stream, self.frames, self.seconds = stream, 0, 0.0
        self.thread = threading.Thread(target=self.read, daemon=True)
        self.thread.start()

    def read(self):
        try:
            for line in self.stream:
                key, _, value = line.decode('utf-8', errors='replace').strip().partition('=')
                try:
                    if key == 'frame':
                        self.frames = max(self.frames, int(value))
                    elif key == 'out_time_us':
                        self.seconds = max(self.seconds, int(value)/1_000_000)
                except ValueError:
                    pass
        finally:
            self.stream.close()

    def join(self):
        self.thread.join(timeout=10)


def export_video(wav, json_path, folder, mode='preview', progress=lambda *_: None, tools=None):
    tools = tools or find_tools()
    ffmpeg, ffprobe = tools
    if mode not in MODES:
        raise ValueError('Choose preview or quality mode.')
    size, fps = MODES[mode]
    data = load_features(json_path)  # The persisted features are the ONLY beat source.
    duration = data['durationSeconds']
    wav_info = probe(wav, ffprobe)
    actual = float(wav_info['format']['duration'])
    if not math.isfinite(actual) or abs(actual-duration) > 1/22050 + .00005:
        raise ValueError('WAV duration does not match saved features; reanalyse the source.')
    folder.mkdir(parents=True, exist_ok=True)
    # A conservative working-space estimate, not a guaranteed encoded file size.
    required = int(duration*750_000 + 25_000_000)
    if shutil.disk_usage(folder).free < required:
        raise OSError(f'Insufficient disk space: allow at least {required/1e6:.0f} MB free.')
    destination = reserve_video(folder, data['source'])
    work = process = encoding = None
    try:
        work = Path(tempfile.mkdtemp(prefix='.beat-render-', dir=folder))
        output, log = work/'render.mp4', work/'ffmpeg.log'
        report = dict(quantisation_report(data, fps), mode=mode, width=size[0], height=size[1])
        (work/'timing.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        renderer = Overlay(data, size, fps)
        w, h = size
        command = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                   '-nostats', '-progress', 'pipe:1', '-f', 'rawvideo', '-pixel_format', 'rgb24',
                   '-video_size', f'{w}x{h}', '-framerate', str(fps), '-i', 'pipe:0',
                   '-i', str(wav.resolve()), '-map', '0:v:0', '-map', '1:a:0',
                   '-t', str(duration), '-r', str(fps), '-c:v', 'libx264',
                   '-preset', 'ultrafast' if mode == 'preview' else 'veryfast',
                   '-crf', '23' if mode == 'preview' else '20', '-pix_fmt', 'yuv420p',
                   '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', str(output)]
        total = math.ceil(duration*fps)
        progress(70, f'ECG prepared: {w}x{h} at {fps} FPS, {total} frames. Streaming directly to FFmpeg.')
        with log.open('wb') as errors:
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors)
            encoding = EncodingProgress(process.stdout)
            last_percent = 70
            try:
                for n in range(total):
                    process.stdin.write(renderer.frame(n).tobytes())
                    percent = 70 + int(24*(n+1)/total)
                    if percent > last_percent:
                        progress(percent, f'Rendered {n+1}/{total} frames; FFmpeg: {encoding.frames} frames, mux time {encoding.seconds:.2f}s.')
                        last_percent = percent
            finally:
                process.stdin.close()
            code = process.wait(timeout=max(120, duration*4))
            encoding.join()
        if code:
            raise RuntimeError('FFmpeg encoding failed.')
        progress(94, f'FFmpeg encoding completed: {encoding.frames} frames / {encoding.seconds:.3f}s reported.')
        progress(95, 'Verifying JSON, codecs, frame rate, duration and full audio/video decode...')
        validate_mp4(output, duration, tools, size, fps)
        if load_features(json_path) != data:
            raise ValueError('Feature JSON changed during rendering.')
        os.replace(output, destination)  # Replaces only this invocation's exclusive placeholder.
        probe(destination, ffprobe)
        try:
            shutil.rmtree(work)
        except OSError:
            progress(96, f'Outputs validated; temporary files could not be removed: {work}')
        return destination, report
    except BaseException as error:
        if process and process.poll() is None:
            process.kill()
            process.wait()
        if encoding:
            encoding.join()
        destination.unlink(missing_ok=True)
        if not isinstance(error, Exception):
            raise
        detail = ''
        if work and (work/'ffmpeg.log').is_file():
            detail = (work/'ffmpeg.log').read_text(encoding='utf-8', errors='replace')[-2000:].strip()
        diagnostics = f' Temporary files retained at: {work}' if work else ''
        raise RuntimeError(f'Video export failed: {error}. {detail}{diagnostics}') from error
