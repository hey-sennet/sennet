"""The approval chime: a low singing bowl tapped twice, the second softer.

Writes frontend/src/voice/approval-chime.mp3. Needs ffmpeg; run with
    uv run --no-project --with numpy --with scipy python design/approval-chime.py frontend/src/voice
"""
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
from scipy.signal import butter, fftconvolve, sosfilt

RATE = 44100
OUT = Path(sys.argv[1])
rng = np.random.default_rng(7)
# The chosen take was rendered third in a batch; skipping the first two rooms' noise keeps it identical.
rng.standard_normal(2 * int(RATE * 0.45))


def t(seconds):
    return np.arange(int(RATE * seconds)) / RATE


def env(x, attack, decay):
    """Rounded attack (raised cosine), exponential decay."""
    a = np.where(x < attack, 0.5 - 0.5 * np.cos(np.pi * np.minimum(x / attack, 1)), 1.0)
    return a * np.exp(-x / decay)


def lowpass(sound, cutoff, order=2):
    return sosfilt(butter(order, cutoff, fs=RATE, output="sos"), sound)


def place(track, sound, at, gain=1.0):
    start = int(at * RATE)
    end = min(len(track), start + len(sound))
    track[start:end] += gain * sound[:end - start]


def room(sound, mix=0.1, seconds=0.45):
    """A small, dark room: just enough to keep the bowl from sounding dry."""
    ir_t = t(seconds)
    ir = lowpass(rng.standard_normal(len(ir_t)) * np.exp(-ir_t / (seconds / 6)), 1800)
    wet = fftconvolve(sound, ir / np.sqrt(np.sum(ir ** 2)))
    dry = np.concatenate([sound, np.zeros(len(wet) - len(sound))])
    return (1 - mix) * dry / np.max(np.abs(dry)) + mix * wet / np.max(np.abs(wet))


# Loud, since phone and laptop speakers barely play the bowl's low notes, but not full scale:
# at -0.6 dB phones sounded rough.
def finish(sound, peak_db=-3.0):
    sound = sound / np.max(np.abs(sound)) * 10 ** (peak_db / 20)
    end = np.max(np.nonzero(np.abs(sound) > 10 ** ((peak_db - 41) / 20))) + 1  # trim the quiet tail
    sound = sound[:end]
    fade = int(0.3 * RATE)
    sound[-fade:] *= np.linspace(1, 0, fade) ** 2
    return sound


def bowl(f, decay=0.5, attack=0.02, beat=1.6, overtone=0.5, cutoff=1400, seconds=1.6):
    """A bowl at fundamental f: two close partials beating, plus the bowl's inharmonic overtones.
    The 2.71x overtone carries the pitch on small speakers, which can't play the fundamental."""
    x = t(seconds)
    tone = (np.sin(2 * np.pi * f * x) + 0.85 * np.sin(2 * np.pi * (f + beat) * x)
            + overtone * np.sin(2 * np.pi * f * 2.71 * x) * np.exp(-x / (decay * 0.7))
            + 0.15 * np.sin(2 * np.pi * f * 5.12 * x) * np.exp(-x / (decay * 0.3)))
    return lowpass(tone * env(x, attack, decay), cutoff)


# Two soft taps on the same low bowl, the second quieter: a gentle "knock knock".
def double_tap():
    track = np.zeros(int(RATE * 1.4))
    place(track, bowl(146.8, decay=0.3, seconds=1.0), 0.0)
    place(track, bowl(146.8, decay=0.4, seconds=1.2), 0.2, 0.7)
    return track


SOUNDS = {"approval-chime": double_tap}

OUT.mkdir(parents=True, exist_ok=True)
for name, make in SOUNDS.items():
    sound = finish(room(make()))
    wav = OUT / f"{name}.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((sound * 32767).astype(np.int16).tobytes())
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav), "-codec:a", "libmp3lame", "-b:a", "192k",
                    str(OUT / f"{name}.mp3")], check=True)
    wav.unlink()
    print(f"{name}.mp3  {len(sound) / RATE:.2f}s")
