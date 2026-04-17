#!/usr/bin/env python3
"""
Generate beep variants and sync to app sound folders.

Naming:
- Integer percent: selected steps only (e.g. beep_v0.wav, beep_v5.wav, ... beep_v100.wav)
- Half-percent in low range: beep_v0_5.wav ... beep_v9_5.wav

By default this script reads a reference beep from cap-app/www/sounds/beep.wav.
If missing, it synthesizes a short sine beep.
"""

from __future__ import annotations

import argparse
import math
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


def build_variant_levels() -> list[tuple[float, str]]:
    levels: list[tuple[float, str]] = []
    # 0..10% by 0.5%
    for i in range(0, 21):
        pct = i * 0.5
        if abs(pct - round(pct)) < 1e-9:
            suffix = str(int(round(pct)))
        else:
            suffix = f"{int(pct)}_5"
        levels.append((pct / 100.0, suffix))
    # 15..100% by 5%
    for pct_int in range(15, 101, 5):
        levels.append((pct_int / 100.0, str(pct_int)))
    return levels


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate beep variants WAV files in all app sound folders.")
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
        expected_variant_names = set()
        for gain, suffix in build_variant_levels():
            name = f"beep_v{suffix}.wav"
            expected_variant_names.add(name)
            out_path = target_dir / f"beep_v{suffix}.wav"
            write_mono_16bit_wav(out_path, sample_rate, scale_samples(ref_samples, gain))
        # Delete stale variants that are no longer used by app logic.
        for existing in target_dir.glob("beep_v*.wav"):
            if existing.name not in expected_variant_names:
                existing.unlink()
        if not args.skip_beep_alias:
            alias = target_dir / "beep.wav"
            src = target_dir / "beep_v100.wav"
            write_mono_16bit_wav(alias, sample_rate, scale_samples(ref_samples, 1.0))
        print(f"Generated variants in: {target_dir}")

    print("Done: generated beep integer variants + low-range half-step variants in all target folders.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
