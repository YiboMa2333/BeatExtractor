"""Causal, sample-indexed DSP. No offline pass, device calls, GUI or visual engines."""
from collections import deque
import math
import numpy as np


class StreamingFeatures:
    def __init__(self, sample_rate, hop=512, fft_size=2048):
        if hop < 64 or fft_size < hop or fft_size & (fft_size-1):
            raise ValueError('Use hop >=64 and a power-of-two FFT size >= hop.')
        self.sr, self.hop, self.size = sample_rate, hop, fft_size
        self.window = np.hanning(fft_size)
        self.freq = np.fft.rfftfreq(fft_size, 1/sample_rate)
        self.samples = np.zeros(fft_size)
        self.previous_spectrum = np.zeros(fft_size//2+1)
        self.history = deque(maxlen=64)
        self.onsets = deque(maxlen=16)
        self.previous = None
        self.left_flux = 0.0
        self.last_onset = -1.0
        self.expected_sample = 0
        self.bpm = self.period = None
        self.confidence = 0.0

    @staticmethod
    def settings():
        return dict(algorithm='causal.normalised_spectral_flux.v1', thresholdFloor=.05,
                    thresholdMadMultiplier=3, historyHops=64, refractorySeconds=.12,
                    tempoAlgorithm='regular_onset_interval_regression.v1',
                    tempoMinIntervals=5, tempoRangeBpm=[60, 200],
                    tempoMaxRelativeIntervalDeviation=.10,
                    beatMeaning='estimated beat on a newly confirmed onset only; not every amplitude peak',
                    onsetTimestamp='centre of previous trailing FFT window; confirmed with one-hop lookahead')

    def process(self, pcm, start_sample):
        pcm = np.asarray(pcm)
        if pcm.ndim == 2:
            mono = pcm.mean(axis=1)
        else:
            mono = pcm
        if not len(mono) or len(mono) > self.hop or not np.all(np.isfinite(mono)):
            raise ValueError('Invalid live PCM block.')
        events = []
        if start_sample != self.expected_sample:
            events.append(dict(type='analysisGap', timeSeconds=start_sample/self.sr,
                               missingSamples=max(0, start_sample-self.expected_sample)))
            self.samples.fill(0)
            self.previous_spectrum.fill(0)
            self.history.clear()
            self.onsets.clear()
            self.previous = None
            self.last_onset = -1.0
            self.bpm = self.period = None
            self.confidence = self.left_flux = 0.0
        end = start_sample+len(mono)
        self.expected_sample = end
        self.samples = np.roll(self.samples, -len(mono))
        self.samples[-len(mono):] = mono
        rms = float(np.sqrt(np.mean(self.samples**2)))
        peak = float(np.max(np.abs(self.samples)))
        magnitude = abs(np.fft.rfft(self.samples*self.window))
        total = float(magnitude.sum())
        centroid = float(np.sum(magnitude*self.freq)/total) if total > 1e-12 else None
        bandwidth = float(np.sqrt(np.sum(magnitude*(self.freq-centroid)**2)/total)) if centroid is not None else None
        flux = float(np.maximum(0, magnitude-self.previous_spectrum).sum()
                     / max(1e-12, float(magnitude.sum()+self.previous_spectrum.sum())))
        power = magnitude**2/(self.size*float(np.sum(self.window**2)))
        power[1:-1] *= 2
        baseline = np.asarray(self.history)
        median = float(np.median(baseline)) if len(baseline) else 0.0
        mad = float(np.median(abs(baseline-median))) if len(baseline) else 0.0
        threshold = max(.05, median+3*mad)
        if self.previous:
            prev_flux, prev_end, prev_rms = self.previous
            event_time = max(0, (prev_end-self.size//2)/self.sr)
            if (prev_flux > self.left_flux and prev_flux >= flux and prev_flux > threshold
                    and prev_rms > 1e-6 and event_time-self.last_onset >= .12):
                self.last_onset = event_time
                self.onsets.append(event_time)
                onset = dict(type='onset', timeSeconds=event_time, strength=prev_flux,
                             detectedAtSeconds=end/self.sr)
                events.append(onset)
                self._estimate_tempo()
                if self.bpm is not None:
                    events.append(dict(type='estimatedBeat', timeSeconds=event_time,
                                       strength=prev_flux, detectedAtSeconds=end/self.sr,
                                       bpm=self.bpm, confidence=self.confidence))
            self.left_flux = prev_flux
        if self.onsets and end/self.sr-self.onsets[-1] > max(2.0, (self.period or 0)*2.5):
            self.bpm, self.confidence = None, 0.0
        self.previous = (flux, end, rms)
        self.previous_spectrum = magnitude
        self.history.append(flux)
        row = dict(timeSeconds=end/self.sr, sampleIndex=end, rms=rms, peakAmplitude=peak,
                   rmsDbfs=20*math.log10(rms) if rms > 0 else None,
                   spectralCentroidHz=centroid, spectralBandwidthHz=bandwidth,
                   bassEnergy=float(power[self.freq < 250].sum()),
                   midrangeEnergy=float(power[(self.freq >= 250) & (self.freq < 4000)].sum()),
                   trebleEnergy=float(power[self.freq >= 4000].sum()),
                   onsetStrength=flux, estimatedBpm=self.bpm, tempoConfidence=self.confidence)
        return row, events, magnitude/self.size

    def _estimate_tempo(self):
        self.bpm, self.confidence = None, 0.0
        if len(self.onsets) < 6:
            return
        times = np.asarray(list(self.onsets)[-9:])
        intervals = np.diff(times)
        median = float(np.median(intervals))
        if not .3 <= median <= 1 or np.max(abs(intervals-median))/median > .10:
            return
        # Regression reduces hop-grid jitter; no hardcoded tempo or event timestamps.
        x = np.arange(len(times))
        period = float(np.sum((x-x.mean())*(times-times.mean()))/np.sum((x-x.mean())**2))
        confidence = max(0, 1-float(np.mean(abs(intervals-period)))/period*5)
        if confidence >= .75:
            self.period, self.bpm, self.confidence = period, 60/period, confidence
