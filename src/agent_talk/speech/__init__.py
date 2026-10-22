"""Local speech: voice activity, speech to text and text to speech.

Nothing here knows about sockets or sessions; the voice package builds on it.
"""
from .base import RATE, Recognizer, Synthesizer, VoiceActivity, VoiceActivityModel, Word
from .service import Speech

__all__ = ["RATE", "Recognizer", "Speech", "Synthesizer", "VoiceActivity", "VoiceActivityModel", "Word"]
