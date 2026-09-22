# ForestFormer3D on the Berlin ALS tiles: status and runbook (2026-09-22)

**Goal.** Run ForestFormer3D (Xiang et al., ICCV 2025, SmartForest-no/ForestFormer3D)
on the Berlin 2021 ALS point clouds, first on FOR-instance plots to prove the
installation, then on Tegel patches.

**Status: blocked on hardware.** Everything that does not need a GPU is staged
(see below). The model cannot run on this Mac (Apple M2, 24 GB, no NVIDIA):

- The official environment is a Docker image `FROM pytorch/pytorch:1.13.1-cuda11.6`
  started with `--gpus all`; it compiles MinkowskiEngine, spconv-cu116,
  torch-scatter and torch-points-kernels with CUDA. Docker Desktop on macOS has no
  NVIDIA passthrough.
- The code is CUDA-bound, not just CUDA-preferring: 8 files import spconv, 6
  import MinkowskiEngine, the loss uses `torch_points_kernels.instance_iou`
  (CUDA kernel), 11 explicit `.cuda()` sites. A CPU/MPS port would be a project
  of its own, not a spike.
- The authors say inference wants an A100-class GPU; smaller cards need a lower
  `chunk` and cylinder `radius` in `configs/oneformer3d_qs_radius16_qp300_2many.py`.
- The two HNEE hosts in known_hosts (`fb1ki.hnee.de`, `10.1.1.250`) do not answer
  from this network; they may be a GPU option from inside HNEE / VPN.

## What is staged (outside this repo)

| item | location |
|---|---|
| training repo (clone) | `~/work/hnee/ForestFormer3D/` |
| inference-only repo (clone), recommended route | `~/work/hnee/FF3D_inference/ff3d_forestsens/` |
| pretrained checkpoint (230 MB, Zenodo 16742708) | `~/work/hnee/FF3D_inference/ff3d_forestsens/work_dirs/clean_forestformer/epoch_3000_fix.pth` (copy also under `ForestFormer3D/work_dirs/`) |
| official FOR-instanceV2 test set (2.8 GB unzipped, .ply) | `~/work/hnee/ForestFormer3D/data/ForAINetV2/test_data/` |
| FOR-instance (v1) annotated plots, 39 .las, 5.4 GB | `~/data/training_data/FORinstance_dataset/` |
| Berlin patches, 100 m, local coords, classes 2-5 | `~/work/hnee/ForestFormer3D_runs/berlin_in/r12_tegel_E381300_N5828300_100m.las` (193k pts), `r13_spandau_E376400_N5827400_100m.las` (289k pts) |

The Berlin patches are the same two hectares as the AMS3D spike
(`2026-09-22-ams3d-spike.md`), so results can be compared with AMS3D and, for
R13, with the reference crown file. Coordinates are shifted by the E/N in the
file name and z by the patch minimum.

## Runbook for a Linux box with an NVIDIA GPU and Docker

```bash
# 1. copy the three folders to the box (rsync -a ...):
#    FF3D_inference/ff3d_forestsens/   (includes the checkpoint)
#    ForestFormer3D_runs/berlin_in/     (Berlin patches)
#    one or two FOR-instance plots for the installation test, e.g.
#    FORinstance_dataset/CULS/plot_2_annotated.las (test split)

# 2. installation test on FOR-instance
cd ff3d_forestsens
mkdir -p /data/ff3d/in /data/ff3d/out
cp .../CULS/plot_2_annotated.las /data/ff3d/in/       # input folder is wiped after the run!
HOST_BUCKET_IN=/data/ff3d/in HOST_BUCKET_OUT=/data/ff3d/out ITERATIONS=1 \
  sudo bash run_docker_locally.sh                     # builds the image on first run (slow)
# output: a zip in /data/ff3d/out with .las carrying semantic_pred / instance_pred / score
# sanity check: compare instance_pred against the treeID column of the input plot

# 3. Berlin patches (sparse ALS, ~20 pts/m^2: expect degraded results, see below)
cp .../berlin_in/*.las /data/ff3d/in/
HOST_BUCKET_IN=/data/ff3d/in HOST_BUCKET_OUT=/data/ff3d/out ITERATIONS=1 \
  sudo bash run_docker_locally.sh
```

If CUDA runs out of memory: lower `chunk` (default 20 000) and `radius` (16 m)
in `configs/oneformer3d_qs_radius16_qp300_2many.py`, or `num_points`
(640 000) in the same config.

## Expectation to keep in mind

FOR-instance / FOR-instanceV2 are drone and terrestrial scans: the NIBIO plot
opened here has ~8 000 pts/m² on a 28 m plot, and the model voxelises at 0.2 m.
The Berlin ALS is an airborne leaf-off scan at ~20 pts/m². That is 2-3 orders of
magnitude sparser than anything in the training data, so the pretrained
checkpoint may under-detect or fragment trees on Tegel even if the installation
test on FOR-instance is perfect. If that happens the options are fine-tuning
on the R13 reference crowns (they are ALS-derived, 538k crowns) or staying with
a geometric method (AMS3D / CHM watershed).
