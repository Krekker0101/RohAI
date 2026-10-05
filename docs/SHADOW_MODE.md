# Shadow Mode

## Modes

- `DISABLED`: no learned proposer is called.
- `SHADOW`: evaluates real `TrafficState` observations and journals proposals; baseline
  action remains active and policy output cannot reach the hardware adapter.
- `ADVISORY`: evaluates/journals the same proposal without actuating; an operator can
  inspect the recorded proposal. It does not currently render a separate UI advisory.
- `ACTIVE`: rejected. There is no physical production release gate in this repository.

SHADOW/ADVISORY require a proposer injected by the application. The repository does not
load or select a learned model in the live backend, so requesting either mode without a
proposer returns conflict. This prevents a baseline result from being mislabeled as an
AI proposal.

## Isolation and failover

The baseline control tick is not awaited on policy inference. At most one policy task
may be outstanding; later ticks keep baseline control and record that the policy was
busy. A task timeout, exception, invalid phase, non-finite green duration, or schema
mismatch is recorded as `policy_error`. The candidate type constrains phase and requires
a finite positive duration. No failed candidate is used as a safety action.
Already-constructed candidate instances are revalidated, including instances created
through validation-bypassing model APIs. Proposers run in one off-loop worker thread.
A timed-out running proposal keeps that worker slot occupied; subsequent ticks journal
busy fallback rather than creating additional workers. Threads cannot terminate arbitrary
native/GIL-held work. Untrusted execution requires process isolation before physical
commissioning; the device watchdog remains necessary.

## Audit fields

`GET /api/v1/safety/decisions` returns a bounded in-memory journal containing observation
sequence, actual baseline action, AI proposed action, safety response, policy error, and
the next observed traffic outcome where available. Journal capacity is fixed at 10,000;
process restart loses records. Durable, tamper-evident storage and outcome-retention
policy are release requirements, not currently implemented.

The current audit outcome captures observed queue counts and simulation/control time.
It is not a causal evaluation of policy quality and must not be used alone to claim
traffic-safety improvement.
