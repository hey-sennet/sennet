"""The wake chime: the approval chime's singing bowl, faded in rather than struck, played as the
wake word is heard so you know it's listening.

Writes frontend/src/voice/wake-chime.mp3. Needs ffmpeg; run with
    uv run --no-project --with numpy --with scipy python design/wake-chime.py frontend/src/voice
With --candidates, writes every candidate tried instead, and the approval chime to compare against.
"""
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
from scipy.signal import butter, fftconvolve, sosfilt

RATE = 44100
CANDIDATES = "--candidates" in sys.argv
OUT = Path(next(arg for arg in sys.argv[1:] if not arg.startswith("--")))
rng = np.random.default_rng(11)
if not CANDIDATES:
    # The chosen take was rendered twelfth in the candidates; skipping their rooms' noise keeps it identical.
    rng.standard_normal(11 * int(RATE * 0.45))


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
def finish(sound, peak_db=-3.0, fade_s=0.2):
    sound = sound / np.max(np.abs(sound)) * 10 ** (peak_db / 20)
    end = np.max(np.nonzero(np.abs(sound) > 10 ** ((peak_db - 41) / 20))) + 1  # trim the quiet tail
    sound = sound[:end]
    fade = int(fade_s * RATE)
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


def track(seconds):
    return np.zeros(int(RATE * seconds))


# The approval chime, for comparison: two soft taps on a low bowl, the second quieter.
def approval():
    out = track(1.4)
    place(out, bowl(146.8, decay=0.3, seconds=1.0), 0.0)
    place(out, bowl(146.8, decay=0.4, seconds=1.2), 0.2, 0.7)
    return out


# 1. One tap on the approval's bowl, an octave up: its cousin, but a single "yes?".
def single_tap():
    out = track(0.9)
    place(out, bowl(293.7, decay=0.25, cutoff=2400, seconds=0.9), 0.0)
    return out


# 2. Two quick taps rising a fifth: "I'm here", the opposite shape to the approval's knock.
def rising_fifth():
    out = track(0.9)
    place(out, bowl(196.0, decay=0.12, cutoff=2200, seconds=0.5), 0.0, 0.8)
    place(out, bowl(293.7, decay=0.25, cutoff=2600, seconds=0.8), 0.11)
    return out


# 3. Two quick taps rising a major third: brighter and friendlier than the fifth.
def rising_third():
    out = track(0.9)
    place(out, bowl(261.6, decay=0.12, cutoff=2600, seconds=0.5), 0.0, 0.8)
    place(out, bowl(329.6, decay=0.25, cutoff=3000, seconds=0.8), 0.1)
    return out


# 4. A small, high bowl: a short bright ping, least like the approval.
def ping():
    out = track(0.6)
    place(out, bowl(587.3, decay=0.12, beat=3.0, overtone=0.3, cutoff=4000, seconds=0.6), 0.0)
    return out


# 5. A soft swell: a bowl that fades in rather than being struck, like an intake of breath.
def swell():
    out = track(0.9)
    place(out, bowl(220.0, decay=0.22, attack=0.12, cutoff=2200, seconds=0.9), 0.0)
    place(out, bowl(330.0, decay=0.2, attack=0.12, overtone=0.3, cutoff=2600, seconds=0.9), 0.04, 0.5)
    return out


def swelled(f, at=0.0, gain=1.0, attack=0.12, decay=0.22, overtone=0.5, cutoff=2200, glide=0.0, seconds=0.9):
    """A bowl faded in rather than struck; glide bends its pitch up by that fraction as it swells."""
    if not glide:
        return bowl(f, decay=decay, attack=attack, overtone=overtone, cutoff=cutoff, seconds=seconds)
    x = t(seconds)
    bend = 1 + glide * np.minimum(x / attack, 1) ** 0.5
    phase = 2 * np.pi * np.cumsum(f * bend) / RATE
    tone = (np.sin(phase) + 0.85 * np.sin(phase * (f + 1.6) / f)
            + overtone * np.sin(phase * 2.71) * np.exp(-x / (decay * 0.7))
            + 0.15 * np.sin(phase * 5.12) * np.exp(-x / (decay * 0.3)))
    return lowpass(tone * env(x, attack, decay), cutoff)


def pair(low, high, gap=0.04, second=0.5, **kw):
    out = track(kw.get("seconds", 0.9) + gap)
    place(out, swelled(low, **kw), 0.0)
    place(out, swelled(high, **{**kw, "overtone": 0.3, "cutoff": kw.get("cutoff", 2200) + 400}), gap, second)
    return out


# Round 2, on 5-swell: the same fifth (220 and 330 Hz) faded in, varied one way at a time.
def swell_quick():  # 6. A shorter breath, so it's done before you start talking.
    return pair(220.0, 330.0, attack=0.07, decay=0.15, seconds=0.6)


def swell_high():  # 7. Up a fourth, brighter and lighter.
    return pair(293.7, 440.0)


def swell_third():  # 8. A major third instead of a fifth: warmer, sweeter.
    return pair(220.0, 277.2)


def swell_rising():  # 9. The upper bowl comes in later, so the swell climbs.
    return pair(220.0, 330.0, gap=0.12, second=0.8, seconds=0.8)


def swell_glide():  # 10. Both bowls bend up a touch as they swell: an "mm?".
    return pair(220.0, 330.0, glide=0.04)


def swell_single():  # 11. Just the lower bowl, the plainest.
    out = track(0.9)
    place(out, swelled(220.0), 0.0)
    return out


def swell_long():  # 12. A slower, deeper breath in.
    return pair(220.0, 330.0, attack=0.2, decay=0.28, seconds=1.1)


SOUNDS = {"wake-chime": swell_single} if not CANDIDATES else {
    "0-approval-for-comparison": approval,
    "1-single-tap": single_tap,
    "2-rising-fifth": rising_fifth,
    "3-rising-third": rising_third,
    "4-ping": ping,
    "5-swell": swell,
    "6-swell-quick": swell_quick,
    "7-swell-high": swell_high,
    "8-swell-third": swell_third,
    "9-swell-rising": swell_rising,
    "10-swell-glide": swell_glide,
    "11-swell-single": swell_single,
    "12-swell-long": swell_long,
}

OUT.mkdir(parents=True, exist_ok=True)
for name, make in SOUNDS.items():
    sound = finish(room(make()), fade_s=0.3 if name.startswith("0-") else 0.2)
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
