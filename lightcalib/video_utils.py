from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess

import cv2
import numpy as np


@dataclass
class VideoMeta:
    fps: float
    frame_count: int
    duration: float
    width: int
    height: int

    def as_dict(self) -> dict:
        return {
            "fps": self.fps,
            "frame_count": self.frame_count,
            "duration": self.duration,
            "width": self.width,
            "height": self.height,
        }


@dataclass
class LightEvent:
    index: int
    frame: int
    time: float
    end_frame: int
    end_time: float
    peak: float
    score: float

    def as_dict(self) -> dict:
        return {
            "index": self.index,
            "frame": self.frame,
            "time": self.time,
            "end_frame": self.end_frame,
            "end_time": self.end_time,
            "peak": self.peak,
            "score": self.score,
        }


def locate_ffmpeg() -> str | None:
    for key in ("FFMPEG_PATH", "FFMPEG_BINARY"):
        value = os.environ.get(key)
        if value and Path(value).exists():
            return value

    found = shutil.which("ffmpeg")
    if found:
        return found

    candidates = [
        Path.cwd() / "ffmpeg" / "bin" / "ffmpeg.exe",
        Path("C:/Program Files/ffmpeg/bin/ffmpeg.exe"),
        Path("C:/Program Files (x86)/ffmpeg/bin/ffmpeg.exe"),
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages",
        Path("C:/ProgramData/chocolatey/bin/ffmpeg.exe"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
        if candidate.is_dir():
            matches = list(candidate.rglob("ffmpeg.exe"))
            if matches:
                return str(matches[0])
    return None


def video_metadata(path: Path) -> VideoMeta:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise ValueError(f"Could not open video {path.name}")
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        duration = frame_count / fps if fps > 0 and frame_count > 0 else 0.0
        return VideoMeta(fps=fps, frame_count=frame_count, duration=duration, width=width, height=height)
    finally:
        cap.release()


def save_video_frame(video_path: Path, output_path: Path, time_sec: float = 0.0) -> VideoMeta:
    meta = video_metadata(video_path)
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise ValueError(f"Could not open video {video_path.name}")
    try:
        if meta.fps > 0 and time_sec > 0:
            target_frame = max(0, min(meta.frame_count - 1, int(round(time_sec * meta.fps))))
            cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
        ok, frame = cap.read()
        if not ok and time_sec > 0:
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = cap.read()
        if not ok:
            raise ValueError(f"Could not read frame from {video_path.name}")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(output_path), frame)
    finally:
        cap.release()
    return meta


def save_first_frame(video_path: Path, output_path: Path) -> VideoMeta:
    return save_video_frame(video_path, output_path, 0.0)


def clamp_roi(roi: dict, width: int, height: int) -> tuple[int, int, int, int]:
    x = int(round(float(roi.get("x", 0))))
    y = int(round(float(roi.get("y", 0))))
    w = int(round(float(roi.get("w", 0))))
    h = int(round(float(roi.get("h", 0))))

    x = max(0, min(x, width - 1))
    y = max(0, min(y, height - 1))
    w = max(1, min(w, width - x))
    h = max(1, min(h, height - y))
    return x, y, w, h


def detect_light_events(
    video_path: Path,
    roi: dict,
    start_time_sec: float = 0.0,
    scan_fps: float = 12.0,
    min_separation_sec: float = 5.0,
    merge_gap_sec: float = 0.55,
    min_on_sec: float = 0.20,
) -> dict:
    meta = video_metadata(video_path)
    if meta.fps <= 0:
        raise ValueError(f"Video {video_path.name} has invalid FPS.")

    x, y, w, h = clamp_roi(roi, meta.width, meta.height)
    start_frame = max(0, int(round(max(0.0, start_time_sec) * meta.fps)))
    if meta.frame_count > 0:
        start_frame = min(start_frame, max(0, meta.frame_count - 1))
    stride = max(1, int(round(meta.fps / scan_fps))) if scan_fps and scan_fps > 0 else 1

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise ValueError(f"Could not open video {video_path.name}")

    scores: list[float] = []
    sample_frames: list[int] = []
    frame_number = start_frame
    try:
        cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
        while True:
            if (frame_number - start_frame) % stride == 0:
                ok, frame = cap.read()
                if not ok:
                    break
                scores.append(_measure_roi_score(frame, x, y, w, h))
                sample_frames.append(frame_number)
            else:
                ok = cap.grab()
                if not ok:
                    break
            frame_number += 1
    finally:
        cap.release()

    if not scores:
        return {
            "roi": {"x": x, "y": y, "w": w, "h": h},
            "events": [],
            "threshold": None,
            "baseline": None,
            "warnings": ["No frames were read from the video."],
        }

    values = np.array(scores, dtype=float)
    baseline = float(np.median(values))
    mad = float(np.median(np.abs(values - baseline)))
    robust_sigma = 1.4826 * mad
    p99 = float(np.percentile(values, 99))
    high_count = max(3, int(round(len(values) * 0.005)))
    high = float(np.mean(np.sort(values)[-high_count:]))
    scan_peak = float(np.max(values))
    contrast = max(0.0, high - baseline)
    threshold = baseline + 0.60 * contrast

    active = values >= threshold
    merge_gap_samples = max(1, int(round((merge_gap_sec * meta.fps) / stride)))
    runs = _runs_from_mask(active)
    runs = _merge_close_runs(runs, merge_gap_samples)

    events: list[LightEvent] = []
    min_on_frames = max(1, int(round(min_on_sec * meta.fps)))
    min_sep_frames = max(1, int(round(min_separation_sec * meta.fps)))
    refine_margin = max(stride * 3, int(round(merge_gap_sec * meta.fps)))
    last_event_frame: int | None = None

    for run_start, run_end in runs:
        coarse_start = sample_frames[run_start]
        coarse_end = sample_frames[run_end]
        refine_start = max(start_frame, coarse_start - refine_margin)
        refine_end = coarse_end + refine_margin
        if meta.frame_count > 0:
            refine_end = min(meta.frame_count - 1, refine_end)

        fine = _scan_roi_range(video_path, x, y, w, h, refine_start, refine_end)
        if not fine:
            continue
        fine_frames = [item[0] for item in fine]
        fine_values = np.array([item[1] for item in fine], dtype=float)
        fine_active = fine_values >= threshold
        fine_runs = _merge_close_runs(_runs_from_mask(fine_active), max(1, int(round(merge_gap_sec * meta.fps))))
        fine_runs = [run for run in fine_runs if run[1] - run[0] + 1 >= min_on_frames]
        if not fine_runs:
            continue

        fine_start_idx, fine_end_idx = fine_runs[0]
        event_frame = fine_frames[fine_start_idx]
        event_end_frame = fine_frames[fine_end_idx]
        if last_event_frame is not None and event_frame - last_event_frame < min_sep_frames:
            continue

        window = fine_values[fine_start_idx : fine_end_idx + 1]
        event_peak = float(np.max(window)) if len(window) else float(fine_values[fine_start_idx])
        score = (event_peak - baseline) / max(1.0, robust_sigma)
        events.append(
            LightEvent(
                index=len(events) + 1,
                frame=event_frame,
                time=float(event_frame / meta.fps),
                end_frame=event_end_frame,
                end_time=float(event_end_frame / meta.fps),
                peak=event_peak,
                score=float(score),
            )
        )
        last_event_frame = event_frame

    warnings: list[str] = []
    if contrast < 3:
        warnings.append("ROI light contrast is low; detection may be unreliable.")
    if not events:
        warnings.append("No light events were detected in this ROI.")

    return {
        "roi": {"x": x, "y": y, "w": w, "h": h},
        "events": [event.as_dict() for event in events],
        "threshold": float(threshold),
        "baseline": baseline,
        "p99": p99,
        "high": high,
        "peak": scan_peak,
        "score": "roi_top10_value_minus_surrounding_top10_value",
        "fps": meta.fps,
        "scan_start_time": float(start_frame / meta.fps),
        "scan_stride": int(stride),
        "frames_scanned": int(len(values)),
        "warnings": warnings,
    }


def detect_light_events_guided(
    video_path: Path,
    roi: dict,
    csv_flash_times: list[float],
    start_time_sec: float = 0.0,
    anchor_search_sec: float = 20.0,
    search_radius_sec: float = 2.0,
) -> dict:
    if not csv_flash_times:
        return detect_light_events(video_path, roi, start_time_sec=start_time_sec)

    meta = video_metadata(video_path)
    if meta.fps <= 0:
        raise ValueError(f"Video {video_path.name} has invalid FPS.")

    x, y, w, h = clamp_roi(roi, meta.width, meta.height)
    video_start = max(0.0, min(float(start_time_sec), max(0.0, meta.duration - 0.1)))
    first_window_end = min(meta.duration, video_start + anchor_search_sec)
    warnings: list[str] = []

    first_event = _detect_light_event_in_window(
        video_path,
        x,
        y,
        w,
        h,
        video_start,
        first_window_end,
        expected_time=None,
    )
    if first_event is None:
        return {
            "roi": {"x": x, "y": y, "w": w, "h": h},
            "events": [],
            "mode": "csv_timeline_guided",
            "anchor_search_window": {"start": video_start, "end": first_window_end},
            "warnings": [
                f"No anchor flash was detected between {video_start:.3f}s and {first_window_end:.3f}s."
            ],
        }

    anchor_csv_time = float(csv_flash_times[0])
    anchor_video_time = float(first_event["time"])
    first_event.update(
        {
            "index": 1,
            "expected_index": 0,
            "expected_csv_time": anchor_csv_time,
            "predicted_time": anchor_video_time,
            "prediction_error": 0.0,
            "role": "start",
        }
    )
    events = [first_event]

    for expected_index, csv_time in enumerate(csv_flash_times[1:], start=1):
        predicted_time = anchor_video_time + (float(csv_time) - anchor_csv_time)
        window_start = max(0.0, predicted_time - search_radius_sec)
        window_end = min(meta.duration, predicted_time + search_radius_sec)
        if window_start >= meta.duration:
            warnings.append(
                f"Predicted flash {expected_index + 1} at {predicted_time:.3f}s is outside the video."
            )
            continue

        event = _detect_light_event_in_window(
            video_path,
            x,
            y,
            w,
            h,
            window_start,
            window_end,
            expected_time=predicted_time,
        )
        if event is None:
            warnings.append(
                f"No flash found near predicted time {predicted_time:.3f}s "
                f"(window {window_start:.3f}-{window_end:.3f}s)."
            )
            continue

        event.update(
            {
                "index": len(events) + 1,
                "expected_index": expected_index,
                "expected_csv_time": float(csv_time),
                "predicted_time": predicted_time,
                "prediction_error": float(event["time"] - predicted_time),
                "role": "start" if expected_index % 2 == 0 else "finish",
            }
        )
        events.append(event)

    return {
        "roi": {"x": x, "y": y, "w": w, "h": h},
        "events": events,
        "mode": "csv_timeline_guided",
        "anchor_csv_time": anchor_csv_time,
        "anchor_video_time": anchor_video_time,
        "anchor_search_window": {"start": video_start, "end": first_window_end},
        "search_radius_sec": float(search_radius_sec),
        "fps": meta.fps,
        "warnings": warnings,
    }


def _detect_light_event_in_window(
    video_path: Path,
    x: int,
    y: int,
    w: int,
    h: int,
    start_time_sec: float,
    end_time_sec: float,
    expected_time: float | None,
) -> dict | None:
    meta = video_metadata(video_path)
    start_frame = max(0, int(round(start_time_sec * meta.fps)))
    end_frame = min(max(0, meta.frame_count - 1), int(round(end_time_sec * meta.fps)))
    if end_frame <= start_frame:
        return None

    samples = _scan_roi_range(video_path, x, y, w, h, start_frame, end_frame)
    if len(samples) < 3:
        return None

    frames = [item[0] for item in samples]
    values = np.array([item[1] for item in samples], dtype=float)
    baseline = float(np.percentile(values, 35))
    high_count = max(3, int(round(len(values) * 0.05)))
    high = float(np.mean(np.sort(values)[-high_count:]))
    peak = float(np.max(values))
    contrast = high - baseline
    if contrast < 2.0:
        return None

    threshold = baseline + 0.45 * contrast
    active = values >= threshold
    merge_gap_frames = max(1, int(round(0.20 * meta.fps)))
    min_on_frames = max(1, int(round(0.05 * meta.fps)))
    runs = _merge_close_runs(_runs_from_mask(active), merge_gap_frames)
    runs = [run for run in runs if run[1] - run[0] + 1 >= min_on_frames]
    if not runs:
        return None

    def run_sort_key(run: tuple[int, int]) -> tuple[float, float]:
        run_values = values[run[0] : run[1] + 1]
        run_peak = float(np.max(run_values))
        midpoint_time = ((frames[run[0]] + frames[run[1]]) / 2.0) / meta.fps
        distance = abs(midpoint_time - expected_time) if expected_time is not None else midpoint_time
        return (-run_peak, distance)

    best_start, best_end = sorted(runs, key=run_sort_key)[0]
    run_values = values[best_start : best_end + 1]
    event_peak = float(np.max(run_values))
    event_frame = int(frames[best_start])
    event_end_frame = int(frames[best_end])
    score = event_peak - baseline

    return {
        "frame": event_frame,
        "time": float(event_frame / meta.fps),
        "end_frame": event_end_frame,
        "end_time": float(event_end_frame / meta.fps),
        "peak": event_peak,
        "score": float(score),
        "baseline": baseline,
        "high": high,
        "threshold": float(threshold),
        "window_start": float(start_time_sec),
        "window_end": float(end_time_sec),
    }


def _measure_roi_score(frame: np.ndarray, x: int, y: int, w: int, h: int) -> float:
    crop = frame[y : y + h, x : x + w]
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    value = hsv[:, :, 2].astype(np.float32)
    roi_score = _top_fraction_mean(value)

    frame_h, frame_w = frame.shape[:2]
    margin = max(12, int(round(max(w, h) * 0.7)))
    x0 = max(0, x - margin)
    y0 = max(0, y - margin)
    x1 = min(frame_w, x + w + margin)
    y1 = min(frame_h, y + h + margin)
    patch = frame[y0:y1, x0:x1]
    if patch.size == 0:
        return roi_score

    hsv_patch = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
    patch_value = hsv_patch[:, :, 2].astype(np.float32)
    mask = np.ones(patch_value.shape, dtype=bool)
    mask[y - y0 : y - y0 + h, x - x0 : x - x0 + w] = False
    surrounding = patch_value[mask]
    if surrounding.size < max(20, value.size // 4):
        return roi_score

    return roi_score - _top_fraction_mean(surrounding)


def _top_fraction_mean(values: np.ndarray, fraction: float = 0.10) -> float:
    flat = values.reshape(-1)
    if flat.size == 0:
        return 0.0
    count = max(1, int(round(flat.size * fraction)))
    return float(np.mean(np.partition(flat, flat.size - count)[-count:]))


def _scan_roi_range(video_path: Path, x: int, y: int, w: int, h: int, start_frame: int, end_frame: int) -> list[tuple[int, float]]:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise ValueError(f"Could not open video {video_path.name}")

    scores: list[tuple[int, float]] = []
    try:
        cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
        frame_number = start_frame
        while frame_number <= end_frame:
            ok, frame = cap.read()
            if not ok:
                break
            scores.append((frame_number, _measure_roi_score(frame, x, y, w, h)))
            frame_number += 1
    finally:
        cap.release()
    return scores


def _runs_from_mask(mask: np.ndarray) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for idx, value in enumerate(mask):
        if value and start is None:
            start = idx
        elif not value and start is not None:
            runs.append((start, idx - 1))
            start = None
    if start is not None:
        runs.append((start, len(mask) - 1))
    return runs


def _merge_close_runs(runs: list[tuple[int, int]], max_gap_frames: int) -> list[tuple[int, int]]:
    if not runs:
        return []
    merged = [runs[0]]
    for start, end in runs[1:]:
        prev_start, prev_end = merged[-1]
        if start - prev_end - 1 <= max_gap_frames:
            merged[-1] = (prev_start, end)
        else:
            merged.append((start, end))
    return merged


def export_video_clip(
    ffmpeg: str,
    input_path: Path,
    output_path: Path,
    start_time: float,
    duration: float,
    crf: int = 18,
    preset: str = "veryfast",
    mute: bool = False,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg,
        "-y",
        "-ss",
        f"{max(0.0, start_time):.6f}",
        "-i",
        str(input_path),
        "-t",
        f"{max(0.0, duration):.6f}",
        "-c:v",
        "libx264",
        "-preset",
        preset,
        "-crf",
        str(crf),
    ]
    if mute:
        command.append("-an")
    else:
        command.extend(["-c:a", "aac"])
    command.extend(["-movflags", "+faststart", str(output_path)])
    subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def create_preview_clip(ffmpeg: str, input_path: Path, output_path: Path, start_time: float, duration: float) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg,
        "-y",
        "-ss",
        f"{max(0.0, start_time):.6f}",
        "-i",
        str(input_path),
        "-t",
        f"{max(0.1, duration):.6f}",
        "-vf",
        "scale='min(640,iw)':-2",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-crf",
        "28",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
