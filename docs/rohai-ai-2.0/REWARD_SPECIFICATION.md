# Reward Stage 2

Существующие reward_terms/RewardLedger сохранены; coefficients frozen до TEST.
Для одного decision interval:

`reward = .01 × (2·departures − Δtotal_delay − .01·max_wait_integral
− .005·direction_wait_gap_integral − 2·Δspillback − switches)`.

Δtotal_delay: entity·s, включая pedestrian queue delay и excess downstream delay.
Departures: entity. Max-wait/gap integrals: s² (instantaneous wait × physics dt).
Spillback: lane·s. Switching: число green→clearance transitions.
queue_delay и pedestrian_delay отдельно логируются, но имеют coefficient=0,
чтобы повторно не суммировать составляющие total_delay.
Max-wait и fairness terms противодействуют starvation; benchmark дополнительно
считает waiting entities, пересёкшие supervisor-derived service bound.
Service opportunity deadline не гарантирует фактический departure при blockage/outage.

Все raw units и weighted contributions сохраняются в episode reports. .01 —
fixed optimizer utility scale, не изменение benchmark KPI. Finite nonnegative
cost/count terms и component coverage проверяются. Ground truth разрешён scorer,
но не actor features; future tape и истинные downstream queues не подмешиваются
в observation. Downstream spillback penalty не даёт actor скрытого occupancy sensor.

Безопасность обеспечивается SafetyController вне reward. Коэффициенты не служат
разрешением конфликтующих зелёных и не выдаются за эмпирически оптимальные.
Исторический PPO имел только −total_delay; новая multiobjective specification —
один явно зарегистрированный дизайн, без подбора весов по held-out TEST.
Budget2x повтор сохраняет ту же reward, чтобы изолировать training budget.
Evaluation episode_reward сохраняет reference −total_delay для общей KPI шкалы
всех baselines; training reports содержат именно multiobjective training utility.
Эти reward reports не подменяются; сравнения основаны на raw delay.
