"""Where open-mic turns start and end: a state machine over 32 ms voice-activity frames.

The numbers come from the M7 spikes (docs/m7-sessions.md, spikes/sessions):

- A turn starts after about 200 ms of speech, so clicks and coughs don't open one. While a
  reply is playing, 160 ms is enough: echo alone never made more than 53 ms.
- smart-turn runs once per pause, after 224 ms of silence, and ends the turn above 0.5.
  1.5 s of silence ends it regardless. Re-running it on the same pause pushes its score
  toward "finished" (HLD §6.2), so it never is.
- A turn that ended is reopened if speech resumes within 0.3 s: people add a second thought
  a moment after a finished sentence. That took cut-offs from 11% to 1% of turns.
- A turn that started over a reply ends as any other does: what is said over a reply is
  sent, so it is heard to the end.
- A turn the user holds the talk control for ends only when they let go.

Events, returned by `frame` and `checked`:

    start     a turn opened; it began `start_back` frames ago
    check     a pause is long enough to ask smart-turn; answer with `checked`
    end       the turn is over; run the final pass
    resume    speech came back within the grace: carry on with the same turn
    settled   the grace passed: the turn is final
"""
from __future__ import annotations

from collections import deque

FRAME_S = 0.032
SPEECH = 0.5             # Silero probability that counts as speech
START = (6, 8)           # frames of speech in the last few that open a turn
START_OVER_REPLY = (5, 6)
CHECK_FRAMES = 7         # 224 ms of silence before smart-turn is asked
THRESHOLD = 0.5
FALLBACK_FRAMES = 47     # 1.5 s
GRACE_FRAMES = 10        # 0.32 s
RESUME_FRAMES = 3        # speech within the grace that reopens the turn

AUTO, MANUAL = "auto", "manual"


class Endpointer:
    def __init__(self):
        self.state = "waiting"   # waiting | turn | ending
        self.mode = AUTO
        self.recent: deque[bool] = deque(maxlen=max(START[1], START_OVER_REPLY[1]))
        self.start_back = 0      # on "start": frames since the first speech that opened it
        self.silence = 0
        self.pause = 0           # counts pauses, so a late smart-turn answer can't end a later one
        self.waited = 0          # frames into the grace
        self.resumed = 0

    def frame(self, speech: bool, over_reply: bool = False) -> str | None:
        if self.state == "waiting":
            self.recent.append(speech)
            needed, window = START_OVER_REPLY if over_reply else START
            recent = list(self.recent)[-window:]
            if sum(recent) >= needed:
                self.start_back = len(recent) - recent.index(True)
                self._open(AUTO)
                return "start"
            return None
        if self.state == "ending":
            self.waited += 1
            self.resumed = self.resumed + 1 if speech else 0
            if self.resumed >= RESUME_FRAMES:
                self.state, self.silence, self.waited, self.resumed = "turn", 0, 0, 0
                return "resume"
            if self.waited >= GRACE_FRAMES and not self.resumed:
                return self._settle()
            return None
        if speech:
            self.silence = 0
            return None
        self.silence += 1
        if self.mode == MANUAL:
            return None
        if self.silence == CHECK_FRAMES:
            self.pause += 1
            return "check"
        if self.silence >= FALLBACK_FRAMES:
            return self._end()
        return None

    def checked(self, probability: float, pause: int) -> str | None:
        """smart-turn's answer for `pause`: "end" if the turn is over, else wait for more."""
        if self.state == "turn" and self.mode == AUTO and pause == self.pause and self.silence >= CHECK_FRAMES \
                and probability > THRESHOLD:
            return self._end()
        return None

    def hold(self):
        """The user pressed talk: the turn (open now, or reopened) ends only on `release`."""
        if self.state == "waiting":
            self._open(MANUAL)
        self.state, self.mode, self.silence, self.waited, self.resumed = "turn", MANUAL, 0, 0, 0

    def release(self):
        """The user let go: the turn is over, with no grace to wait out."""
        self.reset()

    def reset(self):
        self.state, self.mode, self.silence, self.waited, self.resumed = "waiting", AUTO, 0, 0, 0
        self.recent.clear()

    def _open(self, mode: str):
        self.state, self.mode, self.silence = "turn", mode, 0
        self.recent.clear()

    def _end(self) -> str:
        self.state, self.waited, self.resumed = "ending", 0, 0
        return "end"

    def _settle(self) -> str:
        self.reset()
        return "settled"
