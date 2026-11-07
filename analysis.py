"""从 SQLite 读取不复权收盘价，计算透明、可复现的历史风险收益指标。"""

from __future__ import annotations

import math
import sqlite3
from pathlib import Path

import pandas as pd

from config import (ANNUAL_VOLATILITY_ALERT, MAX_DRAWDOWN_ALERT,
                    MIN_OBSERVATIONS, OUTLIER_RETURN, TARGET_OBSERVATIONS)


def load_bars(conn: sqlite3.Connection, start: str | None = None,
              end: str | None = None, codes: list[str] | None = None) -> pd.DataFrame:
    """读取并检查代码、日期、收盘价；不对真实缺失价格插值。"""
    for value in (start, end):
        if value is not None:
            pd.to_datetime(value, format="%Y-%m-%d", exact=True, errors="raise")
    if start and end and start > end:
        raise ValueError("--start must not be later than --end")
    if codes:
        if any(len(code) != 6 or not code.isdigit() for code in codes):
            raise ValueError("Stock codes must contain six digits")
        codes = list(dict.fromkeys(codes))
    sql = "SELECT b.code, s.name, b.trade_date, b.close FROM daily_bars b JOIN stocks s ON s.code=b.code WHERE 1=1"
    params: list[str] = []
    if start:
        sql += " AND b.trade_date >= ?"
        params.append(start)
    if end:
        sql += " AND b.trade_date <= ?"
        params.append(end)
    if codes:
        sql += " AND b.code IN (" + ",".join("?" for _ in codes) + ")"
        params.extend(codes)
    bars = pd.read_sql_query(sql + " ORDER BY b.code, b.trade_date", conn, params=params)
    if bars.empty:
        raise ValueError("No historical bars in the requested range")
    if codes and set(codes) != set(bars.code):
        raise ValueError(f"No bars for: {', '.join(sorted(set(codes) - set(bars.code)))}")
    if bars.duplicated(["code", "trade_date"]).any():
        raise ValueError("Duplicate code/date rows")
    bars["trade_date"] = pd.to_datetime(bars.trade_date, format="%Y-%m-%d", errors="raise")
    bars["close"] = pd.to_numeric(bars.close, errors="coerce")
    if bars.close.isna().any() or (bars.close <= 0).any():
        raise ValueError("Missing or nonpositive close price")
    return bars.sort_values(["code", "trade_date"]).reset_index(drop=True)


def daily_returns(prices: pd.DataFrame) -> pd.DataFrame:
    """日收益率 = 今日收盘价 / 上一交易日收盘价 - 1；首日和缺口保持 NaN。

    行索引为所有股票日期的并集。若某股缺少其中一天，下一天不跨缺口计算收益。
    """
    prices = prices.sort_index()
    return prices.pct_change(fill_method=None)


def cumulative_returns(prices: pd.DataFrame) -> pd.DataFrame:
    """在所有股票共同存在的首日设为 0%，用各自收盘价标准化。"""
    common = prices.dropna(how="any").sort_index()
    if common.empty:
        raise ValueError("No common trading date for selected stocks")
    return common.div(common.iloc[0]).sub(1)


def drawdown(prices: pd.Series) -> pd.Series:
    """先构建净值 close/首日close，再除以此前最高净值减 1；最小值为最大回撤。"""
    prices = prices.dropna().sort_index()
    if prices.empty or (prices <= 0).any():
        raise ValueError("Drawdown requires positive prices")
    nav = prices / prices.iloc[0]
    return nav / nav.cummax() - 1


def correlation(returns: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """仅用两只股票同日均有有效日收益率的样本计算 Pearson 相关系数。"""
    return returns.corr(method="pearson", min_periods=2), returns.notna().astype(int).T.dot(returns.notna().astype(int))


def summarize(prices: pd.DataFrame, names: dict[str, str]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """在共同起止日期比较收益；波动率用日收益率样本标准差乘 sqrt(252)。"""
    prices = prices.sort_index().sort_index(axis=1)
    common = prices.dropna(how="any")
    if len(common) < 2:
        raise ValueError("At least two common trading dates are needed")
    first, last = common.index[0], common.index[-1]
    period = prices.loc[first:last]
    returns = daily_returns(period)
    cumulative = cumulative_returns(period)
    corr, overlap = correlation(returns)
    rows = []
    drawdowns = {}
    for code in period:
        series = period[code].dropna()
        valid_returns = returns[code].dropna()
        dd = drawdown(series)
        drawdowns[code] = dd
        daily_std = valid_returns.std(ddof=1) if len(valid_returns) >= 2 else math.nan
        annual_vol = daily_std * math.sqrt(252)
        max_dd = dd.min()
        warnings = []
        if len(valid_returns) < MIN_OBSERVATIONS:
            warnings.append("样本不足60个收益率，指标仅供演示")
        elif len(valid_returns) < TARGET_OBSERVATIONS:
            warnings.append("少于约一年有效交易日")
        if pd.notna(annual_vol) and annual_vol > ANNUAL_VOLATILITY_ALERT:
            warnings.append("高波动")
        if -max_dd > MAX_DRAWDOWN_ALERT:
            warnings.append("较大回撤")
        outliers = int((valid_returns.abs() > OUTLIER_RETURN).sum())
        if outliers:
            warnings.append(f"{outliers}个异常日收益率待核查")
        missing = int(period[code].isna().sum())
        if missing:
            warnings.append(f"{missing}个共同日缺失价格/停牌待核查")
        rows.append({
            "code": code, "name": names[code], "start_date": first.date().isoformat(),
            "end_date": last.date().isoformat(), "price_days": len(series),
            "return_days": len(valid_returns), "missing_union_dates": missing,
            "outlier_days": outliers, "cumulative_return": series.iloc[-1] / series.iloc[0] - 1,
            "daily_return_std": daily_std, "annualized_volatility": annual_vol,
            "max_drawdown": max_dd, "risk_alert": "；".join(warnings) or "未触发演示阈值",
        })
    return pd.DataFrame(rows), returns, cumulative, pd.DataFrame(drawdowns), corr, overlap


def run_analysis(conn: sqlite3.Connection, output_dir: Path, *, start: str | None = None,
                 end: str | None = None, codes: list[str] | None = None) -> pd.DataFrame:
    """执行分析，保存 CSV、PNG 和简短口径说明。"""
    from visualization import save_charts

    bars = load_bars(conn, start, end, codes)
    names = bars.drop_duplicates("code").set_index("code")["name"].to_dict()
    prices = bars.pivot(index="trade_date", columns="code", values="close")
    summary, returns, cumulative, drawdowns, corr, overlap = summarize(prices, names)
    output_dir.mkdir(parents=True, exist_ok=True)
    summary.to_csv(output_dir / "risk_return_summary.csv", index=False, encoding="utf-8-sig", float_format="%.6f")
    corr.to_csv(output_dir / "return_correlation.csv", encoding="utf-8-sig", float_format="%.6f")
    overlap.to_csv(output_dir / "correlation_overlap_days.csv", encoding="utf-8-sig")
    returns.to_csv(output_dir / "daily_returns.csv", encoding="utf-8-sig", float_format="%.6f")
    save_charts(prices, cumulative, drawdowns, corr, names, output_dir)
    first = summary.start_date.iloc[0]
    last = summary.end_date.iloc[0]
    report = (f"# 金融分析结果\n\n数据：项目 SQLite `daily_bars`，不复权收盘价；共同起止日 {first} 至 {last}。"
              f"共 {len(summary)} 只股票，日收益率最少 {summary.return_days.min()} 个。\n\n"
              "累计收益率=期末/期初-1；日波动率=日收益率样本标准差；年化波动率=日波动率×√252；"
              "最大回撤=min(净值/历史最高净值-1)。相关性按同日有效收益率逐对计算 Pearson 系数，"
              "重叠样本数见 `correlation_overlap_days.csv`。\n\n"
              "不复权价格没有调整分红、送转和拆股，可能扭曲收益率、波动率与回撤；"
              "结果只说明价格序列的历史表现，不含股息总回报。超过 20% 的单日收益率仅标记，不自动删除。"
              "风险阈值为演示规则，不是投资建议。原始数据库时间跨度不足时，年化值仅为数学换算，可靠性有限。\n")
    (output_dir / "analysis_report.md").write_text(report, encoding="utf-8")
    print(summary.to_string(index=False, formatters={
        "cumulative_return": "{:.2%}".format,
        "annualized_volatility": "{:.2%}".format,
        "max_drawdown": "{:.2%}".format,
    }))
    print(f"Saved analysis to {output_dir}")
    return summary
