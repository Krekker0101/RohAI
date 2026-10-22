import json
from pathlib import Path

from traffic_vision.training.config import TrainingConfig
from traffic_vision.training.package import ModelPackage


def main() -> None:
    root = Path("datasets/schema")
    root.mkdir(parents=True, exist_ok=True)
    for name, model in (("training-config", TrainingConfig), ("model-package", ModelPackage)):
        (root / f"{name}.schema.json").write_text(
            json.dumps(model.model_json_schema(), indent=2) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    main()
