"""Render the principal audit from fresh local evidence, with explicit coverage limits."""

# Markdown table rows and reproducible commands intentionally remain on one line.
# ruff: noqa: E501

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

ROOT = Path("artifacts/final-audit/20261004")


def read(name: str) -> dict:
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def main() -> None:
    first = read("policy-run-1/policy_comparison.json")
    surge = read("policy-run-surge/policy_comparison.json")
    rows = first["results"] + surge["results"]
    policies = ("fixed", "adaptive", "actuated", "learned-701", "learned-702", "learned-703")
    state = read("state_estimation.json")
    before, after = read("runtime_probes_before.json"), read("runtime_probes.json")
    suite = ET.parse(ROOT / "full-suite-final.xml").getroot().find("testsuite")
    assert suite is not None
    sections = [
        """# FINAL PRINCIPAL AI SYSTEM AUDIT — RohAI

Дата: 2026-10-04 (Asia/Dushanbe). Проверяется рабочее дерево, не только HEAD.
Разработка новых функций остановлена. Изменения продукта ниже — исправления выявленных
дефектов; новые scripts/tests служат только аудиту. Предыдущие отчёты и passing tests
не использованы как доказательство качества. Исходный аудит использован только как
список F01–F29. Проверка независима от его выводов, но выполнена тем же рабочим агентом:
это не внешняя сертификация. Полноту обнаружения всех дефектов гарантировать нельзя.

## 1. Executive Summary

Подтверждены программные механизмы, а не пригодность к дорожной эксплуатации.
В workspace нет sealed dataset release или annotations JSONL. Свежая validation
отклоняет пустой draft. Real-camera ground truth отсутствует в доступных данных:
precision/recall/mAP, IDF1 и ошибки дорожных измерений определить нельзя.
Физические камеры, cabinet, MCU watchdog и lamp readback не тестировались.
Физическая activation и learned ACTIVE по-прежнему запрещены кодом.

Свежий benchmark: 204 эпизода по 120 s, 17 семейств, два новых flow seed,
Fixed/Adaptive/Actuated и три frozen PPO checkpoint. Выбор этих трёх checkpoint
сделан до измерения, без выбора победителя по новым результатам. Это 3 из 12
имеющихся training seeds, а не проверка всех обученных вариантов. Arrival tapes
парно совпадают; инварианты симулятора проверены. Общего превосходства learned
policy не показано. Learned-701 завершил 0 pedestrian trips в обоих surge эпизодах;
pedestrian green opportunity появился лишь в 119.75 s. Это существенное ограничение
и censoring, а не доказательство отсутствия обслуживания при бесконечном горизонте.

Исправлены: ложный command-sent lamp state при ENTER_SAFE; направление live fault
через device safety program; отсутствие привязки измерений к release gate; открытые
мутации vision/calibration без токена; event-loop blocking shadow evaluation.
Не устранены отсутствие field evidence, physical hardware и полноценного recovery.

## 2. Architecture

Capture → bounded latest slot → YOLO/ByteTrack → analytics/metric estimation.
Control читает latest_analysis до overlay/JPEG; preview имеет отдельный worker.
ObservedTrafficController и SafetyController определяют baseline state.
ProtocolHardwareController различает send/ACK/readback; Mock остаётся demo adapter.
DeviceKernel/ProcessDeviceTransport — эмуляторы, не device firmware.
PPO работает в отдельной simulator observation/action среде. Live backend не загружает
эти checkpoint автоматически: shadow proposer требует явной application injection.
Исправленный proposer работает в одном worker thread, вне control event loop.
ImprovementWriter → bounded SQLite journal; offline workflow/registries отделены
от inference. Generic release pointer не является hot reload или physical deployment.

| Audit area | Проверенный путь / предел доказательства |
|---|---|
| Perception, tracking, camera health | detector.py, estimator.py, health.py; свежие adversarial probes и 18 unlabelled frames. Accuracy неизвестна. |
| Observability, performance | pipeline heartbeat, component health, dashboard telemetry; отдельные control-ready и pipeline-ready. Live SLO/soak не измерены. |
| Calibration, geometry, traffic state | calibration.py, alignment.py, state_estimation.py; metric support, independent point validation, alignment latch. Live landmark provider не поставлен. |
| Pedestrians | WAITING/CROSSING/CLEARING, simulator crossing conflicts и censored pedestrian metrics; нет полевого ground truth. |
| Dataset, leakage | dataset/validation.py, splits.py, release.py: source/camera/day grouping, duplicate review, immutable releases. Нет actual labeled release для проверки leakage. |
| Vision training/validation, registry | training runner/package/evaluate/registry; hash verification до deserialization, held-out gates. Target training/validation не выполнены из-за отсутствия данных. |
| Simulation, sim-to-real | twin_dynamics/environment/perception, engineering noise assumptions; conservation/replay tests. SUMO/road validation отсутствует. |
| RL environment/observations/actions/reward | environment.py, contracts.py, observation.py, reward.py: validity masks, Discrete(9), shield, unit delay costs; truth используется для scoring. |
| Baselines, policy training/generalization/fairness/robustness | свежие frozen-checkpoint comparisons; новые traffic realizations, known families и synthetic stress. Новое обучение не запускалось. |
| Safety, failover, protocol/watchdog | safety supervisors, runtime failure paths, authenticated leases; software/emulator evidence. Physical safety не установлена. |
| Shadow/logging/drift/rollback/MLOps | ограниченный worker, SQLite, measured release gates, atomic pointer rollback; нет integrated live release/reload, signed operator identity или regulatory audit archive. |
| Security/reproducibility/test coverage | token mutation gate, local trusted artifact model, source hashes/snapshot; security penetration test и количественная line coverage не выполнены. |

## 3. Original F01–F29 status

FIXED означает устранение конкретного software defect, а не field validation.
PARTIAL — механизм существует, но исходное ограничение полностью не закрыто.
"""
    ]
    statuses = [
        (
            "FIXED",
            "models.py/analytics.py сохраняют unknown как None; новые probes отвергают пустую/негодную картинку, full perception suite повторён.",
        ),
        (
            "FIXED",
            "estimator.py: bounded hold и TEMPORARILY_OCCLUDED/PREDICTED; missing не сбрасывает ожидание немедленно. Реальная occlusion accuracy неизвестна.",
        ),
        (
            "FIXED",
            "timestamp-based estimator и frame_rate_adequate; low/variable FPS probes дают degraded evidence вместо ложной нулевой очереди.",
        ),
        (
            "PARTIAL",
            "Pedestrian states/arrivals разделены, crossing clearance моделируется; real pedestrian labels отсутствуют, surge выявил слабое обслуживание learned-701.",
        ),
        (
            "PARTIAL",
            "Surveyed planar homography/support/alignment gates есть; 570 synthetic samples. Нет полевой survey validation или live alignment extractor.",
        ),
        (
            "PARTIAL",
            "state_estimation.py считает entry/stop/exit events, mutual-unique recovery; ID-switch stress выполнен. Реальные unique arrivals не подтверждены.",
        ),
        (
            "FIXED",
            "analytics._metrics объединяет detection/tracking/motion/evidence confidence, не detector score alone; probabilistic calibration confidence не измерена.",
        ),
        (
            "FIXED",
            "ModelManifest.validate_model проверяет mapping, input size и hash до/после load; независимый class mismatch probe отвергается.",
        ),
        (
            "PARTIAL",
            "PPO checkpoint и RL adapter существуют; fresh comparison не подтверждает стабильное преимущество. Adaptive остаётся heuristic.",
        ),
        (
            "PARTIAL",
            "ObservationV1 validity/normalization и observed-only simulator inputs; нет готового live TrafficState→PPO history adapter и field equivalence evidence.",
        ),
        (
            "PARTIAL",
            "Digital twin моделирует capacity, headway, spillback/crossings и noisy perception; assumptions не fitted to road, SUMO/road comparison отсутствует.",
        ),
        (
            "FIXED",
            "Gymnasium LearningControlEnvironment, action validation и closed-loop dynamics реально выполнены в fresh benchmark и invalid-action probes.",
        ),
        (
            "FIXED",
            "204 fresh paired episodes, 17 families, два flow seed, три learned runs; tails/censoring/negative cases сохранены. Не road experiment.",
        ),
        (
            "OPEN",
            "Видео replay по-прежнему не меняет будущий road traffic. Только closed-loop simulator допускает causal simulator comparison.",
        ),
        (
            "NOT APPLICABLE",
            "Автоматическая emergency-vehicle classification не заявлена текущим scope; API emergency — operator input. При новом требовании finding снова OPEN.",
        ),
        (
            "OPEN",
            "Свежий inventory: zero release.json и zero annotations JSONL; draft validation empty_dataset. Нет целевого verified dataset.",
        ),
        (
            "PARTIAL",
            "Training config/seed/resume/package tools реализованы и проверены fixtures; target vision training не выполнено, legacy PPO train tree был dirty.",
        ),
        (
            "OPEN",
            "Independent real-camera accuracy не измерена. 18 unlabelled smoke frames и synthetic metric benchmark не заменяют labels.",
        ),
        (
            "PARTIAL",
            "SQLite decisions/outcomes есть даже DISABLED, 7 days/10k records; queue loss, unsigned records, unverified live model version и отсутствие field outcomes.",
        ),
        (
            "PARTIAL",
            "Два local registries, artifact hashes, measured generic gate исправлен; generic deployment pointer не подключён к live loader, approval identity — local assertion.",
        ),
        (
            "PARTIAL",
            "Drift trigger реализован, но scalar proxies и first-session reference пропускают виды drift. Accuracy regression требует external evaluation.",
        ),
        (
            "OPEN",
            "Vendor transport/physical outputs/sensors/MCU watchdog не поставлены и не тестировались. initialize отклоняет physical_device=true.",
        ),
        (
            "FIXED",
            "Vision/analytics/controller/hardware heartbeats разделены; latched live supervisor fault теперь выставляет FAULT и блокирует confirmation.",
        ),
        (
            "FIXED",
            "Control читает latest_analysis; preview worker failure не отменяет analytics. Pipeline regression rerun passed.",
        ),
        (
            "PARTIAL",
            "ID/maps/track budgets bounded, превышение приводит к отказу. Безопасная непрерывная session rotation/reconnect не реализована.",
        ),
        (
            "PARTIAL",
            "Explicit recovery checks/operator state machine есть; terminal source failure и latched safety требуют supervised new runtime/device rearm.",
        ),
        (
            "OPEN",
            "VisionRuntime по-прежнему один pipeline; production multi-camera fusion отсутствует.",
        ),
        (
            "PARTIAL",
            "Shared build_approach_state/default weights совпадают; vision settings независимо configurable, нет release-time semantic equivalence gate.",
        ),
        (
            "PARTIAL",
            "Без токена vision/calibration mutation теперь fail closed; остаются shared token, no RBAC/TLS/rate limiting/read access control в приложении. Simulation demo остаётся без обязательного токена.",
        ),
    ]
    original = Path("docs/AI_READINESS_AUDIT_2026-09-27.md").read_text(encoding="utf-8")
    titles = re.findall(r"\*\*(F\d{2})[^\n]+", original)
    assert titles == [f"F{i:02}" for i in range(1, 30)]
    sections.append("\n| Finding | Status | Свежая проверка / evidence |\n|---|---|---|\n")
    for index, (status, proof) in enumerate(statuses, 1):
        sections.append(f"| F{index:02} | {status} | {proof} |\n")
    sections.append("""
## 4. Vision accuracy

Real-camera accuracy: NOT MEASURED. Workspace draft имеет sources=[], frames=[];
dataset validation отклонён. Ни одним prediction log нельзя заменить ground truth.
YOLO smoke загрузил manifest-verified pretrained weights и обработал 18 CPU frames:
3 black, 3 flat dark, 12 existing demo replay frames. Во всех 18 detector вернул 0
objects. Без labels это не TP/TN/FP/FN и не доказательство работоспособности detector
на дороге. Source replay может содержать presentation overlays. Night/heavy occlusion
accuracy, emergency class accuracy и IDF1 неизвестны.
""")
    latencies = [f["latency_ms"] for f in after["frames"]]
    sections.append(
        f"\nCPU sample latency median/p95/max: {np.percentile(latencies, 50):.2f}/"
        f"{np.percentile(latencies, 95):.2f}/{max(latencies):.2f} ms. "
        "Это малый smoke sample, не end-to-end SLO, GPU benchmark или soak.\n"
    )
    sections.append("""
## 5. State-estimation accuracy

Свежий запуск analytical synthetic benchmark: 24 trajectories, ±0.4 px localization
perturbation, известная planar mapping; 570 metric samples. Он использует ground-contact
fixtures, а не YOLO predictions. Event strata имеют лишь по одному crossing sample;
идеальные нулевые ошибки waiting/count здесь не подтверждают road accuracy.

| Metric | Stratum | n | MAE | p95 absolute error | Unit |
|---|---|---:|---:|---:|---|
""")
    for metric in state["report"]["summaries"]:
        if metric["metric"] in (
            "distance_to_stop_line",
            "estimated_speed_mps",
            "queue_length_m",
            "queue_vehicle_count",
            "mean_wait",
            "event_timestamp",
        ):
            sections.append(
                f"| {metric['metric']} | {metric['stratum']} | {metric['samples']} | "
                f"{metric['mae']:.6g} | "
                f"{metric['absolute_error_percentiles']['p95']:.6g} | {metric['unit']} |\n"
            )
    sections.append("""
## 6. Simulator validation

Fresh digital-twin run: 11 catalog scenarios, seed 913003, duration 120 s, report pass=true.
Fresh policy audit: all 204 episode invariants_passed=true; same arrival tape for all
six policies per case. Inspected dynamics conservation, capacity/queue, crossing conflict
and deterministic replay paths. Synthetic invariants do not establish that their physics
or perception assumptions match a real intersection. No SUMO integration executed.
Scenario families are known; new seeds mean held-out realizations, not unseen cities,
geometry, climate, demand models or independent field calibration.

## 7. Policy comparison

Frozen full-701/702/703 checkpoint-0002160.zip hashes independently rechecked against
their local sidecars. No training or selection based on audit results occurred.
Actuated — queue-responsive deterministic comparator, not proven optimal network
max-pressure. Four policy classes share supervisor/Timing/observed inputs. No policy
receives truth as actor input; scorer necessarily reads simulator truth.

Below: 34 episodes per policy, equally weighted cases. Mean of per-episode p95 is
explicitly not a pooled p95. Maximum wait is a right-censored lower bound at the
120 s horizon. Queue includes queued + upstream entities, not just lane vehicles.

| Policy | Mean delay entity·s | Mean departures | Mean episode p95 wait s | Worst wait lower bound s | Max queue entities | Unfinished total |
|---|---:|---:|---:|---:|---:|---:|
""")
    summary = {}
    for policy in policies:
        group = [r for r in rows if r["policy"] == policy]
        delay = np.mean([r["metrics"]["total_delay_entity_s"] for r in group])
        departed = np.mean([r["metrics"]["throughput_entities"] for r in group])
        tail = np.mean(
            [r["report"]["fairness"]["all"]["all_arrivals_lower_bound"]["p95"] or 0 for r in group]
        )
        maximum = max(
            r["report"]["fairness"]["all"]["all_arrivals_lower_bound"]["maximum"] or 0
            for r in group
        )
        unfinished = sum(r["report"]["fairness"]["all"]["right_censored_count"] for r in group)
        queue = max(r["metrics"]["queue_max_entities"] for r in group)
        summary[policy] = {
            "mean_delay": float(delay),
            "mean_departures": float(departed),
            "mean_episode_p95_wait": float(tail),
            "max_wait_lower_bound": maximum,
            "unfinished": unfinished,
            "queue_max_entities": queue,
        }
        sections.append(
            f"| {policy} | {delay:.2f} | {departed:.2f} | {tail:.2f} | "
            f"{maximum:.2f} | {queue} | {unfinished} |\n"
        )
    sections.append(
        "\nMean total delay by family (entity·s; two fresh seeds):\n\n"
        "| Family | Fixed | Adaptive | Actuated | Learned-701 | Learned-702 | Learned-703 |\n"
        "|---|---:|---:|---:|---:|---:|---:|\n"
    )
    for family in sorted({r["family"] for r in rows}):
        means = [
            np.mean(
                [
                    r["metrics"]["total_delay_entity_s"]
                    for r in rows
                    if r["family"] == family and r["policy"] == p
                ]
            )
            for p in policies
        ]
        sections.append(f"| {family} | " + " | ".join(f"{v:.1f}" for v in means) + " |\n")
    sections.append(
        "\nPaired delay differences versus Fixed (negative means lower simulator cost):\n\n"
        "| Policy | Lower / equal / higher cases | Median delta | Worst delta | Best delta |\n"
        "|---|---|---:|---:|---:|\n"
    )
    for policy in policies[1:]:
        deltas = []
        for row in [r for r in rows if r["policy"] == policy]:
            fixed = next(
                r
                for r in rows
                if r["policy"] == "fixed"
                and r["family"] == row["family"]
                and r["seed"] == row["seed"]
            )
            deltas.append(
                row["metrics"]["total_delay_entity_s"] - fixed["metrics"]["total_delay_entity_s"]
            )
        counts = [
            sum(v < -1e-6 for v in deltas),
            sum(abs(v) <= 1e-6 for v in deltas),
            sum(v > 1e-6 for v in deltas),
        ]
        sections.append(
            f"| {policy} | {counts[0]} / {counts[1]} / {counts[2]} | "
            f"{np.median(deltas):.2f} | {max(deltas):.2f} | {min(deltas):.2f} |\n"
        )
    sections.append("""

Two independent flow seeds and three training seeds are insufficient for a population
confidence claim. Cases share families; they are not 204 independent random field trials.
Per-direction p90/p95/p99/max, censor counts, supervisor interventions, full scenario
parameters and checkpoint hashes are in raw JSON. Do not extrapolate these averages to roads.

## 8. Pedestrian performance

Surge: 6 → 150 → 6 pedestrians/min in 40 s intervals; both flow seeds.
Departures below are completed crossings, not green opportunities.

| Policy | Mean surge pedestrian delay person·s | Mean pedestrian departures | Worst pedestrian wait lower bound s |
|---|---:|---:|---:|
""")
    for policy in policies:
        group = [r for r in surge["results"] if r["policy"] == policy]
        delay = np.mean([r["metrics"]["pedestrian_delay_person_s"] for r in group])
        served = np.mean([r["report"]["pedestrian_departed"] for r in group])
        wait = max(
            r["report"]["fairness"]["pedestrians"]["all_arrivals_lower_bound"]["maximum"] or 0
            for r in group
        )
        sections.append(f"| {policy} | {delay:.2f} | {served:.1f} | {wait:.2f} |\n")
    sections.append("""

Learned-701 first pedestrian opportunity in 119.75 s leaves all 103 pedestrians per
episode unfinished. service_bound_s is a trigger threshold; min-green/clearance and
competing overdue phases add delay. It is not a measured maximum individual wait bound.
Learned-703 serves more pedestrians but has substantially larger total delay in many
vehicle-heavy cases. No blanket fairness/generalization improvement is established.
Field pedestrian detection, disability/crossing-speed coverage and injury risk are unknown.

## 9. Robustness

| Requested fault | Fresh evidence | Limit |
|---|---|---|
| Camera sees nothing / physically blocked | black image probes reject not_black and reliable evidence | No physical lens blockage, textured blocker or complete recall test. |
| Camera moved / calibration mismatch | synthetic static landmarks displaced 30 px → SUSPECT; wrong camera calibration rejected | No live landmark extraction or camera bench. |
| Frozen frame | repeated textured image flagged image_not_frozen=false after threshold | Stationary valid scene may be a false positive. |
| Low / variable FPS | interval probes flag inadequate cadence; 1 FPS synthetic closed-loop comparison | No actual RTSP jitter benchmark. |
| Heavy occlusion / night | 60% synthetic occlusion and degraded-night scenarios; dark image diagnostics | Not real occlusion/night accuracy. |
| High / no traffic | saturated and zero-demand paired cases; zero-demand delay=0 | Arrival models are engineering assumptions. |
| Pedestrian surge | 12 new paired surge episodes, adverse learned-701 result retained | Short horizon and censoring; no road experiment. |
| ID-switch storm / detector regression | 80% ID-switch and 60% missed-detection stress profiles; class mismatch rejection | Synthetic perception, not actual detector retraining/regression measurement. |
| Controller/policy timeout | blocking proposer before/after measurement; new worker isolation test | Worker threads cannot terminate arbitrary GIL/native work. |
| RL NaN / invalid action | NaN/Inf/negative/9/bool/string rejected before world time advances | Learned ACTIVE remains blocked. |
| Hardware ACK missing / disconnected | rerun protocol/fault tests timeout and network failure; no confirmation | Test transports, not real network/device. |
| Network partition / watchdog timeout | independent kernel lease expiry/replay probe and rerun device-process tests | Python emulator, no MCU firmware/power-loss evidence. |
| Recovery after outage | synthetic outage reports remain fault-latched; live confirm rejects latched supervisor | No successful field recovery; supervised restart/rearm required. |
| Out-of-order timestamps | positive backwards packet cadence rejected; analyzer strict ordering | Negative timestamp rejected by schema; initial fixture corrected accordingly. |
| Preview failure | fresh pipeline suite confirms analysis independent of JPEG/overlay | No continuous CPU/memory/network soak. |

Initial new probe suite had 19 passes and 2 failures: one genuine ENTER_SAFE stage
misrepresentation and one invalid negative-time fixture which correctly raised a schema
error. The latter was corrected to a positive backwards timestamp, not masked as a fix.
Adversarial before/after XML and affected-subsystem results are retained separately.

## 10. Safety

Learned policy is not safety authority. ACTIVE configuration raises PermissionError;
physical-device capability initialization also refuses activation. Observation validity,
finite schemas, minimum green, yellow/all-red and replay/session/lease checks were read
and exercised. Source failure latches software supervisor; reappearance does not rearm.
Live latched observation faults now request ENTER_SAFE when the adapter supports it,
allowing its configured local transition instead of sending an assumed all-red lamp state.
Mock all-red remains a prototype behavior, not a site-approved universal traffic mode.
Recovery FAULT/SAFE labels are software state labels, not physical lamp confirmation.

## 11. Hardware status

HARDWARE NOT TESTED. No physical vendor transport, sensors, commissioning or MCU
watchdog evidence is present. HMAC/checksum/session/sequence/TTL/ACK/readback validation
is a software protocol contract. ENTER_SAFE identifies a configured program; command_sent
lamp state is now unknown until actual evidence is available. Device-side max-green and
TTL logic were tested in an emulator; independent hardware reset/power failure/output
short, driver conflict, sensor failure, clock/reset failure and key-compromise behavior
remain untested. Process independence in fixtures is not electrical independence.

## 12. MLOps

SQLite bounded retention and next-observation outcomes are exercised, including DISABLED.
Writer queue is 128 entries; storage failures/drops are counted but not an externally
monitored regulatory audit guarantee. Unverified predictions stay UNVERIFIED_PREDICTION.
Hard-case tags are candidates; track churn is not verified fragmentation, and cross-model
disagreement requires external evidence (only one live detector is installed).

Generic promotion now requires a MEASURED independent report bound to model/benchmark/
dataset/code and computes regression from finite measurements. Human_verified and actor
are still trusted local assertions, not signed review identities. Vision ModelRegistry
uses its own stronger package/evaluation verifier. Neither is physical authorization.
Generic SHADOW/ADVISORY deployment pointer and idempotent rollback verify retained bytes;
pointer switching is tested, live reload/stand canary/operator deployment is not.
Drift scalar proxies and first-session reference only trigger evaluation, never weight updates.
Production inference has no optimizer/training path. New training was not run in this audit.

## 13. Open risks and new findings

Severity is evaluated within the currently blocked physical activation boundary.
No currently reachable physical CRITICAL exploit was demonstrated; this does not establish
physical safety. Critical real-hardware hazards cannot be evaluated without hardware.

| ID / severity / status | Root cause / affected component | Reproduction | Impact | Fix / regression test | Remaining risk |
|---|---|---|---|---|---|
| A01 HIGH FIXED | capture_hardware_state inferred all-red from ENTER_SAFE | new test_enter_safe_does_not_claim_a_signal_was_sent failed before fix | Audit/UI could report a state never sent | command_sent=None for program command; same probe passes | Physical program completion still unknown. |
| A02 HIGH FIXED (software scope) | VisionRuntime sent SIGNAL all-red on latched fault; recovery status not coupled to supervisor | faulted engine plus ProgramHardware spy; source path inspection | Device transition rejection; misleading HEALTHY after observation return | dispatch ENTER_SAFE, set FAULT, prohibit confirm with latched safety; new vision fault probe | No field recovery or physical safe-state guarantee. |
| A03 HIGH FIXED (structural gate) | Generic candidate trusted caller regression booleans; arbitrary evaluation JSON accepted | Empty report candidate probe; prior source lacked report parsing | Ungrounded candidate could obtain local release pointer | Bind measured report, compute finite regression gates; self-certified report rejected | Authenticated report provenance/threshold preregistration remain external. |
| A04 HIGH FIXED (mutation gate) | require_operator accepted missing token in vision/calibration | new no-token require_operator probe; previous early return | Unauthorized policy/emergency/calibration/recovery mutations | Missing auth fails 503 outside simulation; authenticated fixtures and API rerun | Shared token, transport TLS, RBAC, rate limiting and read confidentiality not implemented. |
| A05 HIGH FIXED for blocking-sleep probe | async proposer executed on control loop; asyncio timeout was cooperative | runtime_probes_before: blocking sleep exceeds deadline with policy_error=null | Control heartbeat delayed; advertised timeout not effective | Single off-loop worker retains slot after timeout; latency/timeout/busy regression | Thread cannot kill GIL/native hang; trusted proposer only, process isolation required for untrusted deployment. |
| A06 HIGH OPEN | No independently verified target labels / dataset | inventory zero releases/annotations; fresh empty_dataset rejection | Cannot estimate real-camera error or regression/leakage | Collect/review/version blind field benchmark; no data fabrication permitted | Field vision/tracking/state/pedestrian metrics unavailable. |
| A07 HIGH OPEN | Physical device integration/watchdog/sensing absent | physical_device=true rejected; only emulator fixtures exist | Cannot establish actual outputs or outage behavior | Bench/vendor firmware/sensor/commissioning work required; ACTIVE remains blocked | Electrical conflicts, power/reset and physical stuck lamps untested. |
| A08 HIGH OPEN | Learned policies not demonstrated robust/fair; service deadline is threshold, not per-person bound | fresh vehicle-heavy losses and surge learned-701 zero departures | Potential poor service allocation; short-horizon completion bias | Keep offline/shadow only; evaluate predefined subgroup/fairness gates before any promotion; no audit-driven retraining | Only three training seeds, two flow seeds, engineered perception and short horizon. |
| A09 HIGH OPEN | Recovery has no supervised source/runtime/device rearm implementation | stopped source cannot satisfy live checks; latched supervisor confirmation rejected | Extended outage and manual restart; no automatic operational recovery | Explicit new-runtime/rearm procedure, then renewed qualification; confirm rejection tested | Successful recovery not measured on field equipment. |
| A10 HIGH OPEN for legacy exact training reproduction | Historical PPO sidecars say dirty_tree=true; commit/hash are not archived source bytes | checkpoint sidecar inspection and current source comparison | Cannot reconstruct exact historical training from commit alone | Current audit source snapshot/hash captured; preserve full code/data/env for future training | This cannot reconstruct missing historical dirty source or missing field datasets. |
| A11 MEDIUM OPEN | Drift uses scalar means/class mixture and first-session baseline | source inspection: same weighted means can hide changed distributions; restart resets reference | False negatives/positives in drift trigger | Offline distribution/strata evaluation and reviewed reference needed; no new feature added | Drift cannot prove quality regression. |
| A12 MEDIUM OPEN | Best-effort logging, unverified configurable model IDs, local unsigned approvals | queue/drop/storage-fault tests, default UNVERIFIED_LOCAL_MODEL | Missing audit records and incomplete live provenance | Monitor loss, bind loaded hashes, durable external review before release | Abrupt exit loses queued decisions; filesystem writers are trusted. |
| A13 MEDIUM OPEN | Image heuristics cannot distinguish all stationary/blocked/moved scenes | repeated textured frame triggers freeze; movement requires external landmarks | False shutdown or undetected semantic degradation | Field diagnostic validation and independent landmarks needed | No measured diagnostic sensitivity/specificity. |
| A14 MEDIUM OPEN | Dashboard ignores five hardware stages; generic release pointer has no live consumer | telemetry.ts normalizes requested lamps, ignores safety stages; no generic loader calls | Operator may misinterpret commands; pointer rollback is not live rollback | Keep as demo/analysis UI; integrate measured state and supervised loader before deployment | No physical console validation; no live switching latency measured. |
| A15 LOW OPEN | TestClient dependency deprecations; dashboard default temp/build paths denied by host | fresh pytest warnings; npm default EPERM | Tooling maintenance/reproducibility friction | Alternative runner/noEmit commands pass; update dependencies separately | E2E/browser/GPU/long soak not executed. |

No problem was hidden by weakening assertions, excluding adverse scenarios, retraining
against audit cases or inventing labels. Physical/integration/data findings cannot be
closed by software-only changes. Mutable shared dicts and native model execution are
not a security sandbox; the worker isolates scheduling, not hostile code permissions.

## 14. Evidence

All paths below are local artifacts, not claims copied from prior reports:

- artifacts/final-audit/20261004/policy-run-1/policy_comparison.json — 192 episodes.
- artifacts/final-audit/20261004/policy-run-surge/policy_comparison.json — 12 surge episodes.
- state_estimation.json — fresh 570 analytical synthetic metric samples.
- digital_twin.json — fresh 11-scenario simulator validation.
- inventory.json / dataset_validation.json — fresh available-data inventory and rejection.
- adversarial-before.xml / adversarial-after.xml / affected-subsystems.xml /
  affected-subsystems-final.xml / isolation-after.xml — staged regression evidence.
- runtime_probes_before.json / runtime_probes.json — actual timeout/latency before/after.
- full-suite-final.xml — final fresh Python suite; tests are contract evidence, not accuracy.
- environment.json / source_snapshot.zip — audited source/config hashes, environment,
  commit and dirty-tree metadata. Snapshot excludes .env and raw videos/weights.
- tests/test_principal_audit.py and scripts/audit/*.py — added independent audit probes.

Python fault suites were inspected and rerun; new probes intentionally exercise distinct
public contracts instead of trusting test names. No quantitative line/branch coverage,
external penetration test, physical fault injection, real RTSP reconnect, independent
SUMO comparison or browser E2E was performed. Dashboard default npm commands first
failed due to host EPERM on cache/buildinfo; configLoader runner and tsc noEmit alternatives
passed (12 Vitest tests); ESLint passed. Do not label the failed default commands passing.
""")
    sections.append(
        f"\nFinal Python suite: {suite.attrib['tests']} tests, "
        f"{suite.attrib['failures']} failures, {suite.attrib['errors']} errors. "
        "Two existing deprecation warnings. Ruff passes on backend/packages/tests/audit; "
        "mypy passes on backend, changed release module and two changed test modules "
        "(14 files), not a claim of full-repository typing.\n"
    )
    b, a = before["blocking_policy"], after["blocking_policy"]
    sections.append(
        f"\nBlocking probe: before {b['event_loop_delay_s'] * 1000:.2f} ms, "
        f"policy_error={b['policy_error']}; after {a['event_loop_delay_s'] * 1000:.2f} ms, "
        f"policy_error={a['policy_error']}. Timeout configured 20 ms.\n"
    )
    sections.append("""
## 15. Exact commands to reproduce results

PowerShell, cwd D:\\PROJECTS\\RohAI, installed .venv and existing trusted local weights.
Use new output paths: benchmark refuses to overwrite an existing directory/report.
Timing results vary by host load; scenario outcomes should reproduce with pinned dependencies.
The current runner includes all 17 families (204 episodes), combining the two audit runs.

```powershell
.venv\\Scripts\\python.exe scripts/audit/final_principal_audit.py --output artifacts/final-audit/reproduce-policy
.venv\\Scripts\\python.exe scripts/audit/final_principal_audit.py --only pedestrian-surge --output artifacts/final-audit/reproduce-surge
.venv\\Scripts\\python.exe scripts/benchmark_state_estimation.py --output artifacts/final-audit/reproduce-state.json
.venv\\Scripts\\python.exe scripts/evaluation/digital_twin.py --seed 913003 --duration 120 --output artifacts/final-audit/reproduce-twin.json
.venv\\Scripts\\python.exe scripts/dataset/validate.py configs/datasets/rohai-draft.json --output artifacts/final-audit/reproduce-dataset.json
# Dataset validation is expected to fail (exit 1); it is not an accuracy run.
.venv\\Scripts\\python.exe scripts/audit/runtime_probes.py
.venv\\Scripts\\python.exe -m pytest -q -p no:cacheprovider --basetemp=.runtime/pytest-principal-reproduce --junitxml=artifacts/final-audit/reproduce-tests.xml
.venv\\Scripts\\python.exe -m ruff check apps/backend/src packages tests scripts/audit
.venv\\Scripts\\python.exe -m mypy apps/backend/src packages/vision/src/traffic_vision/training/improvement_release.py tests/test_principal_audit.py tests/test_improvement_loop.py
Set-Location apps/dashboard
npm.cmd test -- --configLoader runner
node node_modules/typescript/bin/tsc -p tsconfig.json --noEmit --incremental false
npm.cmd run lint
Set-Location ../..
.venv\\Scripts\\python.exe scripts/audit/build_final_report.py
```

Historical before-fix failures require the corresponding pre-fix source; rerunning
the fixed source should not recreate them. runtime_probes writes its current result to
the audit directory; archive the existing result first when reproducing.

**Доказанный статус: исследовательский software prototype для offline simulation и
проверки программных контрактов. Дорожная точность, физическое исполнение сигналов,
field recovery и независимый аппаратный watchdog не подтверждены. Learned ACTIVE запрещён.**
""")
    Path("docs/FINAL_AI_VALIDATION_REPORT.md").write_text("".join(sections), encoding="utf-8")
    (ROOT / "summary.json").write_text(
        json.dumps(
            {"episodes": len(rows), "policies": summary, "original_statuses": statuses}, indent=2
        ),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "report": "docs/FINAL_AI_VALIDATION_REPORT.md",
                "episodes": len(rows),
                "tests": suite.attrib["tests"],
            }
        )
    )


if __name__ == "__main__":
    main()
