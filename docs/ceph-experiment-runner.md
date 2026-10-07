# Ceph Gray–Scott experiment runner

The runner analyzes an existing BP5 dataset on a mapped RBD filesystem, records client metrics, validates the analysis output, and optionally measures an explicitly authorized OSD failure. `run_baseline_trials.py` runs repeated independent single-client and concurrent two-client baselines.

## Environment

The current CloudLab setup uses Ubuntu 22.04, Python 3.10, Ceph Reef 18.2.8, ADIOS2 2.10.2, Open MPI 4.1.2, psutil and matplotlib. Build `adios2-pdf-calc` from `source/cpp/gray-scott` into `build-grayscott/`. The repository-root `adios2.xml` configures BP5/stdIO for analysis. Default ADIOS2 library and bpls paths are under `/users/vlad777/software/adios2`; adapt them for another installation (`--bpls` and `--binary` are configurable).

Clients need their own Ceph query identities and pool-scoped RBD identities, root-owned keyrings, and passwordless sudo for named Ceph queries. Fault control also needs verified root SSH access to node0 and OSD hosts. The helper uses root SSH via `sudo -n` when the runner is not root. It discovers active package or cephadm service units instead of guessing a cephadm unit name. Admin Ceph mutations execute on node0; clients do not need the admin keyring.

Verify disk identities, mounts, image mappings, network interface and cluster state before experimenting. This code does not provision disks or create/delete pools. Never modify a physical OS disk. Obtain explicit authorization before running a real fault.

## No-fault baseline

Run from the repository root on a client and choose a fresh output path:

```bash
python3 scripts/run_recovery_experiment.py \
  --no-fault --mpi-procs 8 --expected-steps 3600 \
  --input /mnt/grayscott/gray-scott/gs-900g.bp \
  --output /mnt/grayscott/analysis/baseline-01.bp \
  --output-dir results/paper-baselines \
  --expected-data-pool grayscott-900g \
  --network-interface enp6s0f1 --collect-osd-perf
```

`--expected-steps` and a fresh `--output` are mandatory. The runner verifies HEALTH_OK/clean PGs, input step counts, the mounted RBD mapping, expected EC pool and actual CRUSH selection using OSD failure domain. `--ceph-name` chooses a query identity and `--rbd-name` the identity used to inspect the mapped image. Defaults correspond to client.node12/node13 and client.grayscott-node12/node13. Changing EC codes requires a fresh data pool/image and appropriate credentials.

No-fault mode never invokes OSD control. Successful trials must exit cleanly, produce the expected histogram step counts and throughput rows, pass sampled final-step readback, retain valid monitoring, and finish with HEALTH_OK. A failed/incomplete trial is recorded as such and exits nonzero. Existing outputs are not overwritten.

## Authorized recovery trial

After choosing an OSD and obtaining explicit authorization, use an OSD ID and `--confirm-fault` instead of `--no-fault`:

```bash
python3 scripts/run_recovery_experiment.py 0 --confirm-fault \
  --mpi-procs 8 --expected-steps 3600 --fault-delay 280 \
  --input /mnt/grayscott/gray-scott/gs-900g.bp \
  --output /mnt/grayscott/analysis/authorized-fault-01.bp \
  --output-dir results/authorized-faults --collect-osd-perf
```

The flag expresses intent; it does not replace approval. The helper stops the verified OSD service and waits for the MON's configured down/out interval. Faults are skipped if analysis has already completed; a requested-fault trial then fails validation. MPI completion is observed independently using monotonic time.

Recovery uses fresh OSD/PG JSON polling after the request, never journal backlog. Remapping completion requires the target down/out and all PGs clean. The runner then restores the service and marks the OSD in, observing restoration until the target is up/in and PGs are clean. Monitoring continues through both phases. SIGINT/SIGTERM/SIGHUP and errors trigger MPI process-group cleanup, an attempted OSD restoration, and saved failure status. `--no-restore` deliberately leaves the target out and should only be used when specifically authorized.

Recovery milestones are observations with uncertainty approximately the polling interval plus command latency. A brief recovery state can be missed; missing times remain null. Raw state snapshots are saved in recovery-events.jsonl and restoration-events.jsonl. Healthy PGs after remapping are distinguished from restoring all OSDs up/in. Live fault behavior still requires an authorized validation run; the current regression suite uses mocked controls.

## Repeated single/two-client baseline trials

Run the controller on node0, using a fresh local results directory:

```bash
python3 scripts/run_baseline_trials.py --trials 5 --seed 42 --mpi-procs 8 \
  --output-dir /users/vlad777/research/paper-baselines-new
```

The controller uses node12/node13, with five runs per condition in seeded random order. Each client independently reads the full input; this measures concurrency/interference rather than distributing one MPI job. Each client must mount its own image or snapshot clone; never mount the same ordinary ext4 image read/write on two hosts. A UTC barrier schedules simultaneous starts; synchronize clocks and inspect recorded start skew. The controller saves the schedule, per-job and aggregate logical throughput, and copies measurement artifacts back to node0. Interrupted runs request termination only for matching trial IDs.

Cache policy defaults to natural. The controller does not drop client/OSD caches; a warm label on an individual runner requires an explicit prior warm-up pass. Snapshot clones share backend objects and cache effects. Establish a cache policy, repeat trials, and report uncertainty before drawing paper conclusions.

## Measurements and interpretation

- `results.json`: status/errors, monotonic analysis duration, exact logical input bytes and job throughput; arguments, pool/profile/rule/image mapping, software hashes/versions, rank/binding information and cache label; recovery/restoration state observations.
- `perf.csv`: UTC epoch timestamp, elapsed time/phase, whole-client CPU/RAM, RBD filesystem space, RBD read/write throughput and IOPS, interval-average accounted device latency, verified-interface Rx/Tx, and analysis process-tree RSS/CPU.
- `*_throughput.csv`: exact global byte counts summed across MPI ranks, maximum rank phase times, global phase rates and cumulative phase times. The old rank0-only CSV schema must not be interpreted as this new schema.
- `osd-perf.jsonl`: optional Ceph-reported average OSD operation latency snapshots throughout the trial. This does not collect server CPU/disk utilization.
- `analysis.log`: application output, exit diagnostics and MPI rank bindings. `nofault_io.png`: baseline plot with explicit pool labels and MiB units; matplotlib must already be installed.

Legacy perf columns ending in `_mb` use MiB; rates use MiB/s. The first rate sample is empty because it has no previous sample. Process CPU sums may exceed 100%; summed RSS can count shared pages repeatedly. Device latency is Linux accounted milliseconds/completed operations, not application p95/p99. Job wall time includes launch/close; phase rates time ADIOS operations and are not pure storage bandwidth. Client filesystem caching/buffering remains enabled, so this is not a durability benchmark.

The C++ analyzer accumulates exact uint64 bytes before converting to MiB, avoiding small-write truncation. Output validation checks metadata and sampled final-step values, not full numerical equivalence; use a reference analysis for numerical correctness claims.

## Validation

```bash
python3 -m unittest discover -s tests -p test_recovery_experiment.py
```

Thirteen regression tests cover late faults, partial-stop restoration, service discovery/identity, clean-before-markout, restored up/in state, polling/monitor failures, MPI failure, output validation and explicit fault intent. Small L=32/four-step/four-rank no-fault runs passed on both clients, with exactly 2,097,152 input bytes and 211,216 output bytes. A controller smoke test passed both single and concurrent dual conditions with start-barrier and artifact-copy checks. No real OSD fault was injected during this validation; the cluster remained HEALTH_OK with 24 OSDs up/in.
