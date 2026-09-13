# Light Calibration Tool

Local web app for cutting long swimming videos into calibrated swim segments using a visible light pulse and paired Phidget CSV files.

The CSV files may still be named `buzzer` and `force`. In this version, `buzzer` means the light-calibration voltage channel, not audio.

## Workflow

1. Provide a local folder containing 1-5 long MP4 videos, or upload smaller video files.
2. Provide a local CSV folder, or upload all CSV files.
3. The app pairs `buzzer` and `force` CSV files by filename.
4. For each `buzzer` CSV, it finds:
   - start: first sample where voltage is between `0.002` and `0.0025`
   - end: first sample in the next pulse after the signal dropped below the low threshold
5. Set the start point of each long video so pre-test dead time is skipped.
6. Draw a small rectangle around the light in each camera.
7. The app detects light-on events in each video ROI and pairs them as segment start/end.
8. Review every segment, warnings, durations, and short preview clips.
9. Export frame-accurate video clips and timestamp-masked CSVs as a zip.

## Setup on a new computer

```powershell
git clone git@github.com:Azaryad/light-calibration-tool.git
cd light-calibration-tool
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Open the printed local URL (default `http://127.0.0.1:5000`) in a browser.

This repo ships source only — no videos, CSVs, or job data (`data/` and `exports/` are gitignored). Point the app at your local video/CSV folders after cloning.

### If you're an agent (Claude Code, etc.) setting this up for someone

1. Clone the repo above.
2. Confirm Python 3.11+ is available (`python --version`).
3. `pip install -r requirements.txt` (Flask, numpy, pandas, opencv-python).
4. Confirm `ffmpeg` is on `PATH` (see Requirements below) — install it if missing (e.g. `winget install Gyan.FFmpeg` on Windows).
5. Run `python app.py` and open the printed URL — the "ffmpeg: <path>" banner on the Upload page confirms it was found.
6. There is no data bundled in the repo; ask the user where their video folder and CSV folder live locally, and enter those paths in the "Local video folder" / "Or local CSV folder" fields on the Upload page (browser upload also works for smaller files).

## Requirements

`ffmpeg` is required for preview and export. The app looks in:

- `FFMPEG_PATH`
- `FFMPEG_BINARY`
- `PATH`
- common Windows install locations
- `.\ffmpeg\bin\ffmpeg.exe`

## Notes

- For large videos, use the local video folder field. Browser upload copies the full file into the job folder and can be slow for multi-GB MP4s.
- For CSV ordering, use the local CSV folder field. The app orders CSV pairs by the original file modified time; browser uploads may not preserve that timestamp.
- The start-trim page does not rewrite the original videos. It tells the detector where to begin scanning and which frame to use for ROI marking.
- Video detection uses the rectangle selected by the user and compares it with a surrounding background ring. Keep the rectangle tight around the light so slow sunlight changes around the light can be subtracted out.
- CSV and video durations are shown separately. Small differences of a few hundredths of a second can happen because cameras and the Phidget are different devices.
- Exports re-encode videos with `libx264` and use `-ss ... -i ... -t duration` for frame-accurate cuts.
