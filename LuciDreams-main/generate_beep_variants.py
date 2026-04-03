#!/usr/bin/env python3
"""
Generate beep_v0.wav ... beep_v100.wav and sync to app sound folders.

By default this script reads a reference beep from cap-app/www/sounds/beep.wav.
If missing, it synthesizes a short sine beep.
"""

from __future__ import annotations

import argparse
import math
import shutil
import wave
from array import array
from pathlib import Path


ROOT = Path(__file__).resolve().parent
TARGET_DIRS = [
    ROOT / "static" / "sounds",
    ROOT / "cap-app" / "www" / "sounds",
    ROOT / "cap-app" / "ios" / "App" / "App" / "sounds",
]
DEFAULT_REFERENCE = ROOT / "cap-app" / "www" / "sounds" / "beep.wav"


def read_mono_16bit_wav(path: Path) -> tuple[int, array]:
    with wave.open(str(path), "rb") as wf:
        nchannels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        framerate = wf.getframerate()
        nframes = wf.getnframes()
        raw = wf.readframes(nframes)
    if sampwidth != 2:
        raise ValueError(f"Expected 16-bit WAV at {path}, got sample width {sampwidth}")
    data = array("h")
    data.frombytes(raw)
    if nchannels == 2:
        mono = array("h")
        for i in range(0, len(data), 2):
            mono.append(int((int(data[i]) + int(data[i + 1])) / 2))
        data = mono
    elif nchannels != 1:
        raise ValueError(f"Unsupported channel count {nchannels} in {path}")
    return framerate, data


def synthesize_reference(sample_rate: int = 44100, duration_sec: float = 0.2, freq_hz: float = 1000.0) -> tuple[int, array]:
    total = int(sample_rate * duration_sec)
    fade_len = max(1, int(0.01 * sample_rate))
    out = array("h")
    for i in range(total):
        t = i / sample_rate
        amp = math.sin(2.0 * math.pi * freq_hz * t)
        env = 1.0
        if i < fade_len:
            env = i / fade_len
        elif i > (total - fade_len):
            env = max(0.0, (total - i) / fade_len)
        s = int(32767 * 0.8 * env * amp)
        out.append(max(-32768, min(32767, s)))
    return sample_rate, out


def scale_samples(src: array, gain: float) -> array:
    dst = array("h")
    for s in src:
        v = int(s * gain)
        dst.append(max(-32768, min(32767, v)))
    return dst


def write_mono_16bit_wav(path: Path, sample_rate: int, samples: array) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(samples.tobytes())


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate beep_v0..v100 WAV files in all app sound folders.")
    parser.add_argument("--reference", default=str(DEFAULT_REFERENCE), help="Reference WAV file path.")
    parser.add_argument("--skip-beep-alias", action="store_true", help="Do not create/refresh beep.wav alias.")
    args = parser.parse_args()

    ref_path = Path(args.reference)
    if ref_path.exists():
        sample_rate, ref_samples = read_mono_16bit_wav(ref_path)
        print(f"Using reference WAV: {ref_path}")
    else:
        sample_rate, ref_samples = synthesize_reference()
        print(f"Reference not found at {ref_path}; using synthesized beep.")

    for target_dir in TARGET_DIRS:
        target_dir.mkdir(parents=True, exist_ok=True)
        for step in range(0, 101):
            gain = step / 100.0
            out_path = target_dir / f"beep_v{step}.wav"
            write_mono_16bit_wav(out_path, sample_rate, scale_samples(ref_samples, gain))
        if not args.skip_beep_alias:
            alias = target_dir / "beep.wav"
            src = target_dir / "beep_v100.wav"
            shutil.copyfile(src, alias)
        print(f"Generated variants in: {target_dir}")

    print("Done: beep_v0.wav ... beep_v100.wav in all target folders.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
