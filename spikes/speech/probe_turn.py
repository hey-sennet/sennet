"""Smart-turn threshold sweep: first-check scores at true ends vs mid-sentence pauses.

A score above the threshold at a true end ends the turn ~200 ms after speech;
otherwise the turn waits for the max-silence fallback. Above the threshold at a
mid-sentence pause is a premature cut.
"""
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

from speech import RATE, Parakeet, SmartTurn

device = sys.argv[1] if len(sys.argv) > 1 else "cuda"
stt = Parakeet("cuda", None)
turn = SmartTurn(device)
tail = np.zeros(int(0.2 * RATE), dtype=np.float32)
ends, mids = [], []
for path in sorted(Path(".cache/librispeech").glob("*.wav"))[:50]:
    audio = sf.read(path, dtype="float32")[0]
    words = stt(audio)
    if len(words) < 6:
        continue
    for index in (len(words) // 3, len(words) // 2, 2 * len(words) // 3):
        cut = int((words[index - 1].end + words[index].start) / 2 * RATE)
        mids.append(turn(np.concatenate([audio[:cut], tail])))
    ends.append(turn(np.concatenate([audio[:int(words[-1].end * RATE)], tail])))
ends, mids = np.array(ends), np.array(mids)
print(f"{device}: {len(ends)} ends, {len(mids)} mid-sentence pauses")
for threshold in [0.5, 0.6, 0.7, 0.8, 0.9]:
    print(f"  threshold {threshold}: fast end {np.mean(ends > threshold):.0%}, "
          f"premature cut {np.mean(mids > threshold):.0%}")
