"""Direct comparison between captured speech and recorded command samples."""

from audio_features import dtw_distance, extract_mfcc, subsequence_matches


FRAME_SECONDS = 0.01


class SimilarityRecognizer:
    def __init__(self, commands: list[dict], sample_store, match_threshold: float):
        self.commands = commands
        self.sample_store = sample_store
        self.match_threshold = match_threshold
        self.references = {}
        self.extractor = extract_mfcc

    def load_references(self) -> int:
        """Cache original recording templates with the same MFCC used for live audio."""
        self.references = {}
        for command in self.commands:
            command_id = command["id"]
            features = [
                self.extractor(self.sample_store.load_sample(path))
                for path in self.sample_store.list_samples(command_id)
            ]
            if features:
                self.references[command_id] = features
        return sum(len(items) for items in self.references.values())

    def match(self, audio) -> dict:
        query = self.extractor(audio)
        scores = {}
        for command_id, references in self.references.items():
            scores[command_id] = min(dtw_distance(query, reference) for reference in references)

        best_id = min(scores, key=scores.get)
        command = next(item for item in self.commands if item["id"] == best_id)
        return {
            "accepted": scores[best_id] <= self.match_threshold,
            "command": command,
            "score": scores[best_id],
            "scores": scores,
        }

    def match_many(self, audio) -> list[dict]:
        """Find non-overlapping command occurrences within one speech segment."""
        query = self.extractor(audio)
        candidates = []
        scores = {}
        for command in self.commands:
            command_id = command["id"]
            if command_id not in self.references:
                continue
            scores[command_id] = float("inf")
            for reference in self.references[command_id]:
                for score, start, end in subsequence_matches(query, reference):
                    scores[command_id] = min(scores[command_id], score)
                    if score <= self.match_threshold:
                        candidates.append((score, start, end, command))
        selected = []
        # Multiple templates/endpoints can describe the same spoken occurrence.
        # Keep the best one, without suppressing a later repeated occurrence.
        for candidate in sorted(candidates, key=lambda item: item[0]):
            _, start, end, _ = candidate
            if any(start < other[2] and end > other[1] for other in selected):
                continue
            selected.append(candidate)
        if not selected:
            best_id = min(scores, key=scores.get)
            return [{
                "accepted": False,
                "command": next(item for item in self.commands if item["id"] == best_id),
                "score": scores[best_id],
                "scores": scores,
            }]
        return [
            {
                "accepted": True,
                "command": command,
                "score": score,
                "scores": dict(scores),
                "start_seconds": start * FRAME_SECONDS,
                "end_seconds": end * FRAME_SECONDS,
            }
            for score, start, end, command in sorted(selected, key=lambda item: item[1])
        ]
