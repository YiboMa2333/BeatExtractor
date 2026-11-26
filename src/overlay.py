"""Fast RGB ECG frames: static artwork cached once, no full-frame blur or shaders."""
from bisect import bisect_left, bisect_right
import json
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FPS = 24
MODES = {'preview': ((1280, 720), 24), 'quality': ((1920, 1080), 30)}


def number(value, minimum=None):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and (minimum is None or value >= minimum))


def load_features(path):
    with Path(path).open(encoding='utf-8') as file:
        data = json.load(file)
    try:
        beats = data['beats']
        duration = data['durationSeconds']
        if (not isinstance(data['source'], str) or not data['source']
                or not isinstance(data['analysisMethod'], str)
                or not number(duration, 0) or duration == 0
                or not number(data['estimatedBpm'], 0) or not isinstance(beats, list)
                or type(data['beatCount']) is not int or data['beatCount'] != len(beats)):
            raise ValueError('Invalid feature metadata.')
        previous = -1
        for i, beat in enumerate(beats, 1):
            t = beat['timeSeconds']
            if (type(beat['beatIndex']) is not int or beat['beatIndex'] != i
                    or not number(t, 0) or t <= previous or t > duration):
                raise ValueError('Beat timestamps must be ordered and within the song.')
            previous = t
            for key in ('amplitude', 'beatStrength', 'timbreCentroid', 'timbreBandwidth'):
                value = beat[key]
                if value is not None and not number(value, 0):
                    raise ValueError(f'Invalid {key}.')
            if beat['loudnessDb'] is not None and not number(beat['loudnessDb']):
                raise ValueError('Invalid loudnessDb.')
    except (KeyError, TypeError) as error:
        raise ValueError('Invalid feature JSON schema.') from error
    return data


def beat_frames(times, duration, fps=FPS):
    last = math.ceil(duration * fps) - 1
    return [min(last, int(math.floor(t * fps + .5))) for t in times]


def quantisation_report(data, fps=FPS):
    times = [beat['timeSeconds'] for beat in data['beats']]
    errors = [abs(n / fps - t) * 1000 for n, t in zip(beat_frames(times, data['durationSeconds'], fps), times)]
    return dict(fps=fps, beatCount=len(errors), maxErrorMs=max(errors, default=0.0),
                meanErrorMs=sum(errors) / len(errors) if errors else 0.0)


def font(size):
    for name in ('C:/Windows/Fonts/segoeui.ttf', 'DejaVuSans.ttf'):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default(size=size)


class Overlay:
    def __init__(self, data, size, fps=FPS):
        self.data, self.size, self.fps = data, size, fps
        w, h = size
        self.scale = w / 1280
        self.frames = beat_frames([b['timeSeconds'] for b in data['beats']], data['durationSeconds'], fps)
        self.times = [n / fps for n in self.frames]
        self.frame_set = set(self.frames)
        self.title_font, self.label_font = font(int(30*self.scale)), font(int(21*self.scale))
        strengths = [b['beatStrength'] or 0 for b in data['beats']]
        amplitudes = [b['amplitude'] or 0 for b in data['beats']]
        self.max_strength, self.max_amplitude = max(strengths, default=0), max(amplitudes, default=0)
        self.heights = [self.height_for(b) for b in data['beats']]
        self.base = Image.new('RGB', size, (7, 13, 23))
        draw = ImageDraw.Draw(self.base)
        draw.rectangle((0, int(h*.43), w, int(h*.85)), fill=(3, 9, 17))
        draw.text((int(32*self.scale), int(27*self.scale)), 'Super Happy Space 2.0',
                  font=self.title_font, fill=(225, 242, 245))
        name = data['source']
        while draw.textlength(name, font=self.label_font) > w - 64*self.scale and len(name) > 4:
            name = name[:-5] + '...'
        draw.text((int(32*self.scale), int(80*self.scale)), name, font=self.label_font, fill=(145, 175, 190))
        draw.text((int(32*self.scale), int(h*.94)), 'Musical beat visualisation - not a medical ECG',
                  font=font(int(15*self.scale)), fill=(90, 120, 135))
        self.left, self.right, self.y = int(32*self.scale), int(w-32*self.scale), int(h*.65)
        self.marker, self.speed = int(w*.6), w / 6
        draw.line((self.left, self.y, self.right, self.y), fill=(24, 115, 125), width=max(1, int(self.scale)))

    def height_for(self, beat):
        # Relative per-song scaling for DISPLAY ONLY. Exported descriptors stay raw.
        strength = ((beat['beatStrength'] or 0) / self.max_strength) if self.max_strength else None
        amplitude = ((beat['amplitude'] or 0) / self.max_amplitude) if self.max_amplitude else 0
        intensity = .8*strength + .2*amplitude if strength is not None else amplitude
        return self.scale * (12 + 78 * math.sqrt(max(0, min(1, intensity))))

    def state(self, n):
        return n / self.fps, bisect_right(self.frames, n), n in self.frame_set

    def crossing_emphasis(self, i, n):
        # A short symmetric envelope peaks exactly on the JSON-mapped crossing frame.
        return max(0.0, 1.0-abs(n-self.frames[i])/(self.fps*.12))**2

    def frame(self, n):
        image = self.base.copy()
        draw = ImageDraw.Draw(image)
        w, h = self.size
        s, y = self.scale, self.y
        t, count, pulse = self.state(n)
        bar_top, bar_bottom = int(h*.44), int(h*.75)
        draw.line((self.marker,bar_top,self.marker,bar_bottom), fill=(12, 35, 45), width=max(3, int(7*s)))
        draw.line((self.marker,bar_top,self.marker,bar_bottom), fill=(65, 135, 150), width=max(1, int(2*s)))
        lo, hi = bisect_left(self.times, t-4), bisect_right(self.times, t+3)
        for i in range(lo, hi):
            x = self.marker + (self.times[i] - t)*self.speed
            if self.left+14*s <= x <= self.right-14*s:
                emphasis = self.crossing_emphasis(i, n)
                height = self.heights[i]*(1+.45*emphasis)
                centroid = self.data['beats'][i]['timbreCentroid']
                hue = min(1, (centroid or 0)/6000)
                color = (int(45 + 35*hue), int(235 - 20*hue), 225)
                color = tuple(int(a+(b-a)*emphasis) for a, b in zip(color, (210, 255, 250)))
                points = [(x-14*s,y), (x-6*s,y+height*.13), (x,y-height),
                          (x+6*s,y+height*.25), (x+14*s,y)]
                # A dim wide stroke approximates glow without an expensive image blur.
                glow = tuple(int(a+(b-a)*emphasis) for a, b in zip((10, 55, 65), (35, 110, 120)))
                draw.line(points, fill=glow, width=max(3, int((7+3*emphasis)*s)))
                draw.line(points, fill=color, width=max(1, int((2+2*emphasis)*s)))
        minutes, seconds = divmod(t, 60)
        label = f'BPM {self.data["estimatedBpm"]:.1f}    {int(minutes):02d}:{seconds:04.1f} / {self.data["durationSeconds"]:.1f}s    Beat {count}/{len(self.frames)}'
        draw.text((self.left, int(h*.79)), label, font=self.label_font, fill=(210, 230, 235))
        bar_y = int(h*.89)
        position = self.left + (self.right-self.left)*min(1, t/self.data['durationSeconds'])
        draw.line((self.left,bar_y,self.right,bar_y), fill=(20, 55, 65), width=max(1, int(2*s)))
        draw.line((self.left,bar_y,position,bar_y), fill=(45, 200, 190), width=max(1, int(2*s)))
        draw.ellipse((position-4*s,bar_y-4*s,position+4*s,bar_y+4*s), fill=(140, 255, 240))
        return image
