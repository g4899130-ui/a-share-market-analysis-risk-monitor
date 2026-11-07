"""把分析结果保存为 PNG；仅负责展示，不重复计算金融指标。"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


def save_charts(prices: pd.DataFrame, cumulative: pd.DataFrame,
                drawdowns: pd.DataFrame, corr: pd.DataFrame,
                names: dict[str, str], output_dir: Path) -> None:
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    labels = {code: f"{code} {names[code]}" for code in prices}
    charts = (
        (prices, "历史收盘价（不复权）", "收盘价（元）", "close_prices.png", False),
        (cumulative * 100, "累计收益率对比（共同起点=0%）", "累计收益率（%）", "cumulative_returns.png", True),
        (drawdowns * 100, "历史回撤（各股自身净值）", "回撤（%）", "drawdowns.png", True),
    )
    for frame, title, ylabel, filename, zero_line in charts:
        fig, ax = plt.subplots(figsize=(13, 7))
        for code in frame:
            ax.plot(frame.index, frame[code], label=labels[code], linewidth=1.1)
        if zero_line:
            ax.axhline(0, color="gray", linewidth=0.8)
        ax.set(title=title, xlabel="交易日期", ylabel=ylabel)
        ax.grid(alpha=0.2)
        ax.legend(loc="upper left", bbox_to_anchor=(1.01, 1), fontsize=7, title="股票代码 / 名称")
        fig.autofmt_xdate()
        fig.savefig(output_dir / filename, dpi=150, bbox_inches="tight")
        plt.close(fig)

    fig, ax = plt.subplots(figsize=(max(7, len(corr) * 0.65), max(6, len(corr) * 0.6)))
    data = corr.to_numpy(dtype=float)
    image = ax.imshow(np.ma.masked_invalid(data), vmin=-1, vmax=1, cmap="coolwarm")
    ax.set_xticks(range(len(corr.columns)), labels=corr.columns, rotation=90)
    ax.set_yticks(range(len(corr.index)), labels=corr.index)
    ax.set(title="同日收益率 Pearson 相关性热力图", xlabel="股票代码", ylabel="股票代码")
    if len(corr) <= 10:
        for i in range(len(corr.index)):
            for j in range(len(corr.columns)):
                if not np.isnan(data[i, j]):
                    ax.text(j, i, f"{data[i, j]:.2f}", ha="center", va="center", fontsize=8)
    fig.colorbar(image, ax=ax, label="Pearson 相关系数（-1 至 1）")
    fig.savefig(output_dir / "correlation_heatmap.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
