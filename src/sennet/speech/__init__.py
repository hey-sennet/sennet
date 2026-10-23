"""Local speech: voice activity, speech to text and text to speech.

Nothing here knows about sockets or sessions; the voice package builds on it.
"""
from .base import RATE, Recognizer, Synthesizer, TurnModel, VoiceActivity, VoiceActivityModel, Word
from .service import Speech

__all__ = ["RATE", "Recognizer", "Speech", "Synthesizer", "TurnModel", "VoiceActivity", "VoiceActivityModel",
           "Word"]
