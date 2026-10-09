"""Live stand loop: physical outputs only, independent monotonic control cadence."""

import asyncio
import hashlib
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from typing import Any

import cv2
import numpy as np
from traffic_core.models import Decision, ObservationStatus, SignalStage
from traffic_simulator.learning.safety import SafetySupervisor
from traffic_simulator.twin_models import ObservedTrafficState
from traffic_vision.detector import CalibrationDetector, Detector, YOLODetector
from traffic_vision.geometry import IntersectionGeometry, load_geometry
from traffic_vision.manifest import ModelManifest
from traffic_vision.pipeline import VisionPipeline
from traffic_vision.sources import create_source

from smart_traffic_backend.hardware_service import HardwareApplicationService
from smart_traffic_backend.maket.analytics import MaketAnalyzer
from smart_traffic_backend.maket.calibration import camera_signature
from smart_traffic_backend.maket.config import MaketCalibration, MaketConfig
from smart_traffic_backend.maket.discovery import camera_indices, probe_camera
from smart_traffic_backend.maket.policy import CameraPolicy, request
from smart_traffic_backend.maket.serial import Esp32TrafficLightController, signal_name
from smart_traffic_backend.maket.session import SessionRecorder
from smart_traffic_backend.maket.vision_state import estimate


class MaketRuntime:
    def __init__(self, config: MaketConfig) -> None:
        self.now = 0.0
        self.config = config
        self.hardware = Esp32TrafficLightController(config.hardware)
        self.hardware_service = HardwareApplicationService(
            self.hardware, config.timing, physical=True
        )
        self.supervisor = SafetySupervisor(config.timing)
        self.learned_error: str | None = None
        try:
            self.policy = CameraPolicy(self.supervisor, config.learned_package)
        except Exception as error:
            self.learned_error = str(error)
            self.policy = CameraPolicy(self.supervisor, None)
        self.baseline = CameraPolicy(self.supervisor, None)
        self.calibration: MaketCalibration | None = None
        if config.calibration_path.exists():
            self.calibration = MaketCalibration.model_validate_json(
                config.calibration_path.read_text(encoding="utf-8")
            )
        geometry = self._camera_geometry()
        self.model_error: str | None = None
        self.model_id: str | None = None
        detector: Detector
        try:
            # Package/registry resolution is still performed and verified by YOLODetector.
            settings = config.camera
            if settings.model_registry:
                from traffic_vision.training.registry import ModelRegistry

                settings = settings.model_copy(
                    update={
                        "model_package": ModelRegistry(settings.model_registry).production(),
                        "model_registry": None,
                    }
                )
            if settings.model_package:
                from traffic_vision.training.package import verify_package

                verify_package(settings.model_package)
                settings = settings.model_copy(
                    update={
                        "manifest_path": settings.model_package / "model.json",
                        "model_path": settings.model_package / "weights.pt",
                    }
                )
            manifest = ModelManifest.load(settings.manifest_path)
            if manifest.deployment_domain != settings.deployment_domain:
                raise ValueError("Manifest deployment domain mismatch")
            if config.vision_profile == "COCO_BASELINE" and (
                manifest.model_id != "ultralytics-yolo26n-coco"
                or manifest.weights_hash
                != "9b09cc8bf347f0fc8a5f7657480587f25db09b34bf33b0652110fb03a8ad4fef"
            ):
                raise ValueError("Pinned COCO baseline identity mismatch")
            manifest.validate_model(
                manifest.class_id_mapping, settings.model_path, settings.image_size
            )
            self.model_id = manifest.weights_hash
            detector = YOLODetector(settings)
        except (ValueError, OSError) as error:
            # Camera-only commissioning; no fake detections and never arm outputs.
            self.model_error = str(error)
            detector = CalibrationDetector()
        self.pipeline = VisionPipeline(
            create_source(config.camera),
            detector,
            MaketAnalyzer(geometry, config.camera.analytics, self.calibration),
            config.camera,
        )
        self.pipeline.signal_provider = lambda: self.supervisor.machine.snapshot(self.now)
        self.recorder = SessionRecorder(config)
        self.hardware.event_sink = self.recorder.submit
        analyzer = self.pipeline.analyzer
        assert isinstance(analyzer, MaketAnalyzer)
        analyzer.event_sink = lambda event: self.recorder.submit("traffic_event", event)
        self.state = "SAFE"
        self.policy_mode = "SHADOW"
        self.running = False
        self.demo_running = False
        self.camera_error: str | None = None
        self.revalidation_required = False
        self.hardware_test = False
        self.failure: str | None = None
        self.manual: Decision | None = None
        self.latest: dict[str, Any] = {}
        self.proposed: Decision | None = None
        self.policy_error: str | None = None
        self.started = time.monotonic()
        self.now = 0.0
        self.task: asyncio.Task[None] | None = None
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="maket-policy")
        self.pending: asyncio.Future[Decision] | None = None
        self.latencies: dict[str, deque[float]] = {
            k: deque(maxlen=300)
            for k in (
                "policy",
                "hardware_ack",
                "camera_to_ack",
                "control_interval",
            )
        }
        self._last_tick: float | None = None
        self.alignment: str = "NOT_CHECKED"
        self.alignment_updated_at: float | None = None
        self._alignment_frame = -1
        self._last_preview_frame = -1
        self._last_recorded_frame = -1
        self._alignment_task: asyncio.Task[None] | None = None
        self.metrics: dict[str, Any] = {}
        self.last_decision_id: str | None = None
        self._camera_epoch: int | None = None
        self.physical_permission_until: float | None = None
        self.setup_state: dict[str, Any] = {"state": "NOT_STARTED", "issues": []}

    async def start(self) -> None:
        if self.config.output_mode == "PHYSICAL_OUTPUT":
            try:
                await self.hardware.connect()
            except (OSError, ValueError, RuntimeError, TimeoutError) as error:
                self.failure = f"hardware_disconnected: {error}"
                self.hardware.fault = self.failure
                await self.hardware.close()
        await self._start_camera()
        self.task = asyncio.create_task(self._run(), name="physical-maket-control")

    async def _start_camera(self) -> None:
        if not self.config.camera_selected:
            self.camera_connection = "NOT_SELECTED"
            return
        if self.config.camera.source == "webcam":
            discovered = await camera_indices(indices=(self.config.camera.webcam_index,))
            if not discovered[0]["available"]:
                self.camera_connection = "DISCONNECTED"
                return
        if self.config.camera.source in ("builtin_camera", "usb_camera"):
            result = await asyncio.to_thread(
                probe_camera, self.config.camera.webcam_index, self.config.camera.camera_backend
            )
            if not result.get("available"):
                self.camera_error = result.get("error", "CAMERA_NOT_FOUND")
                self.camera_connection = "DISCONNECTED"
                return
            self.camera_error = None
            settings = self.config.camera.model_copy(update={"camera_backend": result["backend"]})
            self.config = self.config.model_copy(update={"camera": settings})
            self.pipeline.settings = settings
            self.pipeline.source = create_source(settings)
            self.camera_metadata = {k: v for k, v in result.items() if k != "preview"}
        self.camera_connection = "STARTING"
        self.pipeline.start()

    def calibration_check(self) -> bool:
        analysis = self.pipeline.latest_analysis
        c = self.calibration
        if (
            analysis is None
            or c is None
            or not c.camera_pose_verified
            or self.revalidation_required
        ):
            return False
        capture = self.pipeline.latest_capture
        if capture is not None and capture.frame.shape[:2] != (
            c.geometry.reference_height,
            c.geometry.reference_width,
        ):
            self.alignment = "INVALID_ID_OR_RESOLUTION"
            return False  # Do not wait for inference to notice a resolution change.
        h, w = analysis.video.frame.shape[:2]
        if (w, h) != (c.geometry.reference_width, c.geometry.reference_height) or (
            c.geometry.source_id != self.config.camera.source_id
        ):
            self.alignment = "INVALID_ID_OR_RESOLUTION"
            return False
        if isinstance(c, MaketCalibration) and c.camera_signature != camera_signature(
            self.config.camera
        ):
            self.alignment = "INVALID_CAMERA_SETTINGS"
            return False
        return (
            self.alignment == "VALID"
            and self.alignment_updated_at is not None
            and (time.monotonic() - self.alignment_updated_at < 1.5)
        )

    def _alignment_check(self) -> None:
        a = self.pipeline.latest_analysis
        c = self.calibration
        if a is None or c is None or a.video.frame_id == self._alignment_frame:
            return
        self._alignment_frame = a.video.frame_id
        self.alignment_updated_at = time.monotonic()
        reference = self.config.reference_image_path
        if not reference.exists() or hashlib.sha256(reference.read_bytes()).hexdigest() != (
            c.reference_image_sha256
        ):
            self.alignment = "INVALID_REFERENCE"
            return
        ref = cv2.imread(str(reference), cv2.IMREAD_GRAYSCALE)
        current = cv2.cvtColor(a.video.frame, cv2.COLOR_BGR2GRAY)
        if ref is None or ref.shape != current.shape:
            self.alignment = "INVALID_RESOLUTION"
            return
        # Exclude approach/vehicle regions; register static surroundings only.
        mask = np.full(current.shape, 255, dtype=np.uint8)
        h, w = current.shape[:2]
        for approach in c.geometry.approaches:
            points = np.array([(int(p.x * w), int(p.y * h)) for p in approach.incoming.points])
            cv2.fillPoly(mask, [points], 0)
        orb = cv2.ORB_create(nfeatures=500)  # type: ignore[attr-defined]
        k1, d1 = orb.detectAndCompute(ref, mask)
        k2, d2 = orb.detectAndCompute(current, mask)
        if d1 is None or d2 is None:
            self.alignment = "SUSPECT_INSUFFICIENT_STATIC_FEATURES"
            return
        pairs = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(d1, d2, k=2)
        good = [
            pair[0]
            for pair in pairs
            if len(pair) == 2 and pair[0].distance < 0.7 * pair[1].distance
        ]
        if len(good) < 12:
            self.alignment = "SUSPECT_INSUFFICIENT_STATIC_FEATURES"
            return
        first = np.asarray([k1[m.queryIdx].pt for m in good], dtype=np.float32)
        second = np.asarray([k2[m.trainIdx].pt for m in good], dtype=np.float32)
        matrix, inliers = cv2.estimateAffinePartial2D(first, second, method=cv2.RANSAC)
        if matrix is None or inliers is None or int(inliers.sum()) < 10:
            self.alignment = "SUSPECT_ALIGNMENT"
            return
        corners = np.asarray([(0, 0), (w, 0), (w, h), (0, h)], dtype=np.float32).reshape(1, 4, 2)
        shifted = cv2.transform(corners, matrix)
        error = float(np.max(np.linalg.norm(shifted - corners, axis=2)))
        self.alignment = "VALID" if error <= c.max_alignment_shift_px else "SUSPECT_CAMERA_MOVED"

    def preflight(self) -> dict[str, bool]:
        observation = self.pipeline.observation()
        a = self.pipeline.latest_analysis
        ack = self.hardware.ack or {}
        fresh_ack = self.hardware.last_heartbeat is not None and (
            time.time() - self.hardware.last_heartbeat < 1
        )
        timing = self.config.timing
        firmware_timing = all(
            ack.get(key) == round(value * 1000)
            for key, value in (
                ("min_green_ms", timing.min_green_seconds),
                ("max_green_ms", timing.max_green_seconds),
                ("yellow_ms", timing.yellow_seconds),
                ("all_red_ms", timing.all_red_seconds),
                ("ped_green_ms", timing.pedestrian_green_seconds),
                ("ped_clearance_ms", timing.pedestrian_clearance_seconds),
            )
        )
        return {
            "camera_healthy": self.pipeline.source.connected
            and self.pipeline.status().state == "running",
            "model_compatible": self.model_id is not None and self.model_error is None,
            "calibration_valid": self.calibration_check(),
            "calibration_complete": self.calibration is not None
            and all(
                a.enabled
                and a.entry_line is not None
                and a.exit_line is not None
                and bool(a.lanes)
                and a.waiting_zone is not None
                and a.crosswalk_zone is not None
                and a.exit_zone is not None
                for a in self.calibration.geometry.approaches
            ),
            "analytics_healthy": observation is not None
            and all(m.observation.reliable for m in observation.traffic.directions.values()),
            "controller_loaded": self.policy.name != "LEARNED_AI" or self.policy.model is not None,
            "safety_healthy": not self.supervisor.machine.faulted,
            "esp32_connected": self.hardware.connected and fresh_ack,
            "watchdog_healthy": ack.get("watchdog_present") is True
            and not ack.get("sense_fault", True),
            "lamp_states_readable": ack.get("sense_mask") == ack.get("output_mask")
            and (isinstance(ack.get("sense_mask"), int)),
            "no_signal_conflict": ack.get("actual_phase")
            in ("ALL_RED", "NS_GREEN", "NS_YELLOW", "EW_GREEN", "EW_YELLOW", "PED_GREEN"),
            "observation_fresh": a is not None
            and time.monotonic() - a.captured_at < self.config.camera.max_frame_age_seconds,
            "firmware_timing_matches": firmware_timing,
            "emergency_stop_released": ack.get("estop") is False,
            "independent_protection_verified": self.config.hardware.independent_protection_verified,
            "learned_release_approved": self.policy.name != "LEARNED_AI"
            or self.policy_mode != "ACTIVE"
            or (self.policy.active_approved and self.config.vision_profile == "MAKET_MODEL"),
        }

    async def arm(self) -> None:
        if self.config.output_mode != "PHYSICAL_OUTPUT":
            raise ValueError("CAMERA_ONLY forbids hardware ARM; select physical output explicitly")
        if self.running:
            raise ValueError("Stop before rearm")
        if self.pending is not None and not self.pending.done():
            raise ValueError("Policy worker still running; wait or restart before rearm")
        # Explicit recovery creates a fresh host supervisor while device remains latched.
        # No outputs are enabled until ALL renewed checks and local device ARM succeed.
        if self.supervisor.machine.faulted:
            self.supervisor = SafetySupervisor(self.config.timing)
            self.policy.supervisor = self.baseline.supervisor = self.supervisor
            self.state = "RECOVERING"
        checks = self.preflight()
        failed = [k for k, v in checks.items() if not v]
        if failed:
            raise ValueError("Preflight failed: " + ", ".join(failed))
        # Fault reset requires explicit operator action AND the device local button.
        await self.hardware.command("ARM")
        self.hardware_service.adapter = self.hardware
        self.hardware_service.reset()
        self.supervisor = SafetySupervisor(self.config.timing)
        self.started = time.monotonic()
        self.policy.supervisor = self.baseline.supervisor = self.supervisor
        self.policy.stack.frames.clear()
        self.baseline.stack.frames.clear()
        self.running, self.state, self.failure = True, "HEALTHY", None

    async def emergency_stop(self, reason: str = "operator_emergency_stop") -> None:
        if reason == "camera_demo_readiness_lost_revalidate_before_restart":
            self.revalidation_required = True
        self.demo_running = False
        self.running = False
        self.hardware_test = False
        self.manual = None
        self.policy_mode = "SHADOW"
        self.state, self.failure = "FAULT", reason
        self.supervisor.machine.fail_safe(self.now)
        if self.config.output_mode == "CAMERA_ONLY":
            self.state = "STOPPED"
            self.failure = None if reason == "operator_emergency_stop" else reason
            self.recorder.submit("demo_stop", {"reason": reason, "physical_commands": False})
            return
        try:
            await self.hardware_service.enter_safe()
            self.state = "SAFE"
        except Exception as error:
            self.failure = f"{reason}; stop ACK unavailable: {error}"
        self.recorder.submit(
            "emergency_stop", {"reason": self.failure, "hardware": self.hardware.snapshot()}
        )

    async def reconnect(self) -> None:
        if self.config.output_mode != "PHYSICAL_OUTPUT":
            raise ValueError("CAMERA_ONLY has no serial connection; select physical output first")
        if self.running:
            raise ValueError("Stop before reconnect")
        await self.hardware.close()
        self.hardware = Esp32TrafficLightController(self.config.hardware)
        self.hardware.event_sink = self.recorder.submit
        self.hardware_service = HardwareApplicationService(
            self.hardware, self.config.timing, physical=True
        )
        await self.hardware.connect()
        self.state = "READY_FOR_OPERATOR_CONFIRMATION"
        self.policy_mode = "SHADOW"

    async def arm_hardware_test(self) -> None:
        if self.config.output_mode != "PHYSICAL_OUTPUT":
            raise ValueError("Hardware Test requires explicit PHYSICAL_OUTPUT mode")
        if self.running:
            raise ValueError("Stop before Hardware Test")
        if self.pending is not None and not self.pending.done():
            raise ValueError("Policy worker busy; wait before Hardware Test")
        checks = self.preflight()
        required = (
            "esp32_connected",
            "watchdog_healthy",
            "lamp_states_readable",
            "no_signal_conflict",
            "firmware_timing_matches",
            "emergency_stop_released",
            "independent_protection_verified",
        )
        failed = [key for key in required if not checks[key]]
        if failed:
            raise ValueError("Hardware preflight failed: " + ", ".join(failed))
        await self.hardware.command("ARM")
        self.hardware_service.adapter = self.hardware
        self.hardware_service.reset()
        self.supervisor = SafetySupervisor(self.config.timing)
        self.policy.supervisor = self.baseline.supervisor = self.supervisor
        self.started = time.monotonic()
        self.running, self.hardware_test = True, True
        self.state, self.failure, self.manual = "HARDWARE_TEST", None, None
        self.policy_mode = "SHADOW"

    def _camera_geometry(self) -> IntersectionGeometry:
        if self.calibration is not None:
            if self.calibration.geometry.source_id == self.config.camera.source_id:
                return self.calibration.geometry
            # Preserve the old file, but never apply another camera's verified profile.
            self.calibration = None
        draft = load_geometry(self.config.camera.geometry_path)
        return draft.model_copy(
            update={
                "source_id": self.config.camera.source_id,
                "calibration_validated_at": None,
            }
        )

    async def reconnect_camera(self) -> None:
        if self.running or self.demo_running:
            raise ValueError("Stop before camera restart")
        if self._alignment_task is not None and not self._alignment_task.done():
            async with asyncio.timeout(0.5):
                await asyncio.shield(self._alignment_task)
        await asyncio.to_thread(self.pipeline.stop)
        if self.config.calibration_path.exists():
            self.calibration = MaketCalibration.model_validate_json(
                await asyncio.to_thread(self.config.calibration_path.read_text, encoding="utf-8")
            )
        geometry = self._camera_geometry()
        detector = self.pipeline.detector
        self.pipeline = VisionPipeline(
            create_source(self.config.camera),
            detector,
            MaketAnalyzer(geometry, self.config.camera.analytics, self.calibration),
            self.config.camera,
        )
        self.pipeline.signal_provider = lambda: self.supervisor.machine.snapshot(self.now)
        analyzer = self.pipeline.analyzer
        assert isinstance(analyzer, MaketAnalyzer)
        analyzer.event_sink = lambda event: self.recorder.submit("traffic_event", event)
        self.alignment = "NOT_CHECKED"
        self.revalidation_required = True
        self.alignment_updated_at = None
        self._alignment_frame = -1
        await self._start_camera()
        self.policy_mode = "SHADOW"
        self.state = "RECOVERING"

    async def revalidate_camera(self) -> None:
        if self.running or self.demo_running:
            raise ValueError("Stop before explicit camera pose revalidation")
        self.revalidation_required = False
        if not self.calibration_check() or not self.preflight()["observation_fresh"]:
            self.revalidation_required = True
            raise ValueError(
                "Fresh matching camera/resolution/alignment required; recalibrate if moved"
            )
        self.recorder.submit(
            "operator_camera_revalidation",
            {"source_id": self.config.camera.source_id, "alignment": self.alignment},
        )

    def readiness(self) -> dict[str, bool]:
        checks = self.preflight()
        vision = (
            checks["camera_healthy"] and checks["model_compatible"] and checks["observation_fresh"]
        )
        control = vision and all(
            checks[k]
            for k in (
                "calibration_valid",
                "calibration_complete",
                "analytics_healthy",
                "controller_loaded",
            )
        )
        hardware = all(
            checks[k]
            for k in (
                "esp32_connected",
                "watchdog_healthy",
                "lamp_states_readable",
                "no_signal_conflict",
                "firmware_timing_matches",
                "emergency_stop_released",
                "independent_protection_verified",
            )
        )
        return {"VISION_READY": vision, "CONTROL_READY": control, "HARDWARE_READY": hardware}

    async def start_camera_only(self) -> None:
        if self.config.output_mode != "CAMERA_ONLY" or self.running:
            raise ValueError("Select CAMERA_ONLY while stopped before starting shadow demo")
        if self.pending is not None and not self.pending.done():
            raise ValueError("Policy worker still busy; restart/wait before demo")
        if not self.readiness()["CONTROL_READY"]:
            raise ValueError(
                "Camera demo needs fresh vision and revalidated complete calibration/analytics"
            )
        self.supervisor = SafetySupervisor(self.config.timing)
        self.policy.supervisor = self.baseline.supervisor = self.supervisor
        self.policy.stack.frames.clear()
        self.baseline.stack.frames.clear()
        self.policy_mode = "SHADOW"
        self.demo_running, self.failure, self.state = True, None, "CAMERA_ONLY"

    async def _shadow_tick(self) -> None:
        if not self.readiness()["CONTROL_READY"]:
            await self.emergency_stop("camera_demo_readiness_lost_revalidate_before_restart")
            return
        observation = self.pipeline.observation()
        assert observation is not None
        traffic = self.pipeline.analyzer.to_core_state(observation).model_copy(
            update={"simulation_time": self.now}
        )
        baseline = await self.baseline.decide(traffic)
        fallback = None
        try:
            proposed = await self._decide(traffic)
        except Exception as error:
            proposed, fallback = baseline, str(error)
        if not self.demo_running:
            return  # Concurrent Stop discards late inference.
        self.proposed, self.policy_error = proposed, fallback
        virtual = self.supervisor.apply(
            request(proposed),
            ObservedTrafficState(source="live_camera_observation", traffic=traffic),
        )
        decision_id = uuid.uuid4().hex
        if self.last_decision_id:
            self.recorder.submit(
                "subsequent_outcome",
                {
                    "decision_id": self.last_decision_id,
                    "observation": traffic.model_dump(mode="json"),
                    "causal_improvement_claim": False,
                },
            )
        self.last_decision_id = decision_id
        self.recorder.submit(
            "decision",
            {
                "event_id": decision_id,
                "session_id": self.recorder.id,
                "mode": "CAMERA_ONLY",
                "observation": traffic.model_dump(mode="json"),
                "camera_ids": [self.config.camera.source_id],
                "vision_model_id": self.model_id,
                "policy_model_id": self.policy.model_id,
                "baseline_proposed_action": baseline.model_dump(mode="json"),
                "actual_baseline_action": None,
                "proposed_action": proposed.model_dump(mode="json") if fallback is None else None,
                "recommendation": proposed.model_dump(mode="json"),
                "fallback_reason": fallback,
                "safety_response": self.supervisor.outcome.__dict__,
                "virtual_signal_preview": signal_name(virtual),
                "executed_action": None,
                "hardware_ack": None,
                "physical_commands": False,
            },
        )

    async def _decide(self, traffic: Any) -> Decision:
        if self.pending is not None and not self.pending.done():
            raise TimeoutError("Previous policy worker remains busy")
        future = self.executor.submit(lambda: asyncio.run(self.policy.decide(traffic)))
        self.pending = asyncio.wrap_future(future)
        self.pending.add_done_callback(lambda f: f.exception() if not f.cancelled() else None)
        async with asyncio.timeout(self.config.policy_timeout_s):
            return Decision.model_validate(await asyncio.shield(self.pending))

    async def _run(self) -> None:
        while True:
            tick = time.monotonic()
            self.now = tick - self.started
            if self._last_tick is not None:
                self.latencies["control_interval"].append((tick - self._last_tick) * 1000)
            self._last_tick = tick
            # Alignment work runs off-loop; no inference/preview dependency.
            if int(self.now * self.config.control_hz) % round(self.config.control_hz) == 0:
                if self._alignment_task is None or self._alignment_task.done():
                    self._alignment_task = asyncio.create_task(
                        asyncio.to_thread(self._alignment_check)
                    )
                    self._alignment_task.add_done_callback(
                        lambda f: f.exception() if not f.cancelled() else None
                    )
            analysis = self.pipeline.latest_analysis
            if (
                (self.running or self.demo_running)
                and self.physical_permission_until is not None
                and time.monotonic() >= self.physical_permission_until
            ):
                await self.emergency_stop("local_permission_expired")
            capture = self.pipeline.latest_capture
            if capture is not None:
                if self._camera_epoch is not None and capture.stream_epoch != self._camera_epoch:
                    self.revalidation_required = True
                    await self.emergency_stop("camera_stream_recovered_revalidation_required")
                self._camera_epoch = capture.stream_epoch
            fallback = None
            try:
                if analysis is not None and self.calibration is not None:
                    self.metrics = getattr(self.pipeline.analyzer, "latest_metrics", {})
                if self.demo_running:
                    await self._shadow_tick()
                elif (
                    not self.running
                    and self.hardware.connected
                    and self.config.output_mode == "PHYSICAL_OUTPUT"
                ):
                    await self.hardware.command("STATUS")  # Never renew the output lease.
                elif not self.running:
                    pass
                elif self.hardware_test:
                    checks = self.preflight()
                    if not all(
                        checks[key]
                        for key in (
                            "esp32_connected",
                            "watchdog_healthy",
                            "lamp_states_readable",
                            "no_signal_conflict",
                            "firmware_timing_matches",
                            "emergency_stop_released",
                            "independent_protection_verified",
                        )
                    ):
                        raise RuntimeError("Hardware Test readiness lost")
                    previous = self.supervisor.machine.snapshot(self.now)
                    if self.manual is None:
                        signals = previous
                    else:
                        signals = self.supervisor.machine.advance(self.now, self.manual)
                    margin = 2 * (
                        1 / self.config.control_hz
                        + self.config.policy_timeout_s
                        + self.config.hardware.timeout_seconds
                        + 0.05
                    )
                    self.supervisor.machine.green_seconds = min(
                        self.supervisor.machine.green_seconds,
                        self.config.timing.max_green_seconds - margin,
                    )
                    await self.hardware_service.apply(signals)
                    if (previous.phase, previous.stage) != (signals.phase, signals.stage):
                        self.supervisor.machine.entered_at = time.monotonic() - self.started
                    self.recorder.submit(
                        "hardware_test",
                        {
                            "authority": "OPERATOR",
                            "camera_state_used": False,
                            "requested": self.manual.model_dump(mode="json")
                            if self.manual
                            else None,
                            "approved": signals.model_dump(mode="json"),
                            "hardware": self.hardware.snapshot(),
                        },
                    )
                elif not all(self.preflight().values()):
                    await self.emergency_stop("readiness_lost")
                else:
                    observation = self.pipeline.observation()
                    assert observation is not None and analysis is not None
                    traffic = self.pipeline.analyzer.to_core_state(observation).model_copy(
                        update={"simulation_time": self.now}
                    )
                    if self.last_decision_id is not None:
                        self.recorder.submit(
                            "subsequent_outcome",
                            {
                                "decision_id": self.last_decision_id,
                                "observation": traffic.model_dump(mode="json"),
                                "causal_improvement_claim": False,
                            },
                        )
                    baseline = await self.baseline.decide(traffic)
                    start = time.monotonic()
                    try:
                        proposed = await self._decide(traffic)
                    except Exception as error:
                        proposed, fallback = baseline, str(error)
                        # No late output may enable AI automatically after a failed evaluation.
                        self.policy_mode = "SHADOW"
                    self.latencies["policy"].append((time.monotonic() - start) * 1000)
                    self.policy_error = fallback
                    self.proposed = proposed if fallback is None else None
                    if not self.running or self.hardware_test:
                        # A concurrent operator E-stop wins over an in-flight evaluation.
                        continue
                    chosen = self.manual or (proposed if self.policy_mode == "ACTIVE" else baseline)
                    observed = ObservedTrafficState(
                        source="live_camera_observation", traffic=traffic
                    )
                    previous_state = self.supervisor.machine.snapshot(self.now)
                    signals = self.supervisor.apply(request(chosen), observed)
                    margin = 2 * (
                        1 / self.config.control_hz
                        + self.config.policy_timeout_s
                        + self.config.hardware.timeout_seconds
                        + 0.05
                    )
                    self.supervisor.machine.green_seconds = min(
                        self.supervisor.machine.green_seconds,
                        self.config.timing.max_green_seconds - margin,
                    )
                    await self.hardware_service.apply(signals)
                    if (previous_state.phase, previous_state.stage) != (
                        signals.phase,
                        signals.stage,
                    ):
                        confirmed_clock = time.monotonic() - self.started
                        self.supervisor.machine.entered_at = confirmed_clock
                        if signals.stage == SignalStage.GREEN:
                            self.supervisor.phase_started_at = confirmed_clock
                    ack = self.hardware.ack or {}
                    if (
                        ack.get("sense_mask") != ack.get("output_mask")
                        or ack.get("watchdog") != "OK"
                    ):
                        raise RuntimeError("Electrical feedback/watchdog disagreement")
                    self.latencies["hardware_ack"].append(self.hardware.ack_latency_ms or 0)
                    self.latencies["camera_to_ack"].append(
                        (time.monotonic() - analysis.captured_at) * 1000
                    )
                    self.last_decision_id = uuid.uuid4().hex
                    self.recorder.submit(
                        "decision",
                        {
                            "session_id": self.recorder.id,
                            "event_id": self.last_decision_id,
                            "observation_schema": "TrafficState",
                            "observation": traffic.model_dump(mode="json"),
                            "vision_model_id": self.model_id,
                            "policy": self.policy.name,
                            "policy_model_id": self.policy.model_id,
                            "policy_mode": self.policy_mode,
                            "actual_baseline_action": baseline.model_dump(mode="json"),
                            "proposed_action": proposed.model_dump(mode="json")
                            if fallback is None
                            else None,
                            "safety_response": self.supervisor.outcome.__dict__,
                            "executed_phase": signal_name(signals),
                            "hardware_ack": ack,
                            "fallback_reason": fallback,
                            "tracks": [t.model_dump(mode="json") for t in observation.tracks],
                        },
                    )
            except Exception as error:
                await self.emergency_stop(str(error))
            self.latest = self.snapshot()
            self.recorder.submit("telemetry", self.latest)
            if (
                self.recorder.record_video
                and analysis
                and analysis.video.frame_id != self._last_recorded_frame
            ):
                self._last_recorded_frame = analysis.video.frame_id
                self.recorder.submit_frame(
                    analysis.video.frame.copy(),
                    {
                        "source_frame_id": analysis.video.frame_id,
                        "captured_at_monotonic": analysis.video.captured_at,
                        "source_timestamp": analysis.video.timestamp,
                    },
                )
            await asyncio.sleep(max(0, 1 / self.config.control_hz - (time.monotonic() - tick)))

    def overlay_calibration(self) -> dict[str, Any]:
        """Only a saved, complete calibration verified against the live source is active."""
        c = self.calibration
        valid = (
            c is not None and self.calibration_check() and self.preflight()["calibration_complete"]
        )
        return {
            "state": "CALIBRATED" if valid else "CALIBRATION_INVALID" if c else "NO_CALIBRATION",
            "source_id": self.config.camera.source_id,
            "geometry": c.geometry.model_dump(mode="json") if valid and c is not None else None,
        }

    def snapshot(self) -> dict[str, Any]:
        from smart_traffic_backend.maket.setup import diagnostic_issues

        a = self.pipeline.observation()
        analyzed = self.pipeline.latest_analysis
        vision_state = None
        confirmed_calibration = self.calibration_check()
        traffic_data = a.traffic.model_dump(mode="json") if a else None
        if a is not None and not confirmed_calibration and traffic_data is not None:
            traffic_data["directions"] = {
                direction: type(metrics)
                .model_validate(
                    {
                        **metrics.model_dump(),
                        "estimated_queue_count": None,
                        "queue_confidence": 0,
                        "observation": metrics.observation.model_copy(
                            update={
                                "observation_status": ObservationStatus.DEGRADED,
                                "measurement_confidence": 0,
                                "failure_reasons": (
                                    *metrics.observation.failure_reasons,
                                    "calibration_not_confirmed",
                                ),
                            }
                        ),
                    }
                )
                .model_dump(mode="json")
                for direction, metrics in a.traffic.directions.items()
            }
        if a is not None and analyzed is not None:
            h, w = analyzed.video.frame.shape[:2]
            vision_state = estimate(
                a,
                captured_at=analyzed.captured_at,
                now=time.monotonic(),
                width=w,
                height=h,
                calibration=self.calibration,
                confirmed=confirmed_calibration,
                status=self.pipeline.status(),
                slow_speed=self.config.camera.analytics.slow_speed_px_per_second,
                max_age=self.config.camera.max_frame_age_seconds,
            ).model_dump(mode="json")
        signal = self.supervisor.machine.snapshot(self.now)
        duration = (
            self.supervisor.machine.green_seconds
            if signal.stage == SignalStage.GREEN
            else self.config.timing.yellow_seconds
            if signal.stage == SignalStage.YELLOW
            else self.config.timing.all_red_seconds
        )
        return {
            "phase_remaining_seconds": max(0, duration - signal.elapsed_seconds)
            if self.running or self.demo_running
            else None,
            "phase_timer_source": "HOST_CONTROLLER_ESTIMATE_NOT_ESP32_READBACK",
            "vision_state": vision_state,
            "mode": "PHYSICAL_MAKET"
            if self.config.output_mode == "PHYSICAL_OUTPUT"
            else "REAL_VISION",
            "autosetup": self.setup_state,
            "issues": diagnostic_issues(self),
            "output_mode": self.config.output_mode,
            "demo_running": self.demo_running,
            "readiness": self.readiness(),
            "camera_error": getattr(self.pipeline.source, "failure_code", None)
            or self.camera_error,
            "camera_revalidation_required": self.revalidation_required,
            "camera_metadata": getattr(self, "camera_metadata", {}),
            "vision_profile": self.config.vision_profile,
            "camera_connection": "CONNECTED"
            if self.pipeline.source.connected
            else getattr(self, "camera_connection", "NOT_CHECKED"),
            "vision_domain_validation": "NOT_TESTED",
            "vision_warning": "COCO baseline has no measured toy-object accuracy"
            if self.config.vision_profile == "COCO_BASELINE"
            else self.model_error,
            "state": self.state,
            "running": self.running,
            "hardware_test": self.hardware_test,
            "failure": self.failure,
            "policy": self.policy.name,
            "learned_error": self.learned_error,
            "policy_mode": self.policy_mode,
            "session_id": self.recorder.id,
            "traffic": traffic_data,
            "maket_metrics": self.metrics,
            "tracks": [t.model_dump(mode="json") for t in a.tracks] if a else [],
            "pipeline": self.pipeline.status().model_dump(mode="json"),
            "performance": self.pipeline.performance(),
            "heartbeats": self.pipeline.heartbeat_timestamps(),
            "hardware": self.hardware.snapshot(),
            "hardware_execution": self.hardware_service.snapshot(),
            "approved_state": signal_name(self.supervisor.machine.snapshot(self.now))
            if self.config.output_mode == "PHYSICAL_OUTPUT"
            else None,
            "virtual_signal_state": signal_name(self.supervisor.machine.snapshot(self.now))
            if self.demo_running and self.config.output_mode == "CAMERA_ONLY"
            else None,
            "proposed": self.proposed.model_dump(mode="json") if self.proposed else None,
            "policy_error": self.policy_error,
            "preflight": self.preflight(),
            "calibration_status": self.alignment,
            "overlay_calibration": self.overlay_calibration(),
            "latency_ms": {
                k: {"p50": float(np.median(v)), "p95": float(np.percentile(v, 95))} if v else None
                for k, v in self.latencies.items()
            },
            "recording": {
                "available": self.recorder.enabled and self.recorder.error is None,
                "retention_days": self.config.retention_days,
                "events_written": self.recorder.events_written,
                "video": self.recorder.record_video,
                "dropped": self.recorder.dropped,
                "error": self.recorder.error,
            },
            "physical_lamp_optical_confirmation": False,
        }

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
        try:
            await self.emergency_stop("runtime_shutdown")
        finally:
            await self.hardware.close()
            try:
                await asyncio.to_thread(self.pipeline.stop)
            finally:
                await asyncio.to_thread(self.recorder.close)
                self.executor.shutdown(wait=False, cancel_futures=True)
