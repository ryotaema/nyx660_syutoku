"""run_offline.py / run_live.py 共通の出力（ログCSV・推移グラフ）。"""

import csv

import numpy as np


def write_csv(path, rows):
    if not rows:
        return
    with open(path, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def plot_timeline(path, table, runs, gear_labels=None, title=None):
    """runs: {ラベル: フレームごとの行（gear / lum / score を持つ dict）のリスト}"""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(3, 1, figsize=(11, 8), sharex=True)
    for label, rows in runs.items():
        f = [r['frame'] for r in rows]
        axes[0].step(f, [r['gear'] for r in rows], where='post', label=str(label))
        axes[1].plot(f, [np.nan if r['lum'] is None else r['lum'] for r in rows])
        axes[2].plot(f, [r['score'] for r in rows])
    lo, hi = table.params.target_lum
    axes[1].axhspan(lo, hi, color='g', alpha=0.1)
    axes[0].set_yticks(range(len(table.gears)))
    axes[0].set_yticklabels(gear_labels or [f'{i}: {g.exposure}us g{g.gain:g}' for i, g in enumerate(table.gears)],
                            fontsize=7)
    axes[0].set_ylabel('gear')
    axes[1].set_ylabel('lum')
    axes[2].set_ylabel('score (sum conf)')
    axes[2].set_xlabel('frame')
    if len(runs) > 1:
        axes[0].legend(fontsize=7, ncol=4)
    axes[0].set_title(title or f'{table.model} {table.env}')
    for ax in axes:
        ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)
