# Policy Ablation Report — 2026-09-29

Каждый вариант переобучен с теми же 12 seeds и бюджетом 2160 решений. Сравнение на VALIDATION при общем checkpoint 2160, выбранном для full. TEST для ablation не использовался. Это retraining ablation, не только masking при inference.

| Variant | Mean Δ delay variant − full, entity*s | CI95 | Corrected CI |
| --- | ---: | --- | --- |
| no_confidence | 152.18 | [-0.69; 457.22] | [-0.69; 609.62] |
| no_arrivals | 366.01 | [0.00; 884.43] | [0.00; 1,068.02] |
| no_history | 300.31 | [-1,213.34; 1,491.68] | [-1,670.17; 1,794.96] |
| clean_training | 511.92 | [0.00; 1,187.78] | [0.00; 1,419.25] |

Дополнительно full model проверена контрфактическим masking тех же VALIDATION observations. Число изменившихся deterministic action requests:

- no_confidence: 184 / 4320.
- no_arrivals: 53 / 4320.
- no_history: 885 / 4320.

No confidence удаляет confidence value, valid и clipped, сохраняя остальные health/status признаки. Это не удаление всей информации о надёжности камеры. No arrivals удаляет explicit arrival estimates; history очереди по-прежнему может косвенно кодировать приток. No history обнуляет прошлые строки, сохраняя clocks текущего наблюдения. Clean training отключает sensor noise/delay в TRAIN, но evaluation остаётся одинаковым для всех variants.

В healthy simulator confidence почти постоянна (0.9); ranges сохранены в каждом VALIDATION record. Разница retraining outcomes сама по себе не доказывает полезность этого признака. Если counterfactual actions не меняются или CI включает ноль, причинное объяснение улучшения не установлено. Clean-training ablation проверена на обычной VALIDATION, а не на полном stress suite: делать вывод о robustness по ней нельзя.

[Raw comparisons и все decomposition](../artifacts/control/20260929-v1/summary.json).
