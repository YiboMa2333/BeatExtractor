"""Create an original varying-intensity 120 BPM WAV and real feature/ECG outputs."""
import argparse
from pathlib import Path
import sys

import numpy as np
import soundfile as sf

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT/'src'))
from cmd_app import main


def generate(folder, mode='preview'):
    folder.mkdir(parents=True, exist_ok=True)
    wav = folder/'synthetic_features_120bpm.wav'
    if wav.exists():
        raise FileExistsError(f'{wav} already exists; choose a fresh --output-dir.')
    sr = 22050
    samples = np.zeros(sr*8, dtype=np.float32)
    for i, start in enumerate(range(sr//2, len(samples), sr//2)):
        # Different measured intensities, not fabricated feature metadata.
        samples[start:start+220] = np.hanning(220)*(.25 if i % 3 == 0 else .8)
    sf.write(wav, samples, sr)
    return main([str(wav), '--output-dir', str(folder), '--mode', mode])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=PROJECT/'examples')
    parser.add_argument('--mode', choices=('preview', 'quality'), default='preview')
    args = parser.parse_args()
    raise SystemExit(generate(args.output_dir.resolve(), args.mode))
