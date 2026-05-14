"""Diarization model benchmark harness for CouncilScribe.

Runs pyannote 3.1 OSS, pyannote OSS + speaker-merge, pyannote.ai Precision-2,
and NVIDIA NeMo Sortformer against a fixed test set of council meetings on
Modal, then scores outputs for picking the best model for production.
"""
