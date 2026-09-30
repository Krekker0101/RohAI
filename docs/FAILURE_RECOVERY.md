# Failure and Recovery

## Runtime response

Vision freshness and stage-health checks use monotonic clocks. The external status API
reports component states and last successful frame, inference, analytics, control tick,
and hardware ACK timestamps. If an observation is invalid or stale, the deterministic
safety supervisor latches the software state and the runtime requests the configured
device safe program when the adapter provides `enter_safe()`. This request is not
proof that physical outputs changed. The independent device watchdog is the required
backstop when the host, process, or network cannot send a command.

On control/policy error, policy failure is isolated: the learned proposal is discarded
and deterministic baseline/safety remains authoritative. If controller, vision, or
hardware delivery fails, the runtime enters `FAULT` then `SAFE`; the device watchdog
must handle cases where host delivery is unavailable.

## Recovery state machine

`HEALTHY -> DEGRADED -> FAULT -> SAFE -> RECOVERING ->
READY_FOR_OPERATOR_CONFIRMATION -> HEALTHY`

`DEGRADED` is entered by the control loop whenever vision health, analytics health, or
any direction's evidence is unusable while the controller itself is still running and
safe. It carries a machine-readable reason (`vision_degraded`, `analytics_degraded`,
`observation_degraded`) on `GET /api/v1/safety/status`, so a reduced-capability
intersection is never reported as `HEALTHY`. Clearing a transient degradation returns to
`HEALTHY` and drops the reason. `DEGRADED` never unlatches `FAULT`/`SAFE` and never
enables a learned policy; degradation changes what the operator is told, not what the
controller is allowed to do.

Not every state transition is automatic. Only an explicit recovery request can leave
`SAFE`; failed qualification returns to `SAFE`. The checks are:

- `source`: source is connected and the pipeline is running.
- `calibration`: every required direction reports valid calibration.
- `freshness`: frame is within configured source freshness.
- `analytics`: analytics heartbeat is within its stage deadline.
- `controller`: control task is live and no failure is latched.
- `hardware`: recent hardware ACK health is good.

All six checks must pass before the state becomes
`READY_FOR_OPERATOR_CONFIRMATION`. An authorized operator confirmation is still
required. Confirmation never enables a policy; policy mode is reset to `DISABLED`.
The current source worker terminates on disconnect, so reconnect alone is not a
recovery implementation. A supervised source restart and renewed qualification remain
future work.

## Operator API

- `GET /api/v1/safety/status`: recovery state, component health, hardware stages.
- `POST /api/v1/safety/recovery/begin`: enter `RECOVERING` from `SAFE`.
- `POST /api/v1/safety/recovery/check`: evaluate current checks.
- `POST /api/v1/safety/recovery/confirm`: operator confirmation after qualification.
- `GET /api/v1/safety/decisions`: bounded in-memory policy audit records.

Mutating endpoints use the existing operator-token dependency when configured. A
deployment must configure operator authentication and network controls; the local demo
default is not an operational security posture.
