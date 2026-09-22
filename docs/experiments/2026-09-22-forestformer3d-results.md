# ForestFormer3D on the Berlin ALS tiles: results (2026-09-22)

Follow-up to `2026-09-22-forestformer3d-runbook.md`. The model was run on
**carrot** (HNEE box, 8× H100, rootless Docker) at `/raid/cwinkelmann/ff3d/`.

## Answer

ForestFormer3D installs and runs on carrot, passes the installation test on a
FOR-instance plot perfectly, and on the sparse Berlin ALS (20 pts/m², leaf-off)
its network output is **as good as or better than the AMS3D spike and on par
with the reference crown file**, but the tool's post-filter (built for dense
drone/TLS scans) throws three quarters of the trees away. Use the pre-filter
instances, or lower the filter threshold, on ALS.

## Installation test: FOR-instance CULS plot 2 (test split, 3.9 M points)

| | value |
|---|---|
| annotated trees (treeID > 0) | 20 |
| detected at IoU ≥ 0.5 | 20 / 20 |
| median per-tree IoU | 0.996 |
| extra instances | 29, all trees outside the annotated plot (FOR-instance class 3) |

So the pipeline, checkpoint and H100 build are correct.

## Berlin patches (same hectares as the AMS3D spike)

Two 100 m × 100 m patches, one inference iteration unless stated. "pre-filter"
= the network's merged instances (`round_1/*.ply`); "post-filter" = after the
tool's noise filter (`round_1_after_remove_noise_200`). R13 is compared with
the reference crown file exactly as in the AMS3D spike (apex inside reference
polygon, height within 3 m, one match per reference crown).

| run | R12 trees/ha | R13 trees/ha | R13 recall | R13 precision | R13 height RMSE | R13 mean diam |
|---|---|---|---|---|---|---|
| reference crown file | (none) | 407 | | | | 5.8 m |
| AMS3D spike, config C | 339* | 339 | 0.52 | 0.63 | 0.76 m | 7.9 m |
| FF3D iter 1, pre-filter | 377 | 397 | **0.57** | 0.58 | **0.54 m** | **5.9 m** |
| FF3D iter 1, post-filter | 110 | 106 | 0.21 | 0.79 | 0.62 m | 6.6 m |
| FF3D iter 2, post-filter | 155 | 139 | (drive unmounted, pending) | | | |

\* AMS3D R12 count is from the default kernel, not config C.

- **Semantic head is right on ALS.** 118k of the 121k vegetation points above
  2 m in R12 are labelled "tree"; ground and low vegetation are separated.
- **Instance head is right too.** Pre-filter, 85–86 % of canopy points sit in
  an instance, the cross-section shows whole trees with no vertical splitting
  (the AMS3D failure mode), and crown diameters match the reference
  distribution (5.9 vs 5.8 m) where AMS3D had to over-merge (7.9 m).
- **The post-filter is the problem.** `tools/merge_prediction.py` scores each
  instance as (occupied 0.2 m voxels) / (height of its lowest point above
  ground) and drops scores < 200. A tree in a 8 000 pts/m² scan fills thousands
  of voxels; at 20 pts/m² a tree fills a few hundred, so most real trees fail.
  Lower the threshold (line 323) to ~20 for ALS, or skip the filter.
- **Second iteration** (re-inference on unassigned "blue" points, the authors'
  recipe for dense stands) raises the post-filter count by ~40 trees/ha but
  cannot beat the filter; its pre-filter output was not retained by the tool.
- **Speed.** ~2 min per hectare of pure inference after an 11 min PTX JIT
  warm-up per `test.py` invocation (the image is built for sm_80/8.6+PTX, the
  H100 is sm_90 and CUDA 11.6 cannot target it). Per invocation, not per
  patch: batch many patches per run. The full 515 ha R12 footprint is a few
  hours, not days.

Figures: `ff3d_r13_prefilter.png`, `ff3d_r13_postfilter.png`,
`ff3d_r12_prefilter.png` (Tegel). Stats: `ff3d_*_stats.json`,
`*_forinstance_eval.json`. Evaluation code: `ff3d_eval.py`.

## Carrot specifics learned the hard way

- The run script bind-mounts the project dir over `/workspace`, which hides
  the `segmentator` package the Dockerfile built there → `ModuleNotFoundError`.
  Fix: `docker run --rm -v $PROJECT:/host --entrypoint bash IMAGE -lc "cp -r /workspace/segmentator /host/"`.
- `--gpus all` was changed to `--gpus "device=${GPU_DEVICE:-7}"` (shared box).
  Env vars like `ITERATIONS` are not forwarded into the container; edit the
  default in `tools/inference_bluepoint_forestsens.sh`.
- Never `mv` the host output directory while the container exists: the bind
  mount keeps pointing at the old inode and results land there.
- `KEEP_ONLY_ZIP=true` (default in `run_oracle_pipeline.sh`) purges everything
  but the final zip, and with `ITERATIONS=1` the "final" folder name is wrong
  (`round_2_*`), so nothing is exported: copy `out/` before the next run.
- Inference log: `/raid/cwinkelmann/ff3d/run*.log`; image
  `forestformer-forestsens-image`; container `forestformer-forestsens-container`.

## Recommendation

Use ForestFormer3D for crowns on R12: with the noise threshold lowered it
should give ~400 trees/ha with correct crown sizes at ~2 min/ha on carrot. Next
steps: (1) rerun both patches with threshold 20 and two iterations to confirm
the post-filter numbers reach the pre-filter ones; (2) compare R13 against the
reference once the 2TB drive is remounted; (3) tile the R12 footprint into
100–200 m patches with a 10 m overlap and batch them in one run.
