"""演示用风险规则；阈值可修改，不构成投资建议。"""

ANNUAL_VOLATILITY_ALERT = 0.40  # 年化历史波动率超过 40%
MAX_DRAWDOWN_ALERT = 0.20        # 最大回撤超过 20%（绝对值）
OUTLIER_RETURN = 0.20            # 单日收益率绝对值超过 20% 时标记核查
MIN_OBSERVATIONS = 60            # 少于 60 个日收益率时提示样本不足
TARGET_OBSERVATIONS = 200        # 约一年样本的提示门槛，并非计算必需
