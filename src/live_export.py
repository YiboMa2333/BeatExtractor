"""Replay recorded live events/features into an ECG video; no new beat detection."""
from collections import deque
import math
from pathlib import Path

from PIL import ImageDraw

from overlay import Overlay, beat_frames


def load_live_summary(path):
    import ijson
    fields = {'source', 'sourceType', 'sampleRate', 'durationSeconds', 'estimatedBpm', 'analysis.fftSize', 'analysis.hopSize'}
    result = {}
    with Path(path).open('rb') as file:
        for prefix, event, value in ijson.parse(file, use_float=True):
            if prefix in fields and event in ('string', 'number', 'null'):
                result[prefix] = value
    if (not isinstance(result.get('source'), str) or not result['source']
            or result.get('sourceType') not in ('windows.wasapi.loopback','windows.wasapi.microphone')
            or not isinstance(result.get('durationSeconds'), (int,float))
            or not math.isfinite(result.get('durationSeconds', 0)) or result.get('durationSeconds', 0) <= 0
            or result.get('sampleRate', 0) <= 0):
        raise ValueError('No positive-duration capture recording available for video.')
    stat = Path(path).stat()
    result['_fileSize'], result['_modifiedNs'] = stat.st_size, stat.st_mtime_ns
    return result


class LiveCaptureOverlay:
    """Stream continuous rows and detection-ordered musical events with a bounded visible window."""
    def __init__(self, path, size, fps, data):
        import ijson
        self.size, self.fps, self.data = size, fps, data
        self.files = [Path(path).open('rb'), Path(path).open('rb')]
        self.rows = iter(ijson.items(self.files[0], 'features.item', use_float=True))
        self.events = (e for e in ijson.items(self.files[1], 'events.item', use_float=True)
                       if e.get('type') in ('onset', 'estimatedBeat'))
        self.next_row, self.next_event = next(self.rows, None), next(self.events, None)
        self.visible = deque()
        self.current = None
        self.error_sum = self.max_error = 0.0
        self.event_count = 0
        title = 'Microphone audio' if data['sourceType']=='windows.wasapi.microphone' else 'Windows system audio'
        self.template = Overlay(dict(source=title, durationSeconds=data['durationSeconds'],
                                     estimatedBpm=0, beatCount=0, beats=[]), size, fps)
        # Detection timestamps are monotonic even if corrected musical positions are slightly reordered.
        # Look far enough ahead for the scrolling future area plus the configured trailing FFT.
        self.lookahead = 4.0+(data.get('analysis.fftSize',2048)+data.get('analysis.hopSize',512))/data['sampleRate']

    def frame(self, n):
        t = n/self.fps
        while self.next_row and self.next_row['timeSeconds'] <= t:
            self.current = self.next_row
            self.next_row = next(self.rows, None)
        while self.next_event and self.next_event.get('detectedAtSeconds', self.next_event['timeSeconds']) <= t+self.lookahead:
            event = self.next_event
            when = float(event['timeSeconds'])
            if not math.isfinite(when) or not 0 <= when <= self.data['durationSeconds']:
                raise ValueError('Invalid recorded musical event timestamp.')
            frame = beat_frames([when], self.data['durationSeconds'], self.fps)[0]
            event['_frame'] = frame
            self.visible.append(event)
            error = abs(frame/self.fps-when)*1000
            self.max_error = max(self.max_error, error)
            self.error_sum += error
            self.event_count += 1
            self.next_event = next(self.events, None)
        self.visible = deque(e for e in self.visible if e['_frame']/self.fps >= t-5)
        renderer = self.template
        image = renderer.base.copy()
        draw = ImageDraw.Draw(image)
        w, h = self.size
        s, y = renderer.scale, renderer.y
        draw.line((renderer.marker, int(h*.44), renderer.marker, int(h*.75)), fill=(65,135,150), width=max(1,int(2*s)))
        for event in sorted(self.visible, key=lambda e: e['_frame']):
            frame = event['_frame']
            x = renderer.marker+(frame/self.fps-t)*renderer.speed
            if not renderer.left+14*s <= x <= renderer.right-14*s:
                continue
            strength = float(event.get('strength', 0))
            if not math.isfinite(strength) or strength < 0:
                raise ValueError('Invalid recorded event strength.')
            emphasis = max(0, 1-abs(n-frame)/(self.fps*.12))**2
            beat = event['type'] == 'estimatedBeat'
            height = s*(20+75*min(1, strength*2))*(1 if beat else .5)*(1+.45*emphasis)
            base = (45,235,225) if beat else (235,180,65)
            color = tuple(int(a+(b-a)*emphasis) for a,b in zip(base, (215,255,245) if beat else (255,235,160)))
            points = [(x-14*s,y),(x-6*s,y+height*.13),(x,y-height),(x+6*s,y+height*.25),(x+14*s,y)]
            draw.line(points, fill=(15,55,65) if beat else (65,45,15), width=max(3,int((7+3*emphasis)*s)))
            draw.line(points, fill=color, width=max(1,int((2+2*emphasis)*s)))
        row = self.current or {}
        bpm = row.get('estimatedBpm')
        bpm_label = f'{bpm:.1f}' if bpm is not None else 'Estimating...'
        minutes, seconds = divmod(t, 60)
        draw.text((renderer.left,int(h*.79)), f'BPM {bpm_label}    {int(minutes):02d}:{seconds:04.1f} / {self.data["durationSeconds"]:.1f}s',
                  font=renderer.label_font, fill=(210,230,235))
        draw.text((renderer.left,int(h*.21)), 'Yellow: onsets    Cyan: estimated beats (after warm-up)',
                  font=renderer.label_font, fill=(145,175,190))
        bar_y = int(h*.89)
        position = renderer.left+(renderer.right-renderer.left)*min(1,t/self.data['durationSeconds'])
        draw.line((renderer.left,bar_y,renderer.right,bar_y),fill=(20,55,65),width=max(1,int(2*s)))
        draw.line((renderer.left,bar_y,position,bar_y),fill=(45,200,190),width=max(1,int(2*s)))
        return image

    def timing_report(self):
        return dict(fps=self.fps, eventCount=self.event_count, maxErrorMs=self.max_error,
                    meanErrorMs=self.error_sum/self.event_count if self.event_count else 0.0)

    def close(self):
        for file in self.files:
            file.close()


if __name__ == '__main__':
    import argparse
    from analyse import output_folder
    from video_export import export_video
    parser = argparse.ArgumentParser(description='Retry an ECG export from live JSON and retained capture audio.')
    parser.add_argument('json', type=Path)
    parser.add_argument('audio', type=Path)
    parser.add_argument('--mode', choices=('preview','quality'), default='preview')
    parser.add_argument('--output-dir', type=Path, default=output_folder())
    args = parser.parse_args()
    video, report = export_video(args.audio, args.json, args.output_dir, mode=args.mode,
                                source_format='live', progress=lambda p,m: print(f'[{p}%] {m}',flush=True))
    print(video)
