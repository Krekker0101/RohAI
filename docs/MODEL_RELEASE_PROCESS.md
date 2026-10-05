# Model Release Process

1. Freeze dataset release, code commit, training config and benchmark protocol. Preserve
   independent held-out splits; do not tune against the final test split.
2. Train offline with existing training tools and seal an immutable model package.
3. Generate measured evaluation and regression reports. ImprovementReleaseRegistry.candidate
   requires passing gates and binds artifact/evaluation SHA-256, dataset, code and config.
   The report must declare MEASURED independent evaluation, bind the artifact, benchmark,
   dataset and code commit, and provide finite baseline/candidate/margin measurements for
   every gate. Candidate registration computes regressions instead of trusting booleans.
   This does not authenticate report authors or prove that supplied measurements are true.
4. Call qualify in order: offline, shadow, canary, approve. Each requires accountable
   actor and passing evidence. Approval additionally requires human_verified=true.
   Canary means a controlled test stand here, not autonomous physical traffic control.
5. Deploy only SHADOW/ADVISORY, with a distinct approved, verified rollback target.
6. Monitor outcomes, safety interventions, drift and journal loss. Roll back on verified
   regression or operator decision; drift alone never promotes or rolls back a model.

Candidate IDs address immutable manifests. reproduce(model_id) returns verified lineage,
staged qualification and approval; lineage(model_id) verifies manifest,
artifact and evaluation hashes and returns code commit, dataset reference, training
configuration and benchmark. Approval is retained in the model's qualification file.
Qualification and deployment files use integrity-checked atomic JSON. Registration
copies model bytes and evaluation reports into bundles/<model_id> and verifies the
copies before publishing the immutable manifest. reproduce returns retained paths;
deleting original input files does not invalidate the release. Keep referenced
datasets and source commits available; hashes cannot recover deleted external datasets.
The local actor field is an audit identity, not authentication:
restrict filesystem writers and run release operations under authorized operator tooling.
Boolean gate evidence must come from measured benchmark reports, never guessed metrics.

deploy atomically changes a local release pointer; it does not hot-reload a running
controller. rollback verifies the previous artifact and approval, atomically selects it
in SHADOW and is idempotent on retry. The loader/operator must apply the selected release
through a supervised restart, keeping deterministic fallback authoritative throughout.
Automated tests verify pointer selection, retry, corrupt artifacts, promotion order and
ACTIVE denial. Physical switching latency is not measured by these software tests.

Existing vision ModelRegistry handles detector packages; policy releases must use the
staged improvement gate as well. Neither registry is authorization for physical ACTIVE.
There is no automatic deployment, approval, training trigger or production weight update.
