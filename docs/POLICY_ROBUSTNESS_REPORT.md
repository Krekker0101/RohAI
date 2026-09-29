# Policy Robustness Report — 2026-09-29

15 заранее зафиксированных stress scenarios, по одному exogenous seed на профиль, каждый проверен для всех 12 learned seeds и трёх baselines. Это условная оценка данного suite, не оценка вероятности отказа на дороге. 10–30% missed detections, fragmentation, false positives и calibration error — искусственные stress levels.

Latency spike добавляет 5 s доставки кадров в интервале 40–55 s. Late frame не может заменить более новый observation. При outage supervisor завершает clearance и latch all-red; отсутствие конфликта не означает сохранение throughput.

| Scenario | Learned delay | Actuated delay | Δ delay | Critical regressions with CI > 0 |
| --- | ---: | ---: | ---: | --- |
| CHALLENGE-calibration-55006 | 5,047.86 | 3,422.84 | 1,625.02 | max_wait_s, queue_length_max_m, throughput_entities |
| CHALLENGE-congestion-55010 | 50,176.92 | 48,396.77 | 1,780.15 | max_wait_s, fairness_direction_max_gap_s |
| CHALLENGE-false-positives-55005 | 4,749.08 | 2,387.04 | 2,362.04 | queue_length_max_m, throughput_entities |
| CHALLENGE-fragmentation-55004 | 5,487.91 | 3,916.66 | 1,571.25 | max_wait_s, queue_length_max_m, throughput_entities |
| CHALLENGE-latency-spike-55007 | 6,832.68 | 6,237.01 | 595.67 | max_wait_s, queue_length_max_m |
| CHALLENGE-miss-10-55001 | 4,459.65 | 2,243.94 | 2,215.71 | queue_length_max_m, throughput_entities |
| CHALLENGE-miss-30-55002 | 4,853.34 | 3,132.21 | 1,721.12 | queue_length_max_m, fairness_direction_max_gap_s, throughput_entities |
| CHALLENGE-occlusion-55003 | 4,509.83 | 3,154.64 | 1,355.19 | max_wait_s, throughput_entities |
| CHALLENGE-one-fps-55000 | 6,501.12 | 5,438.04 | 1,063.08 | queue_length_max_m, throughput_entities |
| CHALLENGE-outage-55009 | 7,480.16 | 6,478.74 | 1,001.42 | throughput_entities |
| CHALLENGE-partial-outage-55008 | 7,466.63 | 6,781.53 | 685.10 | — |
| CHALLENGE-pedestrian-spike-55014 | 10,301.07 | 8,058.69 | 2,242.38 | pedestrian_delay_person_s, pedestrian_max_wait_s, max_wait_s, safety_intervention_rate, throughput_entities |
| CHALLENGE-rapid-change-55011 | 6,692.99 | 3,881.22 | 2,811.77 | queue_length_max_m, safety_intervention_rate, throughput_entities |
| CHALLENGE-regular-arrivals-55013 | 4,667.52 | 2,613.25 | 2,054.27 | max_wait_s, queue_length_max_m, fairness_direction_max_gap_s, throughput_entities |
| CHALLENGE-unseen-intensity-55012 | 11,811.95 | 10,603.51 | 1,208.44 | — |

Invariants: True. Прочерк в regressions не означает PASS: interval может быть INCONCLUSIVE. Все интервалы, max waits, queue lengths, fairness, safety intervention rates и worst-case records находятся в [raw results](../artifacts/control/20260929-v1/summary.json). Interventions считаются на physics ticks, включая обычный clearance lock; это не число опасных сигналов.

Условия также включают unseen intensity 33 vehicle/min/approach, regular arrivals, pedestrian spike 150 person/min, heavy congestion и rapid demand changes. Ни один из этих stress outcomes не использован для выбора checkpoint или reward.
