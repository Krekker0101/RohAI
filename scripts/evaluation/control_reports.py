"""Render completed, immutable control experiment results without rerunning any policy."""

import argparse
import csv
from datetime import datetime
from pathlib import Path
from typing import Any

from traffic_simulator.learning.experiment_runtime import file_hash, read, write_new
from traffic_simulator.learning.protocol import PolicyRunManifest, digest, freeze_run_manifest


def number(value: Any) -> str:
    return "—" if value is None else f"{value:,.2f}"


def interval(values: list[float]) -> str:
    return f"[{number(values[0])}; {number(values[1])}]"


def delay_table(suite: dict[str, Any]) -> str:
    lines = [
        "| Policy | Mean | Median | SD | p90 | p95 | p99 | Worst |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name in ("fixed", "adaptive", "actuated", "full"):
        v = suite["policies"][name]["total_delay_entity_s"]
        lines.append(
            "| "
            + name
            + " | "
            + " | ".join(
                number(v[key]) for key in ("mean", "median", "std", "p90", "p95", "p99", "maximum")
            )
            + " |"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, default=Path("configs/control/ppo-experiment-v1.json"))
    args = parser.parse_args()
    plan = read(args.plan)
    root = Path(plan["output"])
    results = read(root / "summary.json")
    selection = results["selection"]
    n = len(selection["run_seeds"])
    steps = selection["steps"]
    size = selection["sample_size"]
    raw_link = "../artifacts/control/20260929-v1/summary.json"
    test_interval = results["splits"]["TEST"]["comparisons"]["full-vs-actuated"]["aggregate"][
        "total_delay_entity_s"
    ]["mean_ci95"]
    test_half_width = (test_interval[1] - test_interval[0]) / 2
    report = [
        "# Control Benchmark Report — 2026-09-29",
        "",
        f"Результат: **{results['conclusion']}**. Production default остаётся Adaptive. "
        "Обучение реально выполнено; автоматического promotion или изменения backend config нет.",
        "",
        f"PPO обучен с {n} независимыми seeds для каждого из пяти вариантов: "
        f"{n * 5} runs × {plan['training_steps']} решений, по 12 эпизодов/run. "
        f"Это ограниченный synthetic experiment; сходимость не установлена. "
        f"Validation выбрал checkpoint {steps}, общий для всей группы seeds. "
        "Не выбирался лучший seed по TEST.",
        "",
        f"[Зафиксированный план](../configs/control/ppo-experiment-v1.json), "
        f"[selection](../artifacts/control/20260929-v1/selection.json), "
        f"[raw aggregate]({raw_link}), "
        "[все episode/evaluation records](../artifacts/control/20260929-v1/).",
        "",
        "## Условия и выбор модели",
        "",
        "Все policies используют одинаковые ScenarioDefinition, seeds, arrival tapes, "
        "perception profile, timing и SafetySupervisor. Их соответствие проверено по hash. "
        "Noise имеет одинаковые настройки/seed, но не обязано давать идентичную последовательность "
        "детекций: сами trajectories меняются от действий. Ground truth используется только "
        "scorer.",
        "",
        "Reward — отрицательная суммарная задержка entity*s, с фиксированным множителем 0.01 "
        "для optimizer. Веса не подбирались. Алгоритм: SB3 PPO 2.9.0, MLP 32×32, "
        "learning rate 0.0003, rollout 360, batch 60, epochs/update 4. "
        "Checkpoint сохраняет optimizer и RNG; точное совпадение параметров после "
        "episode-boundary resume проверено отдельным тестом.",
        "",
        f"Pilot использовал 4 VALIDATION seeds: SD парной разницы = {number(size['pilot_std'])} "
        f"entity*s. Для precision planning выбран half-width {number(size['target_half_width'])} "
        f"(10% среднего actuated, не acceptance margin). Правило min=12/max=16 выбрало {n}. "
        f"Ограничение бюджета по этому расчёту: {size['budget_limited']}. "
        "Четыре pilot seeds включены в итоговую группу, а не добавлены как независимые "
        "повторные runs.",
        "",
        "Precision planning не гарантирует точность на новом распределении. На TEST "
        "полуширина CI95 парной разницы delay составляет "
        f"{number(test_half_width)} "
        "entity*s; сопоставлять её нужно с запланированным half-width выше. "
        "Размер выборки после просмотра TEST не увеличивался. Это ограничивает точность "
        "оценки величины эффекта, даже когда знак ухудшения относительно Actuated установлен.",
        "",
        "TEST запущен только после immutable selection. Повторный запуск завершённого "
        "experiment отклоняется. Resume недописанной final suite допускает только те же jobs "
        "и hashes. Ablation не использовались для повторного выбора основной модели по TEST.",
        "",
        "## TEST: total delay, entity*s",
        "",
        delay_table(results["splits"]["TEST"]),
        "",
        "Таблица описывает распределение episode outcomes. Baselines имеют два scenario outcomes; "
        "learned — те же два сценария для каждого training seed. Эти строки нельзя считать "
        "равным количеством независимых наблюдений. Для вывода используется парный анализ ниже.",
        "",
        "| Сравнение | Средняя разница learned − baseline | CI95 | Одновременный CI | Вывод |",
        "| --- | ---: | --- | --- | --- |",
    ]
    for baseline in ("fixed", "adaptive", "actuated"):
        v = results["splits"]["TEST"]["comparisons"][f"full-vs-{baseline}"]["aggregate"][
            "total_delay_entity_s"
        ]
        report.append(
            f"| {baseline} | {number(v['mean'])} | {interval(v['mean_ci95'])} | "
            f"{interval(v['simultaneous_ci'])} | {v['verdict']} |"
        )
    report.extend(
        [
            "",
            "CI строится bootstrap по независимым training seeds после усреднения парных "
            "разниц по фиксированному suite. Повторения baseline не увеличивают n. "
            "Simultaneous CI использует заранее определённую Bonferroni-поправку; 20 000 "
            "resamples. "
            "Это приближённый interval с ограниченной tail resolution, не математическая "
            "гарантия safety. "
            "CI условен на двух TEST scenarios и не оценивает неопределённость всех дорог/камер.",
            "",
            "Для каждого KPI сохранены mean, median, SD, CI95, p90/p95/p99, min/max и "
            "worst-case job "
            "в JSON/CSV. Парные CI по каждому critical metric и каждому сценарию находятся в "
            "comparisons. "
            "Delay включает queue + excess downstream hold; throughput включает vehicles и "
            "pedestrians. "
            "Queue length — максимальная lane extent в метрах, без невидимого upstream backlog; "
            "queue count включает upstream. Fairness включает right-censored unfinished waits; "
            "эти percentiles не являются завершёнными lifetime waits.",
            "",
            "## Известная pedestrian-heavy regression",
            "",
            "Повторён catalog pedestrian-heavy seed=42, duration=180 s. Это ранее известный "
            "регрессионный тест, а не новый held-out scenario.",
            "",
            "| Policy | Pedestrian delay, person*s | Pedestrian maximum wait, s |",
            "| --- | ---: | ---: |",
        ]
    )
    regression = results["splits"]["REGRESSION"]
    for name in ("fixed", "adaptive", "actuated", "full"):
        p = regression["policies"][name]
        report.append(
            f"| {name} | {number(p['pedestrian_delay_person_s']['mean'])} | "
            f"{number(p['pedestrian_max_wait_s']['mean'])} |"
        )
    report.extend(
        [
            "",
            f"Статистические gates: {results['statistical_gates_passed']}. "
            f"Все simulator invariants: {results['all_invariants_passed']}. "
            "Улучшение aggregate delay не отменяет ухудшение pedestrians, tail wait или другого "
            "critical subgroup. Inconclusive gate не даёт PASS. В raw fairness сохранены "
            "direction, "
            "vehicle class и pedestrians с count и censoring.",
            "",
            "## Ограничения вывода",
            "",
            "Небольшой training budget не позволяет опровергнуть возможность более сильного RL "
            "после другого независимого эксперимента. В этом запуске оценивается конкретный PPO "
            "и зафиксированный бюджет. Нельзя дообучить по увиденному TEST и повторно назвать "
            "этот же TEST независимым. Полевой perception benchmark отсутствует; stress levels "
            "не являются измеренными road probabilities. SUMO fidelity и реальная безопасность "
            "данным экспериментом не подтверждаются.",
            "",
            "Эпизоды начинаются с пустой среды и длятся 120/180 s; warm-up и steady-state "
            "оценка не проводились. Queue metrics сэмплируются раз в 1 s, поэтому maximum "
            "не является точным пиком непрерывной траектории. Inference latency в raw "
            "измерена при параллельном CPU evaluation, а не на отдельном production устройстве.",
            "",
            "## Воспроизведение",
            "",
            "```powershell\nuv sync --extra vision --extra control-training --group dev\n"
            ".venv/Scripts/python.exe scripts/training/control_experiment.py run --workers 4\n"
            "```",
            "",
            "Команда возобновляет незавершённый experiment, но откажется повторять уже "
            "завершённый. "
            "Для нового исследования нужны новый preregistration/output и новый held-out release. "
            "Checkpoint zip/rng сохранены локально под artifacts/control и исключены из Git; "
            "JSON provenance и raw results сохранены отдельно. Контроль целостности не "
            "является подписью.",
            "",
            "Реализация использует [официальный PPO "
            "API](https://stable-baselines3.readthedocs.io/en/v2.9.0/modules/ppo.html) "
            "и штатный save/load; backpropagation не переписывался.",
        ]
    )
    robustness = [
        "# Policy Robustness Report — 2026-09-29",
        "",
        "15 заранее зафиксированных stress scenarios, по одному exogenous seed на профиль, "
        f"каждый проверен для всех {n} learned seeds и трёх baselines. "
        "Это условная оценка данного suite, не оценка вероятности отказа на дороге. "
        "10–30% missed detections, fragmentation, false positives и calibration error "
        "— искусственные stress levels.",
        "",
        "Latency spike добавляет 5 s доставки кадров в интервале 40–55 s. Late frame "
        "не может заменить более новый observation. При outage supervisor завершает clearance "
        "и latch all-red; отсутствие конфликта не означает сохранение throughput.",
        "",
        "| Scenario | Learned delay | Actuated delay | Δ delay | Critical regressions with CI "
        "> 0 |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    challenge = results["splits"]["CHALLENGE"]
    rows = [read(p) for p in (root / "evaluation" / "CHALLENGE").glob("*.json")]
    comparisons = challenge["comparisons"]["full-vs-actuated"]["per_scenario"]
    for case, metrics in comparisons.items():
        matching = [r for r in rows if r["scenario"]["scenario_id"] == case]
        learned_values = [
            r["metrics"]["total_delay_entity_s"]
            for r in matching
            if r["job"]["policy"] == "learned"
        ]
        base = next(
            r["metrics"]["total_delay_entity_s"]
            for r in matching
            if r["job"]["policy"] == "actuated"
        )
        bad = [k for k in plan["promotion_rule"]["critical"] if metrics[k]["verdict"] == "WORSE"]
        robustness.append(
            f"| {case} | {number(sum(learned_values) / len(learned_values))} | "
            f"{number(base)} | {number(metrics['total_delay_entity_s']['mean'])} | "
            f"{', '.join(bad) or '—'} |"
        )
    robustness.extend(
        [
            "",
            f"Invariants: {challenge['invariants_passed']}. "
            "Прочерк в regressions не означает PASS: interval может быть INCONCLUSIVE. "
            "Все интервалы, max waits, queue lengths, fairness, safety intervention rates и "
            f"worst-case records находятся в [raw results]({raw_link}). "
            "Interventions считаются на physics ticks, включая обычный clearance lock; "
            "это не число опасных сигналов.",
            "",
            "Условия также включают unseen intensity 33 vehicle/min/approach, regular arrivals, "
            "pedestrian spike 150 person/min, heavy congestion и rapid demand changes. "
            "Ни один из этих stress outcomes не использован для выбора checkpoint или reward.",
        ]
    )
    ablation = [
        "# Policy Ablation Report — 2026-09-29",
        "",
        f"Каждый вариант переобучен с теми же {n} seeds и бюджетом {plan['training_steps']} "
        f"решений. Сравнение на VALIDATION при общем checkpoint {steps}, выбранном для full. "
        "TEST для ablation не использовался. Это retraining ablation, не только masking при "
        "inference.",
        "",
        "| Variant | Mean Δ delay variant − full, entity*s | CI95 | Corrected CI |",
        "| --- | ---: | --- | --- |",
    ]
    for variant, value in results["ablations"].items():
        ablation.append(
            f"| {variant} | {number(value['mean'])} | {interval(value['mean_ci95'])} | "
            f"{interval(value['simultaneous_ci'])} |"
        )
    sensitivity: dict[str, list[int]] = {
        v: [0, 0] for v in ("no_confidence", "no_arrivals", "no_history")
    }
    for path in (root / "evaluation" / "VALIDATION").glob("*.json"):
        row = read(path)
        if row["job"].get("variant") != "full" or row["job"].get("steps") != steps:
            continue
        for variant, count in row["validation_action_sensitivity"].items():
            sensitivity[variant][0] += count
            sensitivity[variant][1] += sum(row["action_histogram"].values())
    ablation.extend(
        [
            "",
            "Дополнительно full model проверена контрфактическим masking тех же "
            "VALIDATION observations. Число изменившихся deterministic action requests:",
            "",
        ]
    )
    ablation.extend(
        f"- {variant}: {value[0]} / {value[1]}." for variant, value in sensitivity.items()
    )
    ablation.extend(
        [
            "",
            "No confidence удаляет confidence value, valid и clipped, сохраняя остальные "
            "health/status признаки. Это не удаление всей информации о надёжности камеры. "
            "No arrivals удаляет explicit arrival estimates; history очереди по-прежнему может "
            "косвенно кодировать приток. No history обнуляет прошлые строки, сохраняя clocks "
            "текущего "
            "наблюдения. Clean training отключает sensor noise/delay в TRAIN, но evaluation "
            "остаётся одинаковым для всех variants.",
            "",
            "В healthy simulator confidence почти постоянна (0.9); ranges сохранены в каждом "
            "VALIDATION record. Разница retraining outcomes сама по себе не доказывает полезность "
            "этого признака. Если counterfactual actions не меняются или CI включает ноль, "
            "причинное объяснение улучшения не установлено. Clean-training ablation проверена "
            "на обычной VALIDATION, а не на полном stress suite: делать вывод о robustness "
            "по ней нельзя.",
            "",
            f"[Raw comparisons и все decomposition]({raw_link}).",
        ]
    )
    for filename, lines in (
        ("CONTROL_BENCHMARK_REPORT.md", report),
        ("POLICY_ROBUSTNESS_REPORT.md", robustness),
        ("POLICY_ABLATION_REPORT.md", ablation),
    ):
        destination = Path("docs") / filename
        with destination.open("x", encoding="utf-8") as stream:
            stream.write("\n".join(lines) + "\n")
    with (root / "metric-statistics.csv").open("x", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "split",
                "policy",
                "metric",
                "n",
                "mean",
                "median",
                "sd",
                "ci95_low",
                "ci95_high",
                "p90",
                "p95",
                "p99",
                "minimum",
                "maximum",
            ]
        )
        for split, suite in results["splits"].items():
            for policy, metrics in suite["policies"].items():
                for metric, value in metrics.items():
                    if not isinstance(value, dict) or "n" not in value:
                        continue
                    writer.writerow(
                        [
                            split,
                            policy,
                            metric,
                            value["n"],
                            value["mean"],
                            value["median"],
                            value["std"],
                            *value["mean_ci95"],
                            value["p90"],
                            value["p95"],
                            value["p99"],
                            value["minimum"],
                            value["maximum"],
                        ]
                    )
    write_new(
        root / "report-manifest.json",
        {
            "summary_sha256": file_hash(root / "summary.json"),
            "renderer_sha256": file_hash(Path(__file__)),
            "files": {
                name: file_hash(Path("docs") / name)
                for name in (
                    "CONTROL_BENCHMARK_REPORT.md",
                    "POLICY_ROBUSTNESS_REPORT.md",
                    "POLICY_ABLATION_REPORT.md",
                )
            },
        },
    )
    validation_rows = [read(p) for p in (root / "evaluation" / "VALIDATION").glob("*.json")]
    for directory in sorted((root / "runs").iterdir()):
        if not directory.is_dir():
            continue
        start = read(directory / "run-start.json")
        checkpoint = directory / f"checkpoint-{steps:07}.zip"
        last = read(directory / f"checkpoint-{plan['training_steps']:07}.json")
        matching = [
            row
            for row in validation_rows
            if row["job"].get("seed") == start["seed"]
            and row["job"].get("variant") == start["variant"]
            and row["job"].get("steps") == steps
        ]
        manifest = PolicyRunManifest.model_validate(
            {
                "run_id": directory.name,
                "seed": start["seed"],
                "reward": plan["reward"],
                "reward_sha256": digest(plan["reward"]),
                "scenario_split_sha256": plan["sha256"],
                "algorithm_config": {
                    "name": "PPO",
                    **plan["algorithm"],
                    "variant": start["variant"],
                    "net_arch": plan["net_arch"],
                    "reward_scale": plan["reward_scale"],
                    "training_steps": plan["training_steps"],
                    "selected_checkpoint_steps": steps,
                },
                "git_commit": plan["git_commit"],
                "dirty_tree": plan["dirty_tree"],
                "code_sha256": plan["code_sha256"],
                "dependency_versions": plan["dependencies"],
                "started_at": datetime.fromisoformat(start["started_at"]),
                "ended_at": datetime.fromisoformat(last["timestamp"]),
                "checkpoint": str(checkpoint.resolve()),
                "checkpoint_sha256": file_hash(checkpoint),
                "evaluation_metrics": {
                    row["scenario"]["scenario_id"]: {
                        "metrics": row["metrics"],
                        "invariants_passed": row["invariants_passed"],
                        "raw_result_sha256": file_hash(Path(row["job"]["destination"])),
                    }
                    for row in matching
                },
                "status": "COMPLETED",
            }
        )
        freeze_run_manifest(manifest, directory / "run-manifest.json")
    write_new(
        root / "raw-results-index.json",
        {
            "plan_sha256": plan["sha256"],
            "files": {
                path.relative_to(root).as_posix(): file_hash(path)
                for path in sorted(root.rglob("*"))
                if path.is_file() and path.suffix in (".json", ".csv")
            },
            "checkpoint_payloads": "Checksums in checkpoint sidecars; zip/rng stay local",
        },
    )


if __name__ == "__main__":
    main()
