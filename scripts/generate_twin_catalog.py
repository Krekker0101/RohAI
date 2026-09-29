"""Materialize the declarative engineering catalog and scenario JSON schema."""

from pathlib import Path

from traffic_simulator.scenarios import catalog
from traffic_simulator.twin_evaluation import write_report
from traffic_simulator.twin_models import ScenarioDefinition


def main() -> None:
    root = Path("configs/simulation/scenarios")
    for name, scenario in catalog().items():
        write_report(root / f"{name}.json", scenario.model_dump(mode="json"))
    write_report(
        Path("configs/simulation/scenario.schema.json"), ScenarioDefinition.model_json_schema()
    )


if __name__ == "__main__":
    main()
