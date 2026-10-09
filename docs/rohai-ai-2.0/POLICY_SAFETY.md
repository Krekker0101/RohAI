# Policy safety и объяснения

IntelligenceEngine принимает только SIMULATION/SHADOW; PHYSICAL_MAKET отвергается.
Hardware HIL RohAI 1.0 не завершён. Approval/deployment pipeline vision Stage 1
не является approval RL-контроллера. Existing Adaptive и firmware не заменялись.

Strict phase/duration requests проходят SafetySupervisor и тот же core
SafetyController: minimum/maximum green, yellow/all-red, pedestrian clearance,
service deadlines, monotonic time, reliable perception и fault latch.
Model не получает serial/GPIO port. Unsupported/nonfinite outputs, metadata
mismatch, stale/low-confidence observation, exception и inference timeout дают
deterministic fixed fallback request. Ненадёжные данные всё равно поступают
в safety: fallback не отменяет all-red latch или mandatory clearance.

Actor/safety clocks и phase/stage snapshots согласованы; последний history frame
сверяется с observation. Decision sequence/time растут. Audit хранит observed
queues/waits/arrivals, request space, proposed action, model ID/version/hash,
fallback reason, safety reason, effective phase/duration/signals и latency.
Три listed phases — пространство requests, не обещание немедленного переключения.
Neural action index не интерпретируется как causal feature explanation.

Inference имеет один worker и одну bounded queue; по timeout actor помещается
в карантин и не вызывается снова. Caller перестаёт ждать по deadline; backlog
и конкурентный predict одного model не создаются. Worker — daemon thread:
Python не прерывает уже выполняющийся native/hung inference. Такой worker может
оставаться до завершения процесса; это ограничение экспериментального SIM/SHADOW
engine, не hard real-time гарантия вычислительного ресурса. Перед будущим physical
разрешением нужны отдельно изолированный/прерываемый worker, HIL и deadlines.
Close ждёт максимум .1 s. Per-call deadline .1 s; benchmark .5 s с логированием fallback.

In-memory audit bounded последними 256 решениями; benchmark сохраняет **все**
decisions JSON отдельно от агрегированных metrics. Caller SHADOW должен сохранять
возвращаемые audits для полного durable журнала. Audit signals описывают shadow
shield result, не физический ACK/readback.

Simulator принимает pre-applied signal только если он равен текущему shield snapshot,
чтобы engine не применял одну команду дважды в первом physics tick.
Switch reward при этом учитывает действительное prior switch counter.
Regression проверяет 4 physics safety ticks на 1 s decision, deterministic fallback,
timeout/error, physical-mode rejection и benchmark invariants.
