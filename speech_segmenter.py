"""Energy-based utterance segmentation for streamed microphone audio."""

from collections import deque

import numpy as np


class SpeechSegmenter:
    """Collect one phrase at a time using speech-start and silence thresholds."""

    def __init__(
        self,
        sample_rate: int = 16000,
        frame_ms: int = 20,
        start_rms: float = 0.025,
        end_rms: float = 0.015,
        pre_roll_seconds: float = 0.2,
        end_silence_seconds: float = 0.45,
        min_utterance_seconds: float = 0.35,
        max_utterance_seconds: float = 3.0,
    ):
        self.sample_rate = sample_rate
        self.frame_size = sample_rate * frame_ms // 1000
        self.start_rms = start_rms
        self.end_rms = end_rms
        self.pre_roll_frames = max(1, round(pre_roll_seconds * sample_rate / self.frame_size))
        self.end_silence_frames = max(1, round(end_silence_seconds * sample_rate / self.frame_size))
        self.min_samples = round(min_utterance_seconds * sample_rate)
        self.max_samples = round(max_utterance_seconds * sample_rate)
        self._pending = np.empty(0, dtype=np.float32)
        self._pre_roll = deque(maxlen=self.pre_roll_frames)
        self._active_frames = []
        self._silence_frames = 0

    def process_chunk(self, chunk: np.ndarray) -> list[np.ndarray]:
        """Consume a streamed chunk and return any completed utterances."""
        samples = np.asarray(chunk, dtype=np.float32).reshape(-1)
        self._pending = np.concatenate((self._pending, samples))
        utterances = []

        while self._pending.size >= self.frame_size:
            frame = self._pending[: self.frame_size]
            self._pending = self._pending[self.frame_size :]
            utterance = self._process_frame(frame)
            if utterance is not None:
                utterances.append(utterance)

        return utterances

    def reset(self):
        self._pending = np.empty(0, dtype=np.float32)
        self._pre_roll.clear()
        self._active_frames = []
        self._silence_frames = 0

    def trim_recording(self, audio: np.ndarray) -> np.ndarray:
        """Trim only the edges of a manually completed recording."""
        samples = np.asarray(audio, dtype=np.float32).reshape(-1)
        rms = np.array([
            np.sqrt(np.mean(samples[offset:offset + self.frame_size] ** 2))
            for offset in range(0, samples.size, self.frame_size)
        ])
        starts = np.flatnonzero(rms >= self.start_rms)
        if not starts.size:
            raise ValueError("未检测到达到起音阈值的声音，未保存录音。")
        first = int(starts[0])
        last = int(np.flatnonzero(rms >= self.end_rms)[-1])
        # Match automatic pre-roll, including the frame that crosses the threshold.
        start = max(0, first - self.pre_roll_frames + 1) * self.frame_size
        end = min(samples.size, (last + 1) * self.frame_size)
        return samples[start:end]

    def _process_frame(self, frame: np.ndarray):
        rms = float(np.sqrt(np.mean(frame * frame)))

        if not self._active_frames:
            self._pre_roll.append(frame)
            if rms < self.start_rms:
                return None
            # Pre-roll keeps the leading consonant that precedes the threshold crossing.
            self._active_frames = list(self._pre_roll)
            self._pre_roll.clear()
            self._silence_frames = 0
            return None

        self._active_frames.append(frame)
        self._silence_frames = self._silence_frames + 1 if rms < self.end_rms else 0
        sample_count = len(self._active_frames) * self.frame_size

        if self._silence_frames >= self.end_silence_frames or sample_count >= self.max_samples:
            utterance = np.concatenate(self._active_frames)
            trailing_samples = self._silence_frames * self.frame_size
            if trailing_samples:
                utterance = utterance[:-trailing_samples]
            self._active_frames = []
            self._silence_frames = 0
            self._pre_roll.clear()
            if utterance.size >= self.min_samples:
                return utterance.astype(np.float32, copy=False)

        return None
