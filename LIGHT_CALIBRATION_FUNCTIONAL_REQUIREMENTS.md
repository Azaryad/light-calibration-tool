# Light-Based Video and Force Calibration Tool

## 1. Purpose

The tool calibrates long underwater swimming videos against force-sensor CSV files using a visible light pulse. The previous audio/buzzer synchronization model is no longer the primary model. The physical synchronization marker is now a strong light that is visible in the cameras and recorded as a voltage change in the calibration CSV channel.

The final goal is to split long video recordings into short swim segments and export each segment with the matching force CSV data, aligned as closely as the data allows.

## 2. Input Files

### 2.1 Video Files

The user provides approximately 1 to 5 long video files per run.

Expected video characteristics:

- Format: MP4.
- Cameras are static during the full recording.
- Each camera may start at a slightly different time.
- The videos can be very large, several GB each.
- The recording may include a long pre-test period, sometimes up to 10 minutes or more.
- The swimmer test segments are usually around 30 seconds, broadly between 20 and 60 seconds.
- A physical light appears near the bottom of the frame and turns on once at the start of a swim and once at the end.

Observed examples:

- Long GoPro-style videos at about 59.94 fps.
- Short test recording at 30 fps and about 39 seconds.
- Large files should be referenced by local folder path, not uploaded through the browser, to avoid copying multi-GB files.

### 2.2 CSV Files

The CSV files are still named with `buzzer` and `force`, even though `buzzer` now represents the light-calibration voltage channel.

Expected naming examples:

- `dan.buzzer1.csv`
- `dan.force1.csv`
- `raziel.buzzer3c.csv`
- `raziel.force3c.csv`
- `arel.buzzer1c.csv`
- `arel.force1c.csv`

Expected CSV structure:

- First column: timestamp.
- Second column: voltage ratio.
- Typical headers:
  - `Date,Voltage Ratio [Bridge Input Ch:1 -> ...]`
  - `Date,Voltage Ratio [Bridge Input Ch:3 -> ...]`
- `buzzer` file: calibration/light voltage channel, observed on Ch1.
- `force` file: force channel, observed on Ch3.

Important timing facts:

- The `buzzer` and `force` CSVs are recorded by the same device and share the same timestamp clock.
- Their file save/start times may differ slightly because they are separate channels/files.
- The timestamp values, not file start offsets, define alignment.
- Observed sample interval in provided CSVs is about 0.008 seconds, roughly 125 Hz.

## 3. File Ordering and Pairing

CSV files must be paired by filename identity:

- `name.buzzerN.csv` pairs with `name.forceN.csv`.
- Minor suffixes such as `3c` should be preserved as part of the pair identity.

Segment order must be determined by original file save/modified time, not alphabetical filename order.

This is important because alphabetical order can place later tests before earlier tests. In one real run, alphabetical sorting placed `arel...` before `raziel...`, but the video segment duration matched `raziel3c`. This produced a false warning of a 7.113 second video/CSV mismatch. Ordering by original modified time corrected the interpretation.

Functional requirement:

- The app should support selecting a local CSV folder so original modified times are preserved.
- Browser CSV upload is acceptable, but it may not preserve original Windows modified times reliably.
- If upload is used, the app should warn that chronological order may be less reliable.

## 4. CSV Pulse Detection Requirements

For each `buzzer` CSV:

1. Find the first calibration pulse.
2. Mark the first row where the voltage is between `0.002` and `0.0025`.
3. Continue forward until the signal drops below the valid range/low threshold.
4. Then search for the next pulse in the same way.
5. The first pulse start is the force segment start.
6. The second pulse start is the force segment end.

Default voltage band:

- Low: `0.002`
- High: `0.0025`

Observed CSV examples:

- `dan.buzzer1.csv`: two detected pulses, CSV segment duration about `34.416s`.
- `raziel.buzzer3c.csv`: two detected pulses, CSV segment duration about `31.400s`.

Warnings should be shown when:

- Fewer than two pulses are found.
- Pulse order is invalid.
- CSV duration is outside the expected broad range, usually 20 to 60 seconds.
- CSV timestamps have large gaps, duplicate timestamps, or decreasing timestamps.

## 5. Video Pre-Trim Requirement

Before choosing the light ROI, the user must be able to set a start time for each video.

Reason:

- Long recordings often include several minutes of setup before testing starts.
- Scanning this whole section is slow and increases the chance of false detections from sunlight/reflections.

Functional behavior:

- Show a start-trim page after upload/folder selection and CSV parsing.
- For each video, allow the user to enter a start time in seconds.
- Allow applying the same start time to all videos.
- Provide lightweight frame preview at a chosen timestamp.
- Do not load full multi-GB videos automatically on this page.
- Only load a video player when the user explicitly requests it.
- The start trim is non-destructive: the original video is not rewritten.
- ROI frame extraction and light-event scanning begin at the chosen start time.
- Export still cuts from the original video timeline using the detected event times.

## 6. ROI Selection Requirements

The user must mark a small rectangle around the physical light for each camera.

ROI requirements:

- One ROI per camera.
- Cameras are assumed static, so one ROI per full video is sufficient.
- The red ROI rectangle must be movable and resizable.
- The user should be able to:
  - draw a new rectangle,
  - drag inside the rectangle to move it,
  - drag corners to resize it.

ROI guidance:

- The ROI should be tight around the light.
- It should include the visible illuminated lamp area, not broad surrounding pool floor.
- It may include a small margin for tiny movement/reflection.
- A bad ROI can completely prevent detection, especially in cameras where the light is small or partially obscured.

## 7. Light Detection Requirements

### 7.0 CSV-Timeline Guided Search

The primary detection algorithm should not scan the entire long video. It should use the CSV light-channel timestamps as a timeline guide.

Algorithm:

1. Build a chronological list of CSV flash times:
   - segment 1 start flash,
   - segment 1 finish flash,
   - segment 2 start flash,
   - segment 2 finish flash,
   - and so on.
2. The first visible video flash is the anchor.
   - The video should be trimmed close to the first flash.
   - Search only a short initial window after the chosen video start, for example 20 seconds.
3. Once the first video flash is found, map CSV time to video time:

   `predicted_video_time = anchor_video_time + (csv_flash_time - anchor_csv_flash_time)`

4. For every later CSV flash, search only a small video window around the prediction, for example `±2s`.
5. Odd-numbered flashes are segment starts.
6. Even-numbered flashes are segment finishes.
7. Pair flash 1/2 to segment 1, flash 3/4 to segment 2, and continue in order.

This approach avoids full-video scanning and reduces false positives from sunlight, swimmers, and reflections because the detector only looks where the CSV clock says a flash should exist.

### 7.1 Core Problem

The light is not detected reliably by raw brightness alone.

Real observations:

- Both "off" and "on" screenshots can contain saturated pixels with value `255`.
- Therefore, max brightness is useless as a classifier.
- Sun angle changes during filming.
- Pool-floor caustics and reflections can become brighter or darker over time.
- Absolute brightness can drift substantially even when the light state does not change.

The detector must classify the light based on the difference between the selected light ROI and its local surrounding background.

### 7.2 Required Detection Strategy

For each sampled frame:

1. Crop the user-selected ROI.
2. Build a surrounding background ring around the ROI.
3. Measure the high-brightness portion of the ROI.
4. Measure the high-brightness portion of the surrounding ring.
5. Use local contrast:

   `score = top_brightness(ROI) - top_brightness(surrounding ring)`

This subtracts slow sunlight drift and local pool-floor brightness changes.

The current practical score is:

- Top 10% mean HSV value inside the ROI.
- Minus top 10% mean HSV value in the surrounding ring.

### 7.3 Thresholding

A global absolute brightness threshold is not acceptable.

The threshold should be adaptive per video and ROI:

- Compute a baseline from the score distribution.
- Compute a high/peak score from the upper tail.
- Set threshold between baseline and high score.

Current working approach:

`threshold = baseline + 0.60 * (high - baseline)`

Important lesson:

- Do not add a robust-sigma term that can push the threshold above the observed peak.
- In a real CamB/CamD run, the calculated threshold exceeded the highest score, making zero detections mathematically guaranteed.

### 7.4 Event Extraction

After thresholding:

- Consecutive active frames belong to the same light event.
- Small gaps should be merged.
- Very short activations should be ignored.
- Events should be separated by a minimum time gap to avoid splitting one flash into many events.

Suggested defaults:

- Coarse scan rate: about 12 fps.
- Merge gap: about 0.55 seconds.
- Minimum on duration: about 0.20 seconds.
- Minimum event separation: about 5 seconds.

After a coarse event is found:

- Re-scan/refine around that time at frame-level resolution.
- Report the first frame where the light turns on as the event time.

## 8. Differences Between Cameras

Each camera sees the light differently.

Observed differences:

- The light appears in different locations in each frame.
- The light can be much smaller in some cameras.
- It may be partially blocked by equipment, swimmer motion, lane lines, or reflections.
- Different camera angles see different amounts of sunlight drift.
- The same physical flash can have strong contrast in CamA and weak contrast in CamB/CamD.
- A threshold that works for one camera may fail for another camera.

Functional implications:

- Each camera must have its own ROI.
- Each camera must have its own adaptive threshold.
- Each camera must report its own confidence/warnings.
- The app must not assume that all cameras detect the same number of events.
- Review must show which cameras matched and which failed.

When a camera fails:

- Show "No light events detected in this ROI."
- Show the ROI diagnostics if available: baseline, high score, peak score, threshold, number of sampled frames.
- Let the user return to ROI selection and adjust the rectangle.

## 9. Segment Matching Requirements

The app must not blindly assign video events as:

- event 1 and 2 = segment 1,
- event 3 and 4 = segment 2,
- etc.

This fails when:

- A false light/reflection event is detected.
- Some cameras miss an event.
- CSV files are out of alphabetical order.
- The first detected video pair actually belongs to a later CSV segment.

Required behavior:

- Detect candidate video event pairs.
- Compute each pair duration.
- Compare candidate durations against CSV segment durations.
- Assign video event pairs to CSV segments by closest duration match.
- Prevent one event pair from being reused by multiple segments.
- Report when extra events were skipped.

Example real conclusion:

- A detected CamA pair had duration `31.415s`.
- It was incorrectly assigned to a CSV segment with duration `38.528s`, creating a `7.113s` warning.
- The same video pair correctly matched `raziel3c`, whose CSV duration was `31.400s`, with only about `0.015s` difference.

## 10. Review Page Requirements

Before export, the app must show a review page with all segments.

For each segment:

- Segment label.
- Force CSV filename.
- Buzzer/light CSV filename.
- CSV start timestamp.
- CSV end timestamp.
- CSV duration.
- For every video:
  - start time,
  - end time,
  - duration,
  - frame numbers,
  - difference between video duration and CSV duration,
  - warning status.
- Short preview clip if available.
- Confidence/warning status.

The review page must also allow manual frame correction.

For each segment and camera:

- Show a 6-second video window around the detected/predicted start flash.
- Show a 6-second video window around the detected/predicted finish flash.
- Provide previous-frame and next-frame controls for each window.
- Provide a set-frame button for each start and finish window.
- When the user sets a frame, update the original-video start or finish timestamp.
- Recompute:
  - start frame,
  - finish frame,
  - video duration,
  - difference from CSV duration,
  - warning status.
- Export must use the manually corrected times.

Warnings should include:

- No light events detected.
- No event pair matched this segment.
- Video/CSV duration mismatch above tolerance.
- Skipped extra events while matching.
- CSV pulse detection problems.
- Suspicious timestamp gaps.

The review page must not crash if ROI analysis is incomplete or missing for a camera.

## 11. Export Requirements

Export should produce a zip file containing, for each matched segment:

- Trimmed video clips for each successful camera.
- Trimmed force CSV.
- Trimmed buzzer/light CSV.
- Segment metadata JSON.

Video export must be frame-accurate:

- Use ffmpeg.
- Re-encode instead of stream copy.
- Use explicit duration.

Required ffmpeg style:

```text
ffmpeg -y -ss <start> -i <input> -t <duration> \
  -c:v libx264 -preset veryfast -crf 18 \
  -c:a aac -movflags +faststart <output>
```

Do not use `-c copy` for precision cutting, because keyframe-only cutting can shift starts and lengths.

CSV export:

- Use timestamp mask from CSV pulse start to CSV pulse end.
- Force and buzzer/light CSVs are cut using their shared timestamp clock.

## 12. Performance Requirements

Large files must be handled carefully.

Requirements:

- Prefer local video folder paths over browser video upload.
- Do not copy multi-GB videos into the job directory when local folder mode is used.
- Do not automatically embed all long videos as HTML video players.
- Use lightweight frame previews on setup pages.
- Load a video player only when the user explicitly requests playback.
- Start scanning from the user-selected trim start.
- Use coarse-to-fine video scanning:
  - coarse scan at reduced fps,
  - then frame-level refinement around candidate events.

Known performance issue:

- Browser video upload copies full MP4 files into the job folder.
- If the workspace is inside OneDrive, OneDrive may watch/sync temporary large files and slow things further.
- OpenCV seeking into multi-GB MP4s can be slow, especially after many minutes of video.

## 13. Data Conclusions So Far

Conclusions from the provided data:

- CSV layout is consistent: `Date` plus voltage-ratio channel.
- `buzzer` filenames should remain supported, but semantically they now mean light calibration.
- CSV timestamps are reliable for alignment.
- CSV sample interval in examples is about 8 ms.
- The voltage pulse range `0.002` to `0.0025` works on provided examples.
- Segment durations are usually around 30-40 seconds.
- Raw max pixel brightness is not a valid light-on classifier.
- Local contrast around the ROI is significantly more reliable.
- The same physical light can produce very different visual evidence across camera angles.
- Modified-time ordering is critical for multi-segment CSV lists.
- Review warnings are necessary because some cameras may fail while others succeed.

## 14. Required User Workflow

1. Select a local video folder containing 1-5 MP4 files.
2. Select a local CSV folder containing all `buzzer` and `force` CSVs.
3. Confirm detected CSV pairs and ordering.
4. Set video start time for each camera to skip pre-test footage.
5. Mark the light ROI for each camera.
6. Run light detection.
7. Review all segment matches, durations, warnings, and previews.
8. If needed, return to ROI selection and adjust failed cameras.
9. Export aligned clips and CSVs.

## 15. Acceptance Criteria

The system is acceptable when:

- It pairs `buzzer` and `force` CSVs correctly.
- It orders CSV segments chronologically by original modified time.
- It detects two CSV pulses per segment using the voltage band.
- It allows per-camera start trimming.
- It allows per-camera movable/resizable ROI selection.
- It detects light-on events despite slow sunlight drift.
- It matches video event pairs to CSV segments by duration.
- It clearly reports failed cameras or suspicious mismatches.
- It exports video clips and CSV files with matching segment durations.
