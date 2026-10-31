"""时段计价：北京时间工作日高峰，其余空闲。

高峰：周一至周五 09:00–12:00、14:00–18:00（不含法定节假日）。
空闲：其余时间、周末、法定节假日全天。空闲价 = 高峰价的一半。
官方没有查询当前时段的接口，只能本地按规则算；节假日列表由配置维护。
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

from .config import Pricing

CN = timezone(timedelta(hours=8))
PEAK_WINDOWS = ((time(9, 0), time(12, 0)), (time(14, 0), time(18, 0)))


def is_off_peak(now: datetime | None = None, holidays: list[str] | None = None) -> bool:
    now = (now or datetime.now(timezone.utc)).astimezone(CN)
    if now.weekday() >= 5:  # 周六、周日
        return True
    if holidays and now.strftime("%Y-%m-%d") in holidays:
        return True
    t = now.time()
    return not any(start <= t < end for start, end in PEAK_WINDOWS)


def current_rates(p: Pricing, now: datetime | None = None) -> tuple[float, float, float, str]:
    """返回 (命中价, 未命中价, 输出价, 时段名)，单位：元/百万 token。"""
    if is_off_peak(now, p.holidays):
        return p.off_input_hit, p.off_input_miss, p.off_output, "空闲"
    return p.input_hit, p.input_miss, p.output, "高峰"
