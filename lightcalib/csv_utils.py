from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


@dataclass
class SensorData:
    path: Path
    raw: pd.DataFrame
    time_col: str
    value_col: str
    times: np.ndarray
    values: np.ndarray


@dataclass
class Pulse:
    index: int
    start_index: int
    end_index: int
    start_time: float
    end_time: float
    start_value: float
    peak_value: float

    def as_dict(self) -> dict:
        return {
            "index": self.index,
            "start_index": self.start_index,
            "end_index": self.end_index,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "start_value": self.start_value,
            "peak_value": self.peak_value,
        }


def natural_key(value: str) -> list[object]:
    parts = re.split(r"(\d+)", value.casefold())
    return [int(part) if part.isdigit() else part for part in parts]


def classify_csv(path: Path) -> str | None:
    name = path.name.casefold()
    if "force" in name:
        return "force"
    if "buzzer" in name:
        return "buzzer"
    return None


def csv_pair_key(path: Path) -> tuple[str, str] | None:
    stem = path.stem.casefold()
    match = re.search(r"^(?P<prefix>.*?)[._\-\s]*(?P<kind>force|buzzer)\s*(?P<num>\d+)?", stem)
    if not match:
        return None
    prefix = re.sub(r"[^a-z0-9]+", "", match.group("prefix"))
    num = match.group("num") or ""
    return prefix, num


def pair_csv_files(paths: Iterable[Path]) -> tuple[list[dict], list[str]]:
    warnings: list[str] = []
    by_kind: dict[str, list[Path]] = {"force": [], "buzzer": []}
    ignored: list[str] = []

    for path in paths:
        kind = classify_csv(path)
        if kind is None:
            ignored.append(path.name)
            continue
        by_kind[kind].append(path)

    if ignored:
        warnings.append("Ignored CSV files without force/buzzer in the filename: " + ", ".join(ignored))

    grouped: dict[tuple[str, str], dict[str, Path]] = {}
    leftovers: dict[str, list[Path]] = {"force": [], "buzzer": []}
    for kind, items in by_kind.items():
        for path in items:
            key = csv_pair_key(path)
            if key is None or key == ("", ""):
                leftovers[kind].append(path)
                continue
            grouped.setdefault(key, {})[kind] = path

    pairs: list[dict] = []
    used: set[Path] = set()
    for key in sorted(grouped, key=lambda item: natural_key("|".join(item))):
        group = grouped[key]
        if "force" in group and "buzzer" in group:
            pairs.append({"key": key, "force": group["force"], "buzzer": group["buzzer"]})
            used.add(group["force"])
            used.add(group["buzzer"])

    unmatched_force = [p for p in by_kind["force"] if p not in used]
    unmatched_buzzer = [p for p in by_kind["buzzer"] if p not in used]
    unmatched_force.sort(key=lambda p: natural_key(p.name))
    unmatched_buzzer.sort(key=lambda p: natural_key(p.name))

    fallback_count = min(len(unmatched_force), len(unmatched_buzzer))
    for idx in range(fallback_count):
        pairs.append({
            "key": ("fallback", str(idx + 1)),
            "force": unmatched_force[idx],
            "buzzer": unmatched_buzzer[idx],
        })

    if len(unmatched_force) != len(unmatched_buzzer):
        warnings.append(
            "Unmatched CSV counts after pairing: "
            f"{len(unmatched_force)} force and {len(unmatched_buzzer)} buzzer files."
        )

    pairs.sort(key=lambda item: natural_key(Path(item["buzzer"]).name))
    return pairs, warnings


def read_sensor_csv(path: Path) -> SensorData:
    candidates: list[tuple[int, pd.DataFrame, str, str, np.ndarray, np.ndarray]] = []
    read_errors: list[str] = []

    for header in (0, None):
        try:
            df = pd.read_csv(
                path,
                comment="#",
                sep=None,
                engine="python",
                header=header,
                encoding="utf-8-sig",
            )
        except Exception as exc:  # pragma: no cover - reported to user at runtime
            read_errors.append(str(exc))
            continue

        if df.shape[1] < 2:
            continue

        time_col = str(df.columns[0])
        value_col = str(df.columns[1])
        times = parse_timestamp_series(df.iloc[:, 0])
        values = pd.to_numeric(df.iloc[:, 1], errors="coerce").to_numpy(dtype=float)
        valid = np.isfinite(times) & np.isfinite(values)
        score = int(valid.sum())
        if score:
            clean = df.loc[valid].copy()
            candidates.append((score, clean, time_col, value_col, times[valid], values[valid]))

    if not candidates:
        detail = "; ".join(read_errors) if read_errors else "no parseable timestamp/value columns"
        raise ValueError(f"Could not read sensor CSV {path.name}: {detail}")

    candidates.sort(key=lambda item: item[0], reverse=True)
    _, raw, time_col, value_col, times, values = candidates[0]
    order = np.argsort(times, kind="stable")
    return SensorData(path=path, raw=raw.iloc[order].copy(), time_col=time_col, value_col=value_col, times=times[order], values=values[order])


def parse_timestamp_series(series: pd.Series) -> np.ndarray:
    return np.array([parse_timestamp(value) for value in series], dtype=float)


def parse_timestamp(value: object) -> float:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return np.nan
    if isinstance(value, (int, float, np.integer, np.floating)):
        return float(value)

    text = str(value).strip().strip('"')
    if not text:
        return np.nan
    text = text.replace(",", ".")

    try:
        return float(text)
    except ValueError:
        pass

    colon_parts = text.split(":")
    if len(colon_parts) in (2, 3) and all(part.strip() for part in colon_parts):
        try:
            seconds = float(colon_parts[-1])
            minutes = int(colon_parts[-2])
            hours = int(colon_parts[-3]) if len(colon_parts) == 3 else 0
            return hours * 3600 + minutes * 60 + seconds
        except ValueError:
            pass

    formats = (
        "%Y/%m/%d %H:%M:%S.%f",
        "%Y/%m/%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%d/%m/%Y %H:%M:%S.%f",
        "%d/%m/%Y %H:%M:%S",
    )
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).timestamp()
        except ValueError:
            continue

    parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        return np.nan
    return parsed.to_pydatetime().timestamp()


def detect_voltage_pulses(
    times: np.ndarray,
    values: np.ndarray,
    low: float = 0.002,
    high: float = 0.0025,
    min_separation_sec: float = 5.0,
) -> list[Pulse]:
    if len(times) != len(values):
        raise ValueError("times and values must have the same length")
    if len(times) == 0:
        return []
    if low >= high:
        raise ValueError("low voltage threshold must be below high threshold")

    pulses: list[Pulse] = []
    index = 0
    pulse_index = 1
    n_rows = len(values)

    while index < n_rows:
        while index < n_rows and not (low <= values[index] <= high):
            index += 1
        if index >= n_rows:
            break

        start_index = index
        start_time = float(times[start_index])
        end_index = start_index
        index += 1

        while index < n_rows and values[index] >= low:
            end_index = index
            index += 1

        peak = float(np.nanmax(values[start_index : end_index + 1]))
        pulses.append(
            Pulse(
                index=pulse_index,
                start_index=start_index,
                end_index=end_index,
                start_time=start_time,
                end_time=float(times[end_index]),
                start_value=float(values[start_index]),
                peak_value=peak,
            )
        )
        pulse_index += 1

        guard_until = start_time + min_separation_sec
        while index < n_rows and times[index] < guard_until:
            index += 1

    return pulses


def sample_stats(times: np.ndarray) -> dict:
    if len(times) < 2:
        return {
            "samples": int(len(times)),
            "median_dt": None,
            "mean_dt": None,
            "max_dt": None,
            "duration": 0.0,
            "warnings": ["CSV has fewer than two samples."],
        }

    diffs = np.diff(times)
    positive = diffs[diffs > 0]
    warnings: list[str] = []
    if len(positive) == 0:
        warnings.append("CSV timestamps are not increasing.")
        median_dt = mean_dt = max_dt = None
    else:
        median_dt = float(np.median(positive))
        mean_dt = float(np.mean(positive))
        max_dt = float(np.max(positive))
        if max_dt > median_dt * 10 and max_dt > 0.05:
            warnings.append(f"Large timestamp gap detected: {max_dt:.4f}s.")
        if np.any(diffs <= 0):
            warnings.append("CSV contains duplicate or decreasing timestamps.")

    return {
        "samples": int(len(times)),
        "median_dt": median_dt,
        "mean_dt": mean_dt,
        "max_dt": max_dt,
        "duration": float(times[-1] - times[0]),
        "warnings": warnings,
    }


def trim_csv_to_window(input_path: Path, output_path: Path, start_time: float, end_time: float) -> int:
    data = read_sensor_csv(input_path)
    mask = (data.times >= start_time) & (data.times <= end_time)
    trimmed = data.raw.loc[mask].copy()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    trimmed.to_csv(output_path, index=False)
    return int(mask.sum())
