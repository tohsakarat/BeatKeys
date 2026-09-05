"""MFCC feature extraction and Dynamic Time Warping distance."""

import numpy as np


def extract_mfcc(
    audio: np.ndarray,
    sample_rate: int = 16000,
    coefficient_count: int = 13,
    mel_filter_count: int = 26,
) -> np.ndarray:
    """Return per-coefficient normalized MFCC frames for one utterance."""
    samples = np.asarray(audio, dtype=np.float32).reshape(-1)
    emphasized = np.append(samples[0], samples[1:] - 0.97 * samples[:-1])
    frame_length = round(0.025 * sample_rate)
    frame_step = round(0.010 * sample_rate)
    frame_count = 1 + max(0, int(np.ceil((emphasized.size - frame_length) / frame_step)))
    padded_length = (frame_count - 1) * frame_step + frame_length
    padded = np.pad(emphasized, (0, max(0, padded_length - emphasized.size)))
    indices = np.arange(frame_length)[None, :] + frame_step * np.arange(frame_count)[:, None]
    frames = padded[indices] * np.hamming(frame_length)

    fft_size = 512
    power = np.abs(np.fft.rfft(frames, fft_size)) ** 2 / fft_size
    filter_bank = _mel_filter_bank(sample_rate, fft_size, mel_filter_count)
    log_energies = np.log(np.maximum(power @ filter_bank.T, np.finfo(float).eps))

    basis = np.cos(
        np.pi
        * np.arange(coefficient_count)[:, None]
        * (np.arange(mel_filter_count)[None, :] + 0.5)
        / mel_filter_count
    )
    mfcc = log_energies @ basis.T
    mean = mfcc.mean(axis=0, keepdims=True)
    std = mfcc.std(axis=0, keepdims=True)
    return ((mfcc - mean) / np.maximum(std, 1e-6)).astype(np.float32)


def dtw_distance(left: np.ndarray, right: np.ndarray) -> float:
    """Return average frame cost along the best non-linear alignment path."""
    left = np.asarray(left, dtype=np.float32)
    right = np.asarray(right, dtype=np.float32)
    frame_costs = np.sqrt(np.mean((left[:, None, :] - right[None, :, :]) ** 2, axis=2))
    previous_cost = np.full(right.shape[0] + 1, np.inf)
    previous_length = np.zeros(right.shape[0] + 1, dtype=np.int32)
    previous_cost[0] = 0.0

    for row in range(left.shape[0]):
        current_cost = np.full(right.shape[0] + 1, np.inf)
        current_length = np.zeros(right.shape[0] + 1, dtype=np.int32)
        for column in range(1, right.shape[0] + 1):
            candidates = (
                (previous_cost[column], previous_length[column]),
                (current_cost[column - 1], current_length[column - 1]),
                (previous_cost[column - 1], previous_length[column - 1]),
            )
            best_cost, best_length = min(candidates, key=lambda item: item[0])
            current_cost[column] = best_cost + float(frame_costs[row, column - 1])
            current_length[column] = best_length + 1
        previous_cost = current_cost
        previous_length = current_length

    return float(previous_cost[-1] / previous_length[-1])


def subsequence_matches(query: np.ndarray, reference: np.ndarray) -> list[tuple]:
    """Return local distance minima and their [start, end) query frame spans."""
    costs = np.sqrt(np.mean(
        (query[:, None, :] - reference[None, :, :]) ** 2, axis=2))
    width = len(reference)
    previous = np.full(width + 1, np.inf)
    lengths = np.zeros(width + 1, dtype=np.int32)
    starts = np.zeros(width + 1, dtype=np.int32)
    endpoints = []
    for row in range(len(query)):
        current = np.full(width + 1, np.inf)
        current[0] = 0
        previous[0] = 0
        current_lengths = np.zeros(width + 1, dtype=np.int32)
        current_starts = np.full(width + 1, row, dtype=np.int32)
        starts[0] = row
        for column in range(1, width + 1):
            choices = (
                (previous[column - 1], lengths[column - 1], starts[column - 1]),
                (previous[column], lengths[column], starts[column]),
                (current[column - 1], current_lengths[column - 1], current_starts[column - 1]),
            )
            cost, length, start = min(choices, key=lambda item: item[0])
            current[column] = cost + costs[row, column - 1]
            current_lengths[column] = length + 1
            current_starts[column] = start
        start = int(current_starts[-1])
        duration = row + 1 - start
        # Prevent a small fragment from masquerading as an entire recorded word.
        score = current[-1] / current_lengths[-1]
        if not max(8, width * 0.35) <= duration <= width * 3:
            score = np.inf
        endpoints.append((float(score), start, row + 1))
        previous, lengths, starts = current, current_lengths, current_starts
    return [
        item for index, item in enumerate(endpoints)
        if np.isfinite(item[0])
        and (index == 0 or item[0] <= endpoints[index - 1][0])
        and (index == len(endpoints) - 1 or item[0] < endpoints[index + 1][0])
    ]


def _mel_filter_bank(sample_rate: int, fft_size: int, filter_count: int) -> np.ndarray:
    low_mel = 0.0
    high_mel = 2595.0 * np.log10(1.0 + (sample_rate / 2) / 700.0)
    mel_points = np.linspace(low_mel, high_mel, filter_count + 2)
    hz_points = 700.0 * (10 ** (mel_points / 2595.0) - 1.0)
    bins = np.floor((fft_size + 1) * hz_points / sample_rate).astype(int)
    bank = np.zeros((filter_count, fft_size // 2 + 1), dtype=np.float32)

    for index in range(1, filter_count + 1):
        left, center, right = bins[index - 1 : index + 2]
        if center > left:
            bank[index - 1, left:center] = (np.arange(left, center) - left) / (center - left)
        if right > center:
            bank[index - 1, center:right] = (right - np.arange(center, right)) / (right - center)
    return bank
