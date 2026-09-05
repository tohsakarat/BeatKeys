"""Microphone capture and telemetry shared by every audio mode."""

from collections import deque
import queue
import threading
import time

import numpy as np
import sounddevice as sd


def list_input_devices():
    apis = sd.query_hostapis()
    return [
        {
            "index": index,
            "name": device["name"],
            "channels": device["max_input_channels"],
            "default_samplerate": device["default_samplerate"],
            "api": apis[device["hostapi"]]["name"],
        }
        for index, device in enumerate(sd.query_devices())
        if device["max_input_channels"] > 0
    ]


class AudioListener:
    def __init__(self, device_index=None, sample_rate=16000, block_size=1600):
        self.device_index = device_index
        self.sample_rate = sample_rate
        self.block_size = block_size
        self.audio_queue = queue.Queue(maxsize=30)
        self._stream = None
        self._lock = threading.Lock()
        self._wave = deque(maxlen=20)
        self._last_frame = 0.0
        self._rms = self._peak = 0.0
        self._dropped = self._overflows = 0
        self._status = ""
        self.device_name = ""

    def _audio_callback(self, indata, frames, time_info, status):
        audio = indata[:, 0].copy()
        # Preserve peaks in display bins so the waveform does not hide clipping.
        bins = np.array_split(audio, 80)
        envelope = np.array([(part.min(), part.max()) for part in bins])
        with self._lock:
            self._wave.append(envelope)
            self._last_frame = time.monotonic()
            self._rms = float(np.sqrt(np.mean(audio * audio)))
            self._peak = float(np.max(np.abs(audio)))
            if status:
                self._status = str(status)
            if status.input_overflow:
                self._overflows += 1
        try:
            self.audio_queue.put_nowait(audio)
        except queue.Full:
            with self._lock:
                self._dropped += 1

    def start(self):
        info = sd.query_devices(self.device_index, "input")
        self.device_name = info["name"]
        sd.check_input_settings(
            device=self.device_index, channels=1,
            dtype="float32", samplerate=self.sample_rate,
        )
        self._stream = sd.InputStream(
            device=self.device_index, channels=1, dtype="float32",
            samplerate=self.sample_rate, blocksize=self.block_size,
            callback=self._audio_callback,
        )
        self._stream.start()

    def stop(self):
        if self._stream is not None:
            try:
                self._stream.stop()
            finally:
                self._stream.close()
                self._stream = None

    def get_chunk(self, timeout=0.1):
        try:
            return self.audio_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def discard_pending(self):
        while True:
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                return

    def snapshot(self):
        with self._lock:
            return {
                "last_frame": self._last_frame,
                "rms": self._rms, "peak": self._peak,
                "dropped": self._dropped, "overflows": self._overflows,
                "driver_status": self._status,
                "wave": np.concatenate(self._wave) if self._wave else None,
                "device": self.device_name,
            }
