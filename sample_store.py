"""Persistence for user-recorded command samples."""

import wave
from pathlib import Path

import numpy as np


class SampleStore:
    def __init__(self, root: str | Path, sample_rate: int = 16000):
        self.root = Path(root)
        self.sample_rate = sample_rate

    def list_samples(self, command_id: str) -> list[Path]:
        command_dir = self.root / command_id
        if not command_dir.is_dir():
            return []
        return sorted(command_dir.glob("*.wav"))

    def count_samples(self, command_id: str) -> int:
        return len(self.list_samples(command_id))

    def rename_command(self, old_id: str, new_id: str):
        """Move the whole sample folder without changing individual recordings."""
        if old_id == new_id:
            return
        source = self.root / old_id
        destination = self.root / new_id
        if destination.exists():
            raise FileExistsError(f"录音目录已存在：{destination}")
        if source.exists():
            source.rename(destination)

    def save_sample(self, command_id: str, audio: np.ndarray) -> Path:
        command_dir = self.root / command_id
        command_dir.mkdir(parents=True, exist_ok=True)
        existing = self.list_samples(command_id)
        next_number = max((int(path.stem) for path in existing), default=0) + 1
        sample_path = command_dir / f"{next_number:03d}.wav"
        pcm = np.clip(np.asarray(audio), -1.0, 1.0)
        pcm = (pcm * 32767).astype("<i2")
        with wave.open(str(sample_path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(self.sample_rate)
            wav_file.writeframes(pcm.tobytes())
        return sample_path

    def load_sample(self, path: str | Path) -> np.ndarray:
        with wave.open(str(path), "rb") as wav_file:
            if wav_file.getnchannels() != 1 or wav_file.getframerate() != self.sample_rate:
                raise ValueError(f"样本格式必须是 {self.sample_rate} Hz 单声道 WAV：{path}")
            frames = wav_file.readframes(wav_file.getnframes())
        return np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0

    def delete_samples(self, command_id: str):
        command_dir = self.root / command_id
        for path in self.list_samples(command_id):
            path.unlink()
        if command_dir.is_dir():
            command_dir.rmdir()
