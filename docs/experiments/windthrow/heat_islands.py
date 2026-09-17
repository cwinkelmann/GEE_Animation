"""Heat islands vs detected fallen trees — an object-level test.

Islands = connected warm patches (>= T_ISLAND K above the footprint mean) in the
post-event summer mean (2025-07 .. 2026-08, Apr-Sep). "New" islands are those
that were NOT warm in the pre-event summers (2022-2024). Windthrow clusters =
connected cells with >= 20 m of predicted stem. Overlap is scored both ways and
against a null from random circular shifts of the stem map.
"""
import glob, re, sys
import numpy as np, rasterio, matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy import ndimage
from scipy.stats import spearmanr
sys.path.insert(0, "/private/tmp/claude-501/-Users-christian-work-hnee-GEE-animation/cb67f53d-9e2d-4a19-b00f-f9ebfe8add3b/scratchpad")
from windthrow_vs_delta import stem_density

POST, PRE, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
T_ISLAND, T_WT, MIN_CELLS, EDGE = 1.0, 20.0, 3, 2
def summer_mean(pattern, lo, hi):
    files = [f for f in sorted(glob.glob(pattern)) if (m := re.search(r"(\d{4}-\d{2})\.tif$", f))
             and lo <= m.group(1) <= hi and int(m.group(1)[5:]) in (4, 5, 6, 7, 8, 9)]
    stack = []
    for f in files:
        with rasterio.open(f) as ds:
            d = ds.read(1).astype("float32"); d[d == ds.nodata] = np.nan; stack.append(d)
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore"); return np.nanmean(np.stack(stack), axis=0), files
post, pf = summer_mean(POST, "2025-07", "2026-08")
pre, prf = summer_mean(PRE, "2022-04", "2024-09")
print(f"post-event months: {len(pf)}, pre-event months: {len(prf)}")
with rasterio.open(pf[0]) as t:
    dens, street = stem_density(t)
inside = np.isfinite(post) & np.isfinite(pre)
core = ndimage.binary_erosion(inside, iterations=EDGE) & ~ndimage.binary_dilation(street, iterations=2)
# --- islands ------------------------------------------------------------------------
warm_post = core & (post >= T_ISLAND); warm_pre = core & (pre >= T_ISLAND)
lab, n = ndimage.label(warm_post)
sizes = ndimage.sum(warm_post, lab, range(1, n + 1))
keep = [i + 1 for i, s in enumerate(sizes) if s >= MIN_CELLS]
rows = []
for i in keep:
    m = lab == i
    rows.append(dict(id=i, cells=int(m.sum()), mean_post=float(post[m].mean()), mean_pre=float(pre[m].mean()),
                     stem_m=float(dens[m].sum()), stem_per_cell=float(dens[m].mean()),
                     new=bool(pre[m].mean() < T_ISLAND * 0.5)))
base_rate = float(dens[core].mean())
wt_share_core = float((dens[core] >= T_WT).mean())
print(f"islands (>= {T_ISLAND} K, >= {MIN_CELLS} cells, edge/street excluded): {len(rows)}, "
      f"new since pre-event: {sum(r['new'] for r in rows)}")
def report(name, rs):
    if not rs: print(f"  {name}: none"); return
    sp = np.array([r['stem_per_cell'] for r in rs]); wt = np.array([r['stem_per_cell'] >= T_WT for r in rs])
    print(f"  {name}: n={len(rs)} | mean stem/cell {sp.mean():.1f} m (footprint base rate {base_rate:.1f} m) | "
          f"{wt.mean()*100:.0f}% are windthrow-dense (base rate of cells {wt_share_core*100:.0f}%) | "
          f"with any stem {np.mean(sp > 0)*100:.0f}%")
report("all islands", rows); report("NEW islands (not warm 2022-24)", [r for r in rows if r['new']])
report("OLD islands (already warm)", [r for r in rows if not r['new']])
# --- windthrow clusters ---------------------------------------------------------------
wt_mask = core & (dens >= T_WT)
lab2, n2 = ndimage.label(wt_mask); sizes2 = ndimage.sum(wt_mask, lab2, range(1, n2 + 1))
cl = []
for i in range(1, n2 + 1):
    m = lab2 == i
    if m.sum() < 2: continue
    cl.append(dict(cells=int(m.sum()), post=float(post[m].mean()), pre=float(pre[m].mean()),
                   island=bool(warm_post[m].mean() > 0.5)))   # share of the cluster's own cells that are warm
if cl:
    p = np.array([c['post'] for c in cl]); q = np.array([c['pre'] for c in cl])
    print(f"windthrow clusters (>= 2 cells of >= {T_WT} m): {len(cl)} | mean post-event Δ {p.mean():+.2f} K "
          f"(pre-event {q.mean():+.2f} K) | {np.mean([c['island'] for c in cl])*100:.0f}% coincide with a heat island | "
          f"clusters warmed by >= 1 K: {np.mean(p - q >= 1.0)*100:.0f}%")
# --- permutation null: shift the stem map randomly, recompute island stem/cell ----------
rng = np.random.default_rng(1)
obs = np.mean([r['stem_per_cell'] for r in rows if r['new']]) if any(r['new'] for r in rows) else np.nan
null = []
newmask = np.zeros_like(core)
for r in rows:
    if r['new']: newmask |= (lab == r['id'])
for _ in range(500):
    sh = np.roll(np.roll(dens, rng.integers(5, dens.shape[0] - 5), 0), rng.integers(5, dens.shape[1] - 5), 1)
    null.append(sh[newmask].mean())
null = np.array(null)
print(f"permutation test (500 random shifts): new-island stem/cell observed {obs:.1f} m vs null {null.mean():.1f} ± {null.std():.1f} m, "
      f"p = {np.mean(null >= obs):.3f}")
# --- change map correlation ----------------------------------------------------------
chg = post - pre
rho = spearmanr(dens[core], chg[core]).statistic
print(f"cell-level: Spearman(stem density, post − pre warming) = {rho:+.3f} over {core.sum()} cells; "
      f"warming in windthrow-dense cells {chg[core & (dens >= T_WT)].mean():+.2f} K vs stem-free {chg[core & (dens == 0)].mean():+.2f} K")
# --- figure -----------------------------------------------------------------------
fig, axes = plt.subplots(1, 3, figsize=(19, 6.6), facecolor="white")
ax = axes[0]; im = ax.imshow(np.where(inside, chg, np.nan), cmap="RdBu_r", vmin=-3, vmax=3, interpolation="nearest")
ax.contour(newmask.astype(float), levels=[0.5], colors="k", linewidths=0.8)
ax.set_title("Warming since 2022–24 summers (post − pre), K\nblack outline = new heat islands", loc="left", fontsize=11)
fig.colorbar(im, ax=ax, fraction=0.046, pad=0.02).set_label("K")
ax = axes[1]; im = ax.imshow(np.where(inside, dens, np.nan), cmap="Greys", vmin=0, vmax=60, interpolation="nearest")
ax.contour(newmask.astype(float), levels=[0.5], colors="#c44", linewidths=0.8)
ax.set_title("Predicted stem length per 20 m cell\nred outline = the same new heat islands", loc="left", fontsize=11)
fig.colorbar(im, ax=ax, fraction=0.046, pad=0.02).set_label("m / cell")
ax = axes[2]
for flag, col, name in ((True, "#c44", "new island"), (False, "#888", "already warm 2022–24")):
    rs = [r for r in rows if r['new'] == flag]
    ax.scatter([r['stem_per_cell'] for r in rs], [r['mean_post'] for r in rs], s=[8 + r['cells'] * 3 for r in rs],
               color=col, alpha=0.7, edgecolor="white", label=name)
ax.axvline(T_WT, color="#aaa", lw=1, ls="--"); ax.set_xlabel("stem length per cell inside the island, m"); ax.set_ylabel("island mean Δ, K")
ax.set_title("Heat islands: warmth vs fallen trees (size = area)", loc="left", fontsize=11); ax.legend(frameon=False); ax.grid(alpha=0.25)
for a in axes[:2]:
    a.set_xticks([]); a.set_yticks([])
for a in axes:
    for s in a.spines.values(): s.set_visible(False)
fig.tight_layout(); fig.savefig(OUT, dpi=110); print("wrote", OUT)
