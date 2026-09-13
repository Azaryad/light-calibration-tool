from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import uuid
import zipfile

from flask import Flask, abort, redirect, render_template, request, send_file, url_for
from werkzeug.utils import secure_filename

from lightcalib.csv_utils import (
    detect_voltage_pulses,
    pair_csv_files,
    read_sensor_csv,
    sample_stats,
    trim_csv_to_window,
)
from lightcalib.video_utils import (
    create_preview_clip,
    detect_light_events,
    detect_light_events_guided,
    export_video_clip,
    locate_ffmpeg,
    save_first_frame,
    save_video_frame,
    video_metadata,
)


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data" / "jobs"
ALLOWED_VIDEO_EXT = {".mp4", ".mov", ".m4v"}
ALLOWED_CSV_EXT = {".csv", ".txt"}

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "light-calibration-dev")


@app.template_filter("sec")
def format_seconds(value: object) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):.3f}s"
    except (TypeError, ValueError):
        return "-"


@app.template_filter("num")
def format_number(value: object, digits: int = 4) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "-"


@app.get("/")
def index():
    ffmpeg = locate_ffmpeg()
    return render_template("index.html", ffmpeg_found=bool(ffmpeg), ffmpeg_path=ffmpeg)


@app.post("/upload")
def upload():
    job_id = uuid.uuid4().hex[:12]
    job_dir = DATA_DIR / job_id
    input_dir = job_dir / "input"
    frame_dir = job_dir / "frames"
    input_dir.mkdir(parents=True, exist_ok=True)
    frame_dir.mkdir(parents=True, exist_ok=True)

    voltage_low = float(request.form.get("voltage_low", "0.002"))
    voltage_high = float(request.form.get("voltage_high", "0.0025"))
    expected_min = float(request.form.get("expected_min", "20"))
    expected_max = float(request.form.get("expected_max", "60"))

    video_folder = request.form.get("video_folder", "").strip().strip('"')
    csv_folder = request.form.get("csv_folder", "").strip().strip('"')
    videos = collect_local_videos(video_folder) if video_folder else []
    local_video_paths = {path.resolve() for path in videos}
    uploaded_videos = save_uploads(request.files.getlist("videos"), input_dir, ALLOWED_VIDEO_EXT)
    videos.extend(uploaded_videos)
    csvs = collect_local_csvs(csv_folder) if csv_folder else []
    csvs.extend(save_uploads(request.files.getlist("csvs"), input_dir, ALLOWED_CSV_EXT))
    if not videos:
        abort(400, "Upload at least one MP4 video or provide a local video folder.")
    if len(videos) > 5:
        abort(400, f"Use 1-5 videos per run. Found {len(videos)} video files.")
    if not csvs:
        abort(400, "Upload buzzer and force CSV files.")

    manifest = {
        "id": job_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "settings": {
            "voltage_low": voltage_low,
            "voltage_high": voltage_high,
            "expected_min": expected_min,
            "expected_max": expected_max,
        },
        "warnings": [],
        "videos": [],
        "csv_pairs": [],
        "csv_flash_timeline": [],
        "segments": [],
        "rois": {},
        "analysis": {},
    }

    for idx, path in enumerate(videos, start=1):
        video_id = f"video_{idx:02d}"
        first_frame = frame_dir / f"{video_id}.jpg"
        meta = save_first_frame(path, first_frame)
        manifest["videos"].append(
            {
                "id": video_id,
                "name": path.name,
                "path": str(path),
                "source": "local folder" if path.resolve() in local_video_paths else "upload",
                "trim_start": 0.0,
                "first_frame": str(first_frame),
                "first_frame_url": url_for("job_asset", job_id=job_id, folder="frames", filename=first_frame.name),
                "meta": meta.as_dict(),
            }
        )

    pairs, pair_warnings = pair_csv_files(csvs)
    manifest["warnings"].extend(pair_warnings)
    if not pairs:
        save_manifest(job_dir, manifest)
        abort(400, "No force/buzzer CSV pairs were found. Filenames must contain force and buzzer.")

    for idx, pair in enumerate(pairs, start=1):
        segment = build_csv_segment(idx, pair["force"], pair["buzzer"], voltage_low, voltage_high, expected_min, expected_max)
        manifest["csv_pairs"].append(
            {
                "index": idx,
                "force": str(pair["force"]),
                "buzzer": str(pair["buzzer"]),
                "force_name": pair["force"].name,
                "buzzer_name": pair["buzzer"].name,
                "key": list(pair["key"]),
            }
        )
        manifest["segments"].append(segment)

    normalize_segment_order(manifest)
    save_manifest(job_dir, manifest)
    return redirect(url_for("trim_start", job_id=job_id))


@app.get("/trim-start/<job_id>")
def trim_start(job_id: str):
    manifest = load_manifest(job_id)
    return render_template("trim_start.html", manifest=manifest)


@app.post("/trim-start/<job_id>")
def save_trim_start(job_id: str):
    job_dir = DATA_DIR / job_id
    frame_dir = job_dir / "frames"
    manifest = load_manifest(job_id)

    for video in manifest["videos"]:
        video_id = video["id"]
        raw_value = request.form.get(f"trim_{video_id}", "0").strip()
        try:
            trim_value = max(0.0, float(raw_value or 0))
        except ValueError:
            abort(400, f"Invalid start trim for {video['name']}.")

        duration = float(video["meta"].get("duration") or 0)
        if duration > 0:
            trim_value = min(trim_value, max(0.0, duration - 0.1))

        frame_file = frame_dir / f"{video_id}_t{int(round(trim_value * 1000)):09d}.jpg"
        meta = save_video_frame(Path(video["path"]), frame_file, trim_value)
        video["trim_start"] = trim_value
        video["first_frame"] = str(frame_file)
        video["first_frame_url"] = url_for("job_asset", job_id=job_id, folder="frames", filename=frame_file.name)
        video["meta"] = meta.as_dict()

    manifest["rois"] = {}
    manifest["analysis"] = {}
    for segment in manifest["segments"]:
        segment["videos"] = {}

    save_manifest(job_dir, manifest)
    return redirect(url_for("roi", job_id=job_id))


@app.get("/roi/<job_id>")
def roi(job_id: str):
    manifest = load_manifest(job_id)
    return render_template("roi.html", manifest=manifest)


@app.post("/roi/<job_id>")
def analyze_roi(job_id: str):
    job_dir = DATA_DIR / job_id
    manifest = load_manifest(job_id)
    rois: dict[str, dict] = {}
    for video in manifest["videos"]:
        video_id = video["id"]
        prefix = f"roi_{video_id}_"
        try:
            roi = {
                "x": float(request.form[prefix + "x"]),
                "y": float(request.form[prefix + "y"]),
                "w": float(request.form[prefix + "w"]),
                "h": float(request.form[prefix + "h"]),
            }
        except KeyError:
            abort(400, f"Missing ROI for {video['name']}.")
        if roi["w"] <= 1 or roi["h"] <= 1:
            abort(400, f"ROI for {video['name']} is too small.")
        rois[video_id] = roi

    manifest["rois"] = rois
    video_analysis = {}
    csv_flash_timeline = manifest.get("csv_flash_timeline") or build_csv_flash_timeline(manifest["segments"])
    for video in manifest["videos"]:
        video_id = video["id"]
        if csv_flash_timeline:
            video_analysis[video_id] = detect_light_events_guided(
                Path(video["path"]),
                rois[video_id],
                csv_flash_timeline,
                start_time_sec=float(video.get("trim_start") or 0.0),
            )
        else:
            video_analysis[video_id] = detect_light_events(
                Path(video["path"]),
                rois[video_id],
                start_time_sec=float(video.get("trim_start") or 0.0),
            )

    manifest["analysis"]["videos"] = video_analysis
    attach_video_segments(manifest)
    save_manifest(job_dir, manifest)
    return redirect(url_for("review", job_id=job_id))


@app.get("/review/<job_id>")
def review(job_id: str):
    manifest = load_manifest(job_id)
    ffmpeg = locate_ffmpeg()
    return render_template("review.html", manifest=manifest, ffmpeg_found=bool(ffmpeg))


@app.get("/asset/<job_id>/<folder>/<filename>")
def job_asset(job_id: str, folder: str, filename: str):
    if folder not in {"frames", "previews"}:
        abort(404)
    directory = DATA_DIR / job_id / folder
    path = directory / filename
    if not path.is_file():
        abort(404)
    return send_file(path)


@app.get("/frame/<job_id>/<video_id>")
def video_frame(job_id: str, video_id: str):
    manifest = load_manifest(job_id)
    video = find_by_id(manifest["videos"], video_id)
    try:
        time_sec = max(0.0, float(request.args.get("t", "0")))
    except ValueError:
        abort(400, "Invalid frame time.")

    frame_dir = DATA_DIR / job_id / "frames"
    filename = f"{video_id}_preview_{int(round(time_sec * 1000)):09d}.jpg"
    output = frame_dir / filename
    if not output.is_file():
        save_video_frame(Path(video["path"]), output, time_sec)
    return send_file(output, mimetype="image/jpeg")


@app.get("/video/<job_id>/<video_id>")
def source_video(job_id: str, video_id: str):
    manifest = load_manifest(job_id)
    video = find_by_id(manifest["videos"], video_id)
    path = Path(video["path"])
    if not path.is_file():
        abort(404)
    return send_file(path, conditional=True)


@app.get("/preview/<job_id>/<video_id>/<segment_id>.mp4")
def preview_clip(job_id: str, video_id: str, segment_id: str):
    manifest = load_manifest(job_id)
    ffmpeg = locate_ffmpeg()
    if not ffmpeg:
        abort(503, "ffmpeg was not found.")

    segment = find_by_id(manifest["segments"], segment_id)
    video = find_by_id(manifest["videos"], video_id)
    clip = segment.get("videos", {}).get(video_id)
    if not clip or not clip.get("ok"):
        abort(404)

    output = DATA_DIR / job_id / "previews" / f"{segment_id}_{video_id}.mp4"
    if not output.is_file():
        preview_duration = min(6.0, max(0.1, float(clip["duration"])))
        create_preview_clip(ffmpeg, Path(video["path"]), output, float(clip["start_time"]), preview_duration)
    return send_file(output, mimetype="video/mp4", conditional=True)


@app.get("/manual-preview/<job_id>/<video_id>/<segment_id>/<kind>.mp4")
def manual_preview_clip(job_id: str, video_id: str, segment_id: str, kind: str):
    if kind not in {"start", "finish"}:
        abort(404)

    manifest = load_manifest(job_id)
    ffmpeg = locate_ffmpeg()
    if not ffmpeg:
        abort(503, "ffmpeg was not found.")

    segment = find_by_id(manifest["segments"], segment_id)
    video = find_by_id(manifest["videos"], video_id)
    clip = segment.get("videos", {}).get(video_id)
    if not clip or not clip.get("ok"):
        abort(404)

    center = float(clip["start_time"] if kind == "start" else clip["end_time"])
    window_start = max(0.0, center - 3.0)
    duration = 6.0
    window_key = int(round(window_start * 1000))
    output = DATA_DIR / job_id / "previews" / f"{segment_id}_{video_id}_{kind}_{window_key:010d}.mp4"
    if not output.is_file():
        create_preview_clip(ffmpeg, Path(video["path"]), output, window_start, duration)
    return send_file(output, mimetype="video/mp4", conditional=True)


@app.post("/adjust/<job_id>")
def adjust(job_id: str):
    job_dir = DATA_DIR / job_id
    manifest = load_manifest(job_id)

    for segment in manifest["segments"]:
        for video in manifest["videos"]:
            clip = segment.get("videos", {}).get(video["id"])
            if not clip:
                continue

            prefix = f"adjust__{segment['id']}__{video['id']}__"
            start_raw = request.form.get(prefix + "start")
            end_raw = request.form.get(prefix + "end")
            if start_raw in (None, "") or end_raw in (None, ""):
                continue

            try:
                start_time = float(start_raw)
                end_time = float(end_raw)
            except ValueError:
                continue

            apply_clip_times(segment, video, clip, start_time, end_time, manual=True)

    rebuild_segment_status(manifest)
    save_manifest(job_dir, manifest)
    return redirect(url_for("review", job_id=job_id))


@app.post("/export/<job_id>")
def export(job_id: str):
    manifest = load_manifest(job_id)
    ffmpeg = locate_ffmpeg()
    if not ffmpeg:
        abort(503, "ffmpeg was not found. Install ffmpeg or set FFMPEG_PATH.")

    export_dir = DATA_DIR / job_id / "export"
    if export_dir.exists():
        shutil.rmtree(export_dir)
    export_dir.mkdir(parents=True, exist_ok=True)

    for segment in manifest["segments"]:
        segment_dir = export_dir / segment["id"]
        segment_dir.mkdir(parents=True, exist_ok=True)

        force_start = float(segment["csv"].get("force_trim_start_time", segment["csv"]["start_time"]))
        force_end = float(segment["csv"].get("force_trim_end_time", segment["csv"]["end_time"]))
        buzzer_start = float(segment["csv"].get("buzzer_trim_start_time", segment["csv"]["start_time"]))
        buzzer_end = float(segment["csv"].get("buzzer_trim_end_time", segment["csv"]["end_time"]))
        force_path = Path(segment["force"]["path"])
        buzzer_path = Path(segment["buzzer"]["path"])
        trim_csv_to_window(force_path, segment_dir / f"{segment['id']}_force.csv", force_start, force_end)
        trim_csv_to_window(buzzer_path, segment_dir / f"{segment['id']}_buzzer.csv", buzzer_start, buzzer_end)

        for video in manifest["videos"]:
            clip = segment.get("videos", {}).get(video["id"])
            if not clip or not clip.get("ok"):
                continue
            output_name = f"{segment['id']}_{safe_stem(video['name'])}.mp4"
            export_video_clip(
                ffmpeg,
                Path(video["path"]),
                segment_dir / output_name,
                float(clip["start_time"]),
                float(clip["duration"]),
            )

        (segment_dir / "segment_metadata.json").write_text(json.dumps(segment, indent=2), encoding="utf-8")

    zip_path = DATA_DIR / job_id / f"light_calibration_export_{job_id}.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in export_dir.rglob("*"):
            if path.is_file():
                archive.write(path, path.relative_to(export_dir))

    return send_file(zip_path, as_attachment=True, download_name=zip_path.name)


def save_uploads(files, target_dir: Path, allowed_ext: set[str]) -> list[Path]:
    saved: list[Path] = []
    for uploaded in files:
        if not uploaded or not uploaded.filename:
            continue
        original = Path(uploaded.filename).name
        ext = Path(original).suffix.casefold()
        if ext not in allowed_ext:
            continue
        filename = unique_filename(target_dir, secure_filename(original) or f"upload{ext}")
        path = target_dir / filename
        uploaded.save(path)
        saved.append(path)
    return saved


def collect_local_videos(folder_text: str) -> list[Path]:
    folder = Path(folder_text).expanduser().resolve()
    if not folder.is_dir():
        abort(400, f"Video folder does not exist: {folder_text}")
    videos = [path for path in folder.iterdir() if path.is_file() and path.suffix.casefold() in ALLOWED_VIDEO_EXT]
    videos.sort(key=lambda path: path.name.casefold())
    return videos


def collect_local_csvs(folder_text: str) -> list[Path]:
    folder = Path(folder_text).expanduser().resolve()
    if not folder.is_dir():
        abort(400, f"CSV folder does not exist: {folder_text}")
    csvs = [path for path in folder.iterdir() if path.is_file() and path.suffix.casefold() in ALLOWED_CSV_EXT]
    csvs.sort(key=lambda path: path.name.casefold())
    return csvs


def unique_filename(directory: Path, filename: str) -> str:
    candidate = filename
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    counter = 2
    while (directory / candidate).exists():
        candidate = f"{stem}_{counter}{suffix}"
        counter += 1
    return candidate


def safe_stem(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in Path(name).stem)


def build_csv_segment(
    index: int,
    force_path: Path,
    buzzer_path: Path,
    voltage_low: float,
    voltage_high: float,
    expected_min: float,
    expected_max: float,
) -> dict:
    warnings: list[str] = []
    buzzer = read_sensor_csv(buzzer_path)
    force = read_sensor_csv(force_path)
    pulses = detect_voltage_pulses(buzzer.times, buzzer.values, voltage_low, voltage_high)
    buzzer_stats = sample_stats(buzzer.times)
    force_stats = sample_stats(force.times)
    warnings.extend([f"Buzzer CSV: {item}" for item in buzzer_stats["warnings"]])
    warnings.extend([f"Force CSV: {item}" for item in force_stats["warnings"]])

    csv_start = None
    csv_end = None
    csv_duration = None
    ok = False
    if len(pulses) < 2:
        warnings.append(f"Expected two calibration pulses in {buzzer_path.name}, found {len(pulses)}.")
    else:
        csv_start = pulses[0].start_time
        csv_end = pulses[1].start_time
        csv_duration = csv_end - csv_start
        ok = csv_duration > 0
        if not ok:
            warnings.append("CSV calibration pulse order is invalid.")
        elif csv_duration < expected_min or csv_duration > expected_max:
            warnings.append(
                f"CSV duration {csv_duration:.3f}s is outside expected range "
                f"{expected_min:.1f}-{expected_max:.1f}s."
            )

    return {
        "id": f"segment_{index:03d}",
        "index": index,
        "label": f"Segment {index}",
        "ok": ok,
        "warnings": warnings,
        "force": {
            "name": force_path.name,
            "path": str(force_path),
            "stats": force_stats,
            "first_time": float(force.times[0]) if len(force.times) else None,
            "last_time": float(force.times[-1]) if len(force.times) else None,
            "time_basis": "absolute" if is_absolute_timestamp(force.times[0] if len(force.times) else None) else "relative",
        },
        "buzzer": {
            "name": buzzer_path.name,
            "path": str(buzzer_path),
            "pulses": [pulse.as_dict() for pulse in pulses],
            "stats": buzzer_stats,
            "first_time": float(buzzer.times[0]) if len(buzzer.times) else None,
            "last_time": float(buzzer.times[-1]) if len(buzzer.times) else None,
            "time_basis": "absolute" if is_absolute_timestamp(buzzer.times[0] if len(buzzer.times) else None) else "relative",
        },
        "csv": {
            "start_time": csv_start,
            "end_time": csv_end,
            "duration": csv_duration,
        },
        "videos": {},
        "confidence": "warning" if warnings else "ok",
    }


def normalize_segment_order(manifest: dict) -> None:
    normalize_segment_timeline(manifest)
    combined = list(zip(manifest["segments"], manifest["csv_pairs"], strict=False))
    combined.sort(key=lambda item: (
        float(item[0].get("csv", {}).get("timeline_start_time") or item[0].get("csv", {}).get("start_time") or float("inf")),
        item[0].get("force", {}).get("name", ""),
    ))

    manifest["segments"] = []
    manifest["csv_pairs"] = []
    for idx, (segment, pair) in enumerate(combined, start=1):
        segment["id"] = f"segment_{idx:03d}"
        segment["index"] = idx
        segment["label"] = f"Segment {idx}"
        pair["index"] = idx
        manifest["segments"].append(segment)
        manifest["csv_pairs"].append(pair)

    manifest["csv_flash_timeline"] = build_csv_flash_timeline(manifest["segments"])


def normalize_segment_timeline(manifest: dict) -> None:
    segments = manifest["segments"]
    absolute_starts = [
        float(segment["csv"]["start_time"])
        for segment in segments
        if is_absolute_timestamp(segment.get("csv", {}).get("start_time"))
    ]
    if not absolute_starts:
        for segment in segments:
            segment["csv"]["timeline_start_time"] = segment["csv"].get("start_time")
            segment["csv"]["timeline_end_time"] = segment["csv"].get("end_time")
            segment["csv"]["force_trim_start_time"] = segment["csv"].get("start_time")
            segment["csv"]["force_trim_end_time"] = segment["csv"].get("end_time")
            segment["csv"]["buzzer_trim_start_time"] = segment["csv"].get("start_time")
            segment["csv"]["buzzer_trim_end_time"] = segment["csv"].get("end_time")
        return

    min_abs = min(absolute_starts)
    max_abs = max(absolute_starts)
    hour_start = int((min_abs - 7200) // 3600) * 3600
    hour_end = int((max_abs + 7200) // 3600) * 3600
    hour_candidates = range(hour_start, hour_end + 3600, 3600)
    timeline_warnings: list[str] = []

    for segment in segments:
        csv = segment["csv"]
        start_time = csv.get("start_time")
        end_time = csv.get("end_time")
        if start_time is None or end_time is None:
            continue

        if is_absolute_timestamp(start_time):
            csv["timeline_start_time"] = start_time
            csv["timeline_end_time"] = end_time
            csv["timeline_basis"] = "absolute"
            csv["force_trim_start_time"] = start_time if segment.get("force", {}).get("time_basis") == "absolute" else csv["start_time"]
            csv["force_trim_end_time"] = end_time if segment.get("force", {}).get("time_basis") == "absolute" else csv["end_time"]
            csv["buzzer_trim_start_time"] = start_time if segment.get("buzzer", {}).get("time_basis") == "absolute" else csv["start_time"]
            csv["buzzer_trim_end_time"] = end_time if segment.get("buzzer", {}).get("time_basis") == "absolute" else csv["end_time"]
            continue

        rel_start = float(start_time)
        rel_end = float(end_time)
        rel_duration = rel_end - rel_start
        force_first = segment.get("force", {}).get("first_time")
        force_last = segment.get("force", {}).get("last_time")
        best = None
        best_end = None
        warning_basis = "shared CSV timeline"
        if is_absolute_timestamp(force_first) and is_absolute_timestamp(force_last):
            best = choose_time_only_candidate(rel_start, float(force_first), float(force_last))
            best_end = choose_time_only_candidate(rel_end, float(force_first), float(force_last))
            warning_basis = "matched force CSV timeline"
        if best is None:
            best = choose_absolute_candidate(rel_start, hour_candidates, min_abs, max_abs)
            best_end = best + rel_duration if best is not None else None
            warning_basis = "shared CSV timeline"
        if best is None or best_end is None:
            csv["timeline_start_time"] = start_time
            csv["timeline_end_time"] = end_time
            csv["timeline_basis"] = "relative"
            csv["force_trim_start_time"] = start_time
            csv["force_trim_end_time"] = end_time
            csv["buzzer_trim_start_time"] = start_time
            csv["buzzer_trim_end_time"] = end_time
            continue

        csv["timeline_start_time"] = best
        csv["timeline_end_time"] = best_end
        csv["timeline_basis"] = "inferred_hour"
        csv["force_trim_start_time"] = best if segment.get("force", {}).get("time_basis") == "absolute" else start_time
        csv["force_trim_end_time"] = best_end if segment.get("force", {}).get("time_basis") == "absolute" else end_time
        csv["buzzer_trim_start_time"] = best if segment.get("buzzer", {}).get("time_basis") == "absolute" else start_time
        csv["buzzer_trim_end_time"] = best_end if segment.get("buzzer", {}).get("time_basis") == "absolute" else end_time
        timeline_warnings.append(
            f"Interpreted {segment['buzzer']['name']} minute-second timestamps on the {warning_basis}."
        )

    if timeline_warnings:
        existing = set(manifest.get("warnings", []))
        for warning in timeline_warnings:
            if warning not in existing:
                manifest.setdefault("warnings", []).append(warning)


def is_absolute_timestamp(value: object) -> bool:
    try:
        return float(value) > 1_000_000_000
    except (TypeError, ValueError):
        return False


def choose_absolute_candidate(
    relative_seconds: float,
    hour_candidates: range,
    min_abs: float,
    max_abs: float,
) -> float | None:
    candidates = [hour + relative_seconds for hour in hour_candidates]
    window_start = min_abs - 1800
    window_end = max_abs + 1800
    in_window = [candidate for candidate in candidates if window_start <= candidate <= window_end]
    if in_window:
        center = (min_abs + max_abs) / 2
        return min(in_window, key=lambda candidate: abs(candidate - center))
    center = (min_abs + max_abs) / 2
    return min(candidates, key=lambda candidate: abs(candidate - center)) if candidates else None


def choose_time_only_candidate(time_seconds: float, reference_start: float, reference_end: float) -> float | None:
    reference_center = (reference_start + reference_end) / 2
    window_start = reference_start - 300
    window_end = reference_end + 300

    if 0 <= time_seconds < 3600:
        base = int(reference_center // 3600) * 3600
        candidates = [base + (hour_offset * 3600) + time_seconds for hour_offset in range(-3, 4)]
    elif 0 <= time_seconds < 86400:
        base = int(reference_center // 86400) * 86400
        candidates = [base + (day_offset * 86400) + time_seconds for day_offset in range(-1, 2)]
    else:
        return None

    in_window = [candidate for candidate in candidates if window_start <= candidate <= window_end]
    if in_window:
        return min(in_window, key=lambda candidate: abs(candidate - reference_center))
    return min(candidates, key=lambda candidate: abs(candidate - reference_center)) if candidates else None


def build_csv_flash_timeline(segments: list[dict]) -> list[float]:
    flash_times: list[float] = []
    for segment in segments:
        start_time = segment.get("csv", {}).get("timeline_start_time", segment.get("csv", {}).get("start_time"))
        end_time = segment.get("csv", {}).get("timeline_end_time", segment.get("csv", {}).get("end_time"))
        if start_time is None or end_time is None:
            continue
        flash_times.extend([float(start_time), float(end_time)])
    return flash_times


def attach_video_segments(manifest: dict) -> None:
    settings = manifest["settings"]
    expected_min = float(settings["expected_min"])
    expected_max = float(settings["expected_max"])
    analysis = manifest.get("analysis", {}).get("videos", {})

    clean_segment_warnings = {
        segment["id"]: [warning for warning in segment.get("warnings", []) if not warning.startswith("Video ")]
        for segment in manifest["segments"]
    }
    for segment in manifest["segments"]:
        segment["videos"] = {}

    for video in manifest["videos"]:
        video_id = video["id"]
        result = analysis.get(video_id, {})
        events = result.get("events", [])
        assignments = assign_video_event_pairs(manifest["segments"], events, expected_min, expected_max)

        for segment in manifest["segments"]:
            pair = assignments.get(segment["id"])
            clip = {
                "ok": False,
                "start_time": None,
                "end_time": None,
                "duration": None,
                "start_frame": None,
                "end_frame": None,
                "warnings": [],
            }

            if pair is None:
                clip["warnings"].append(
                    f"No light-event pair matched this segment. Detected {len(events)} total event(s)."
                )
            else:
                start_event = events[pair["start_index"]]
                end_event = events[pair["end_index"]]
                start_time = float(start_event["time"])
                end_time = float(end_event["time"])
                duration = end_time - start_time
                clip.update(
                    {
                        "ok": duration > 0,
                        "start_time": start_time,
                        "end_time": end_time,
                        "duration": duration,
                        "start_frame": int(start_event["frame"]),
                        "end_frame": int(end_event["frame"]),
                        "start_event": start_event,
                        "end_event": end_event,
                        "event_pair": pair,
                    }
                )
                if duration <= 0:
                    clip["warnings"].append("Video light event order is invalid.")
                elif duration < expected_min or duration > expected_max:
                    clip["warnings"].append(
                        f"Video duration {duration:.3f}s is outside expected range "
                        f"{expected_min:.1f}-{expected_max:.1f}s."
                    )

                csv_duration = segment["csv"].get("duration")
                if csv_duration is not None and duration > 0:
                    delta = abs(duration - float(csv_duration))
                    clip["csv_delta"] = delta
                    if delta > 0.25:
                        clip["warnings"].append(f"Video/CSV duration differs by {delta:.3f}s.")

                if pair.get("skipped_events"):
                    clip["warnings"].append(
                        f"Skipped {pair['skipped_events']} extra detected light event(s) while matching CSV duration."
                    )

            if result.get("warnings"):
                clip["warnings"].extend(result["warnings"])

            if clip["warnings"]:
                clean_segment_warnings[segment["id"]].extend(
                    [f"Video {video['name']}: {warning}" for warning in clip["warnings"]]
                )
            segment["videos"][video_id] = clip

    for segment in manifest["segments"]:
        segment_warnings = clean_segment_warnings[segment["id"]]
        segment["warnings"] = segment_warnings
        segment["ok"] = not segment_warnings and bool(segment["csv"].get("duration"))
        segment["confidence"] = "ok" if segment["ok"] else "warning"


def assign_video_event_pairs(
    segments: list[dict],
    events: list[dict],
    expected_min: float,
    expected_max: float,
) -> dict[str, dict]:
    assignments: dict[str, dict] = {}
    guided_positions = {
        int(event["expected_index"]): index
        for index, event in enumerate(events)
        if event.get("expected_index") is not None
    }
    used_guided_events: set[int] = set()

    for segment_index, segment in enumerate(segments):
        start_expected = segment_index * 2
        end_expected = start_expected + 1
        if start_expected not in guided_positions or end_expected not in guided_positions:
            continue

        start_index = guided_positions[start_expected]
        end_index = guided_positions[end_expected]
        if start_index in used_guided_events or end_index in used_guided_events:
            continue

        duration = float(events[end_index]["time"]) - float(events[start_index]["time"])
        csv_duration = segment.get("csv", {}).get("duration")
        assignments[segment["id"]] = {
            "start_index": start_index,
            "end_index": end_index,
            "duration": duration,
            "csv_delta": abs(duration - float(csv_duration)) if csv_duration is not None else None,
            "skipped_events": 0,
            "score": abs(duration - float(csv_duration)) if csv_duration is not None else 0.0,
            "guided": True,
        }
        used_guided_events.update({start_index, end_index})

    candidates: list[dict] = []

    for start_index in range(0, max(0, len(events) - 1)):
        if start_index in used_guided_events:
            continue
        for end_index in range(start_index + 1, len(events)):
            if end_index in used_guided_events:
                continue
            duration = float(events[end_index]["time"]) - float(events[start_index]["time"])
            if expected_min <= duration <= expected_max:
                candidates.append(
                    {
                        "start_index": start_index,
                        "end_index": end_index,
                        "duration": duration,
                        "skipped_events": end_index - start_index - 1,
                    }
                )

    matches: list[dict] = []
    for segment in segments:
        if segment["id"] in assignments:
            continue
        csv_duration = segment.get("csv", {}).get("duration")
        if csv_duration is None:
            continue
        for candidate_index, candidate in enumerate(candidates):
            delta = abs(float(candidate["duration"]) - float(csv_duration))
            matches.append(
                {
                    "segment_id": segment["id"],
                    "candidate_index": candidate_index,
                    "delta": delta,
                    "score": delta + candidate["skipped_events"] * 0.05,
                }
            )

    used_segments: set[str] = set()
    used_events: set[int] = set()
    matches.sort(key=lambda item: (item["score"], item["delta"], item["candidate_index"]))

    for match in matches:
        segment_id = match["segment_id"]
        candidate = candidates[match["candidate_index"]]
        event_range = set(range(candidate["start_index"], candidate["end_index"] + 1))
        if segment_id in used_segments or event_range & used_events:
            continue

        assignments[segment_id] = {
            **candidate,
            "csv_delta": match["delta"],
            "score": match["score"],
        }
        used_segments.add(segment_id)
        used_events.update(event_range)

    return assignments


def apply_clip_times(segment: dict, video: dict, clip: dict, start_time: float, end_time: float, manual: bool = False) -> None:
    fps = float(video.get("meta", {}).get("fps") or 0.0)
    video_duration = float(video.get("meta", {}).get("duration") or 0.0)
    start_time = max(0.0, start_time)
    end_time = max(0.0, end_time)
    if video_duration > 0:
        start_time = min(start_time, video_duration)
        end_time = min(end_time, video_duration)

    duration = end_time - start_time
    clip["start_time"] = start_time
    clip["end_time"] = end_time
    clip["duration"] = duration
    clip["start_frame"] = int(round(start_time * fps)) if fps > 0 else None
    clip["end_frame"] = int(round(end_time * fps)) if fps > 0 else None
    clip["manual_adjusted"] = bool(manual)
    clip["warnings"] = []

    if duration <= 0:
        clip["ok"] = False
        clip["warnings"].append("Manual start/end frame order is invalid.")
    else:
        clip["ok"] = True

    csv_duration = segment.get("csv", {}).get("duration")
    if csv_duration is not None and duration > 0:
        delta = abs(duration - float(csv_duration))
        clip["csv_delta"] = delta
        if delta > 0.25:
            clip["warnings"].append(f"Video/CSV duration differs by {delta:.3f}s.")


def rebuild_segment_status(manifest: dict) -> None:
    for segment in manifest["segments"]:
        warnings = [warning for warning in segment.get("warnings", []) if not warning.startswith("Video ")]
        for video in manifest["videos"]:
            clip = segment.get("videos", {}).get(video["id"])
            if not clip:
                continue
            for warning in clip.get("warnings", []):
                warnings.append(f"Video {video['name']}: {warning}")

        segment["warnings"] = warnings
        segment["ok"] = not warnings and bool(segment.get("csv", {}).get("duration"))
        segment["confidence"] = "ok" if segment["ok"] else "warning"


def find_by_id(items: list[dict], item_id: str) -> dict:
    for item in items:
        if item.get("id") == item_id:
            return item
    abort(404)


def manifest_path(job_id: str) -> Path:
    return DATA_DIR / job_id / "manifest.json"


def save_manifest(job_dir: Path, manifest: dict) -> None:
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def load_manifest(job_id: str) -> dict:
    path = manifest_path(job_id)
    if not path.is_file():
        abort(404)
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    port = int(os.environ.get("PORT", "5000"))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="127.0.0.1", port=port, debug=debug)
