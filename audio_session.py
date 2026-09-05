"""One cancellable audio session for monitoring, recording, or matching."""

import copy
import queue
import threading
import time
import numpy as np

from audio_listener import AudioListener
from keyboard_controller import KeyboardController
from similarity_recognizer import SimilarityRecognizer
from speech_segmenter import SpeechSegmenter


class AudioSession:
    def __init__(self, config, store, mode, command=None):
        self.config = copy.deepcopy(config)
        self.store = store
        self.mode = mode
        self.mode_label = "试匹配" if mode == "match" else "正式监听"
        self.command = copy.deepcopy(command)
        self.events = queue.Queue()
        self.cancel = threading.Event()
        self.record_finished = threading.Event()
        self.recognition_queue = queue.Queue()
        self.execution_queue = queue.Queue()
        self.workers = []
        self.listener = AudioListener(device_index=config.get("device_index"))
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.cancel.set()

    def finish_recording(self):
        self.record_finished.set()

    def _save_recording(self, audio):
        path = self.store.save_sample(self.command["id"], audio)
        self.events.put(("recorded", (path.name, len(audio) / 16000)))

    def _recognize(self, recognizer):
        try:
            while not self.cancel.is_set():
                item = self.recognition_queue.get()
                if item is None or self.cancel.is_set():
                    return
                utterance, queued_at = item
                self.events.put(("matching", None))
                started = time.monotonic()
                queued_ms = (started - queued_at) * 1000
                results = recognizer.match_many(utterance)
                elapsed_ms = (time.monotonic() - started) * 1000
                if self.cancel.is_set():
                    return
                count = sum(result["accepted"] for result in results)
                self.events.put(("log", f"{self.mode_label} · 声段 {len(utterance) / 16000:.2f} 秒 · "
                                 f"命中 {count} 次 · 排队 {queued_ms:.0f} ms · "
                                 f"计算 {elapsed_ms:.0f} ms"))
                for result in results:
                    if self.cancel.is_set():
                        return
                    result["elapsed_ms"] = elapsed_ms
                    result["execution"] = "仅匹配"
                    if result["accepted"]:
                        self.events.put(("log", f"命中 {result['command']['name']} · "
                                         f"{result['start_seconds']:.2f}–"
                                         f"{result['end_seconds']:.2f} 秒 · "
                                         f"距离 {result['score']:.3f}"))
                    if result["accepted"] and self.mode == "listen":
                        self.execution_queue.put(result)
                    else:
                        self.events.put(("result", result))
        except Exception as exc:
            self.events.put(("error", str(exc)))
            self.cancel.set()

    def _execute(self):
        # Rate limiting waits here; it must not discard recognized repetitions.
        controller = KeyboardController(0)
        next_execution = 0.0
        while not self.cancel.is_set():
            result = self.execution_queue.get()
            if result is None or self.cancel.is_set():
                return
            if self.cancel.wait(max(0, next_execution - time.monotonic())):
                return
            try:
                controller.trigger_hotkey(result["command"]["hotkey"])
                result["execution"] = "已发送"
            except Exception as exc:
                result["execution"] = f"发送失败：{exc}"
            next_execution = time.monotonic() + self.config["cooldown_seconds"]
            self.events.put(("result", result))

    def _run(self):
        try:
            config = self.config
            recognizer = None
            if self.mode in ("match", "listen"):
                self.events.put(("loading", "正在提取录音样本特征"))
                prepared_at = time.monotonic()
                recognizer = SimilarityRecognizer(
                    config["commands"], self.store,
                    config["match_threshold"],
                )
                if not recognizer.load_references():
                    raise ValueError("没有录音样本，请先录制至少一个命令。")
                self.events.put(("log", "MFCC + DTW · 本地匹配 · "
                                 f"匹配阈值 {recognizer.match_threshold:.2f}"))
                self.events.put(("log", f"{self.mode_label} · 样本准备 "
                                 f"{(time.monotonic() - prepared_at) * 1000:.0f} ms · "
                                 f"句末静音 {config['end_silence_seconds']:.2f} 秒"))
            segmenter = SpeechSegmenter(
                start_rms=config["speech_start_rms"],
                end_rms=config["speech_end_rms"],
                end_silence_seconds=config["end_silence_seconds"],
                min_utterance_seconds=config["min_utterance_seconds"],
                max_utterance_seconds=config["max_utterance_seconds"],
            )
            if self.cancel.is_set():
                return
            if recognizer is not None:
                self.workers.append(threading.Thread(
                    target=self._recognize, args=(recognizer,), daemon=True))
            if self.mode == "listen":
                self.workers.append(threading.Thread(target=self._execute, daemon=True))
            for worker in self.workers:
                worker.start()
            self.listener.start()
            self.events.put(("ready", self.listener.device_name))
            deadline = time.monotonic() + 12
            last_arrival = time.monotonic()
            losses = 0
            recording = []
            while not self.cancel.is_set():
                if self.mode == "record" and self.record_finished.is_set():
                    if not recording:
                        raise ValueError("尚未采集到音频。")
                    audio = np.concatenate(recording)
                    trimmed = segmenter.trim_recording(audio)
                    self.events.put(("log", f"首尾静音裁剪：{len(audio) / 16000:.2f} 秒"
                                     f" → {len(trimmed) / 16000:.2f} 秒"))
                    self._save_recording(trimmed)
                    return
                if self.mode == "record" and time.monotonic() > deadline:
                    raise ValueError("录制超时：12 秒内没有完整声段。请查看电平和起止阈值。")
                chunk = self.listener.get_chunk()
                if chunk is None:
                    if time.monotonic() - last_arrival > 2:
                        raise RuntimeError("音频流连续 2 秒未返回数据，请重新选择或连接设备。")
                    continue
                last_arrival = time.monotonic()
                if self.mode == "monitor":
                    continue
                if self.mode == "record":
                    recording.append(chunk.copy())
                telemetry = self.listener.snapshot()
                new_losses = telemetry["dropped"] + telemetry["overflows"]
                if new_losses != losses:
                    # Do not save or match a phrase containing missing frames.
                    losses = new_losses
                    recording.clear()
                    segmenter.reset()
                    self.listener.discard_pending()
                    self.events.put(("log", "检测到音频丢帧，已丢弃当前声段。"))
                    continue
                for utterance in segmenter.process_chunk(chunk):
                    if self.cancel.is_set():
                        return
                    if self.mode == "record":
                        self._save_recording(utterance)
                        return
                    # Timestamp starts at completed segmentation, not speech onset.
                    self.recognition_queue.put((utterance, time.monotonic()))
        except Exception as exc:
            self.events.put(("error", str(exc)))
        finally:
            # Stop cancels queued work; closed is emitted after workers exit.
            self.cancel.set()
            self.recognition_queue.put(None)
            self.execution_queue.put(None)
            try:
                self.listener.stop()
            except Exception as exc:
                self.events.put(("error", f"释放音频设备失败：{exc}"))
            for worker in self.workers:
                worker.join()
            self.events.put(("closed", None))
