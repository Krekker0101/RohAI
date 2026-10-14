"""Offline SUMO fixture: paired vehicle-only experiments, never a hardware adapter."""

import importlib
import json
import math
import random
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from traffic_core.models import Decision, Phase, SignalStage
from traffic_core.safety import SafetyController, validate_active_groups
from traffic_core.settings import Timing


def generate_fixture(directory: Path, seed: int, seconds: int, demand: float) -> Path:
    if not 30 <= seconds <= 3600 or not math.isfinite(demand) or not 1 <= demand <= 3600:
        raise ValueError("Duration must be 30..3600s and demand 1..3600 vehicles/hour/approach")
    netconvert = shutil.which("netconvert")
    if netconvert is None:
        raise RuntimeError("SUMO netconvert is unavailable; install eclipse-sumo")
    directory.mkdir(parents=True, exist_ok=False)
    nodes = ET.Element("nodes")
    ET.SubElement(nodes, "node", id="c", x="0", y="0", type="traffic_light")
    coordinates = {"north": (0, 200), "south": (0, -200), "east": (200, 0), "west": (-200, 0)}
    edges = ET.Element("edges")
    connections = ET.Element("connections")
    opposite = {"north": "south", "south": "north", "east": "west", "west": "east"}
    for direction, (x, y) in coordinates.items():
        ET.SubElement(nodes, "node", id=direction, x=str(x), y=str(y))
        ET.SubElement(
            edges,
            "edge",
            {
                "id": f"{direction}_in",
                "from": direction,
                "to": "c",
                "numLanes": "1",
                "speed": "13.89",
            },
        )
        ET.SubElement(
            edges,
            "edge",
            {
                "id": f"{direction}_out",
                "from": "c",
                "to": direction,
                "numLanes": "1",
                "speed": "13.89",
            },
        )
        ET.SubElement(
            connections,
            "connection",
            {
                "from": f"{direction}_in",
                "to": f"{opposite[direction]}_out",
                "fromLane": "0",
                "toLane": "0",
            },
        )
    for name, element in (
        ("nodes.nod.xml", nodes),
        ("edges.edg.xml", edges),
        ("connections.con.xml", connections),
    ):
        ET.ElementTree(element).write(directory / name, encoding="utf-8", xml_declaration=True)
    subprocess.run(
        [
            netconvert,
            "--node-files",
            str(directory / "nodes.nod.xml"),
            "--edge-files",
            str(directory / "edges.edg.xml"),
            "--connection-files",
            str(directory / "connections.con.xml"),
            "--tls.default-type",
            "actuated",
            "--output-file",
            str(directory / "network.net.xml"),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )
    routes = ET.Element("routes")
    for direction in coordinates:
        ET.SubElement(
            routes, "route", id=direction, edges=f"{direction}_in {opposite[direction]}_out"
        )
    rng = random.Random(seed)
    departures: list[tuple[float, str]] = []
    for direction in coordinates:
        timestamp = rng.expovariate(demand / 3600)
        while timestamp < seconds:
            departures.append((timestamp, direction))
            timestamp += rng.expovariate(demand / 3600)
    for index, (timestamp, direction) in enumerate(sorted(departures)):
        ET.SubElement(
            routes,
            "vehicle",
            id=f"v{index}",
            route=direction,
            depart=f"{timestamp:.6f}",
            departSpeed="max",
        )
    ET.ElementTree(routes).write(directory / "routes.rou.xml", encoding="utf-8")
    configuration = ET.Element("configuration")
    inputs = ET.SubElement(configuration, "input")
    ET.SubElement(inputs, "net-file", value="network.net.xml")
    ET.SubElement(inputs, "route-files", value="routes.rou.xml")
    path = directory / "fixture.sumocfg"
    ET.ElementTree(configuration).write(path, encoding="utf-8")
    return path


def run_case(config: Path, policy: str, seed: int, seconds: int) -> dict[str, Any]:
    if policy not in {"fixed", "queue_adaptive", "queue_pressure", "sumo_actuated"}:
        raise ValueError("Unknown SUMO policy")
    binary = shutil.which("sumo")
    if binary is None:
        raise RuntimeError("SUMO binary unavailable")
    traci = importlib.import_module("traci")
    label = f"rohai-{seed}-{policy}"
    traci.start(
        [
            binary,
            "-c",
            str(config.resolve()),
            "--seed",
            str(seed),
            "--step-length",
            "1",
            "--no-step-log",
            "true",
            "--duration-log.disable",
            "true",
            "--waiting-time-memory",
            str(seconds),
            "--time-to-teleport",
            "-1",
        ],
        label=label,
    )
    client = traci.getConnection(label)
    timing = Timing()
    safety = SafetyController(timing)
    total_wait = 0.0
    inserted = departed = violations = 0
    queue_peak = 0
    wait_by_vehicle: dict[str, float] = {}
    max_direction_wait = dict.fromkeys(("north", "south", "east", "west"), 0.0)
    try:
        links = client.trafficlight.getControlledLinks("c")
        link_groups = []
        for group in links:
            incoming = {link[0].split("_", 1)[0] for link in group}
            if not incoming or not incoming <= {"north", "south", "east", "west"}:
                raise ValueError("Unexpected controlled SUMO link")
            phases = {Phase.NS if d in {"north", "south"} else Phase.EW for d in incoming}
            if len(phases) != 1:
                raise ValueError("Ambiguous SUMO link group")
            link_groups.append(phases.pop())
        for step in range(seconds):
            queues = {
                d: client.lane.getLastStepHaltingNumber(f"{d}_in_0") for d in max_direction_wait
            }
            ns, ew = queues["north"] + queues["south"], queues["east"] + queues["west"]
            if policy != "sumo_actuated":
                target = safety.phase
                elapsed = step - safety.entered_at
                if safety.stage != SignalStage.GREEN or elapsed >= safety.green_seconds:
                    target = Phase.EW if safety.phase == Phase.NS else Phase.NS
                if policy == "queue_pressure" and elapsed >= timing.min_green_seconds:
                    target = Phase.NS if ns > ew else Phase.EW if ew > ns else safety.phase
                duration = timing.fixed_green_seconds
                if policy == "queue_adaptive":
                    duration = min(
                        timing.max_green_seconds,
                        max(timing.min_green_seconds, 2 * (ns if target == Phase.NS else ew)),
                    )
                signal = safety.advance(
                    float(step), Decision(phase=target, green_seconds=duration, reason=policy)
                )
                state = "".join(
                    "G"
                    if p == signal.phase and signal.stage == SignalStage.GREEN
                    else "y"
                    if p == signal.phase and signal.stage == SignalStage.YELLOW
                    else "r"
                    for p in link_groups
                )
                client.trafficlight.setRedYellowGreenState("c", state)
            client.simulationStep()
            state = client.trafficlight.getRedYellowGreenState("c")
            try:
                validate_active_groups(
                    {p for p, lamp in zip(link_groups, state, strict=True) if lamp in "gGyY"}
                )
            except ValueError:
                violations += 1
                raise
            inserted += client.simulation.getDepartedNumber()
            departed += client.simulation.getArrivedNumber()
            halted = 0
            for vehicle in client.vehicle.getIDList():
                wait_by_vehicle.setdefault(vehicle, 0)
                if client.vehicle.getSpeed(vehicle) < 0.1:
                    halted += 1
                    wait_by_vehicle[vehicle] += 1
                road = client.vehicle.getRoadID(vehicle)
                direction = road.split("_", 1)[0]
                if direction in max_direction_wait:
                    max_direction_wait[direction] = max(
                        max_direction_wait[direction], client.vehicle.getWaitingTime(vehicle)
                    )
            total_wait += halted
            queue_peak = max(queue_peak, halted)
        waits = sorted(wait_by_vehicle.values())
        return {
            "policy": policy,
            "seed": seed,
            "seconds": seconds,
            "inserted": inserted,
            "departed": departed,
            "remaining": inserted - departed,
            "total_stopped_vehicle_seconds": total_wait,
            "mean_censored_stopped_seconds_per_inserted_vehicle": total_wait / inserted
            if inserted
            else None,
            "p95_censored_stopped_seconds": waits[math.ceil(len(waits) * 0.95) - 1]
            if waits
            else None,
            "max_direction_wait_seconds": max_direction_wait,
            "peak_halted_vehicles": queue_peak,
            "safety_violations": violations,
            "sumo_version": list(client.getVersion()),
        }
    finally:
        client.close()


def benchmark(
    directory: Path, seeds: tuple[int, ...], seconds: int, demand: float
) -> dict[str, Any]:
    if not seeds or len(set(seeds)) != len(seeds) or len(seeds) > 20:
        raise ValueError("Use 1..20 distinct seeds")
    if directory.exists():
        raise FileExistsError("Refusing to overwrite SUMO experiment")
    directory.mkdir(parents=True)
    policies = ["fixed", "queue_adaptive", "queue_pressure", "sumo_actuated"]
    plan = {
        "domain": "SUMO_SIMULATION",
        "seeds": seeds,
        "seconds": seconds,
        "demand_per_hour_per_approach": demand,
        "policies": policies,
        "limitations": "Straight vehicle movements only; uncalibrated fixture. "
        "No pedestrians, field Vision or learned actors. Inserted denominator excludes "
        "pending source vehicles; stopped time is not total travel delay. No road claims.",
    }
    (directory / "plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
    cases = []
    for seed in seeds:
        config = generate_fixture(directory / f"seed-{seed}", seed, seconds, demand)
        for policy in policies:
            result = run_case(config, policy, seed, seconds)
            cases.append(result)
            (directory / f"result-{seed}-{policy}.json").write_text(
                json.dumps(result, indent=2), encoding="utf-8"
            )
    report = {
        **plan,
        "cases": cases,
        "physical_execution": False,
        "status": "MEASURED",
        "field_effectiveness": "NOT_VERIFIED",
    }
    (directory / "summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
