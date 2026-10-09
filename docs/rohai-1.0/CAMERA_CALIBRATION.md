# Smart Vision & Calibration

## Live camera workflow

Use the existing MAKET simple console. Discovery probes cameras sequentially, releases
each candidate, and selects automatically only when one camera is available. Candidate
previews are labelled snapshots. A selected built-in/USB camera has one capture process;
UI, inference and calibration consume its shared latest-frame stream, never another reader.
Advanced setup supports width, height and requested FPS; changing these selects CUSTOM.
Driver acceptance of FPS/resolution must be checked from measured frames, not assumed.

The capture process publishes one shared-memory frame. Bounded latest slots separate
capture, YOLO/ByteTrack, analytics and JPEG publishing. Short driver interruptions close
the old process and resources before bounded reopen. Each reopening changes stream epoch,
resets tracking and requires operator pose revalidation before resumed control. Repeated
open-success/read-failure cycles also exhaust the retry budget. Physical outputs never
automatically rearm after video recovery. A stale inference falls back to fresh raw preview.

## Calibration hypotheses and confirmation

On first live setup without calibration, the simple console attempts an image-edge proposal.
Canny/Hough identifies candidate horizontal/vertical boundaries; a template is fitted to
them. This is an **image-edge plus template hypothesis**, not semantic road recognition.
Missing or inconsistent boundaries produce an explicit assisted fallback, not invented ROI.
Confidence is null: no calibrated road-detection probability has been measured.

For perspective/rotated/custom views, select **Perspective: 4 corners**. Mark outer road-view
corners clockwise (top-left, top-right, bottom-right, bottom-left), then apply. A validated
projective transform maps a seed intersection into that quadrilateral. Direction names,
lane subdivisions, pedestrian areas and count lines remain hypotheses requiring review.
The projective seed is not a centimeter homography; physical dimensions/scale still need
explicit measurement. Queue length is otherwise reported in image pixels.

Drag vertices or draw the incorrect zone/line, verify orientation and confirm the live layout.
No suggestion automatically saves, arms outputs or becomes a control ROI. Lines require
start/end/upstream anchors; polygons must be normalized, nondegenerate and nonintersecting.
Perspective anchors must form a convex quadrilateral. Source-resolution metadata is retained.

Display conventions: actual visible YOLO tracks use solid boxes; saved validated ROI uses
solid geometry when enabled; editor hypotheses use dashed geometry and an explicit label.
Counting-line/trajectory diagnostics require Debug Overlay or the calibration editor.
Presentation stays clean. Ordinary backend JPEGs now show actual frames by default;
the legacy rendered debug panel requires `STA_VISION__DEBUG_OVERLAY=true`.

## Saved calibration validity

Saving requires a fresh connected frame, matching source/dimensions and explicit operator
pose confirmation. The backend supplies the actual reference hash and camera signature.
Signature binds source ID/type/index, backend, URI, resolution, rotation, mirror and crop.
Requested FPS alone does not invalidate geometry. Camera index is not a permanent hardware
serial identity; scene alignment remains necessary when devices are swapped.

Reference and calibration JSON are individually atomic replacements; JSON commits last.
An interruption between writes fails closed on reference hash mismatch. Old unsigned
calibrations require renewed save/confirmation. Resolution/transform/source mismatches,
stale/moved/insufficient scene alignment and recovered streams block control. Existing ORB
static-scene verification remains: a plain board may need stable textured/fiducial surroundings.
Insufficient features are not silently accepted as alignment success.

## Frame and metric provenance

MAKET `vision_state` provides frame/media timestamp, captured monotonic time, camera ID,
frame ID/epoch/size, calibration version, actual detections/tracks, track-to-lane assignment,
moving/stopped/occluded counts, queue/wait estimates, confidence, age and pipeline health.
Unconfirmed or stale calibrated metrics are unavailable, not fabricated zeroes. Existing
SafetySupervisor -> HardwareApplicationService -> ESP32 remains the only physical chain.

`/api/maket/frame?analyzed=true` returns a fresh processed frame where available, with
frame/epoch headers. UI draws boxes only when telemetry matches that exact frame/size/epoch.
Raw fallback remains visible during slow inference and carries no mismatched old boxes.
