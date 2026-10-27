"""字段级证据绑定（U2）。

把答案 event 里的 time / deadline / location / participants / file_name
逐个绑定到"能支撑它"的那条消息与原文片段，供 validate.py 做字段级校验。

原则：
- 值不是"出现在任意位置"就算数，必须能从该 quote 推导出来
  （地点做别名归一、时间按该消息 send_time 换算、文件名做模糊匹配）。
- 推导不出来就不绑定，绝不推测补全。
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from typing import Optional

# 字段顺序固定，保证同一输入产出顺序一致（可复现）
FIELD_ORDER = ("time", "deadline", "location", "participants", "file_name")

# 地点别名：4.1 别名表的落地（南门口 = 南门）；只做等价归一，不做语义扩展
LOCATION_ALIASES = {
    "南门口": "南门",
    "北门口": "北门",
    "东门口": "东门",
    "西门口": "西门",
    "正门口": "正门",
}

# 可识别的地点词（用于冲突检测）；按长度降序匹配，避免"南门"先吃掉"南门口"
LOCATION_TERMS = ("南门口", "北门口", "东门口", "西门口", "正门口", "南门", "北门", "东门", "西门", "正门")

_CN_WEEKDAY = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
_WEEK_OFFSET = {"这": 0, "本": 0, "下": 1, "上": -1}

_ABS_DATE = re.compile(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})[日号]?")
_MONTH_DAY = re.compile(r"(?<!\d)(\d{1,2})月(\d{1,2})[日号]")
_WEEKDAY = re.compile(r"(这|本|下|上)?个?(?:周|星期|礼拜)([一二三四五六日天])")
_WEEKEND = re.compile(r"(这|本|下|上)?(?:周|星期|礼拜)?末")
_MONTH_REL = re.compile(r"(这|本|下|上)个?月")

_FILE_EXT = re.compile(r"\.[A-Za-z0-9]{1,6}$")


@dataclass
class FieldEvidence:
    """一条字段级证据：哪个字段、值是什么、由哪条消息的哪段原文支撑。"""

    field: str
    value: object
    msg_id: str
    quote: str
    method: str  # quote_substring / token_match


def content_of(entry) -> str:
    """corpus 条目兼容两种形式：str 或 {"content": ..., "send_time": ...}。"""
    if isinstance(entry, dict):
        return entry.get("content") or ""
    return entry or ""


def send_time_of(entry) -> Optional[str]:
    """取 corpus 条目的 send_time（相对时间换算的基准）。"""
    if isinstance(entry, dict):
        return entry.get("send_time")
    return None


def _plain(text) -> str:
    """去掉所有空白，便于中文子串/别名比较。"""
    return re.sub(r"\s+", "", str(text or ""))


def normalize_location(value) -> str:
    """地点归一：去空白、去"集合地点/地点"前缀、别名等价（南门口→南门）。"""
    text = _plain(value)
    text = re.sub(r"^(集合地点|集合点|地点)[:：]?", "", text)
    for alias, canonical in LOCATION_ALIASES.items():
        text = text.replace(alias, canonical)
    return text


def normalize_file_stem(value) -> str:
    """文件名去扩展名。"""
    return _FILE_EXT.sub("", _plain(value))


def parse_send_time(value) -> Optional[datetime]:
    """解析消息发送时间，作为相对日期换算的基准。"""
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def parse_value_date(value) -> Optional[date]:
    """把答案字段里的时间值解析成日期（只认明确日期，不猜）。"""
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    match = re.match(r"(\d{4})年(\d{1,2})月(\d{1,2})", text)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None
    return None


def quote_dates(quote: str, send_time) -> set[date]:
    """把 quote 里出现的日期（绝对/相对）按 send_time 换算成具体日期集合。

    相对规则：今天/明天/后天/大后天、周X/星期X/礼拜X（可带这/本/下/上）、周末。
    周按周一起算。send_time 缺失时不换算相对时间（返回空集）。
    """
    text = _plain(quote)
    base = parse_send_time(send_time)
    if not text or base is None:
        return set()

    dates: set[date] = set()
    for match in _ABS_DATE.finditer(text):
        try:
            dates.add(
                date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            )
        except ValueError:
            continue
    for match in _MONTH_DAY.finditer(text):
        try:
            dates.add(date(base.year, int(match.group(1)), int(match.group(2))))
        except ValueError:
            continue

    # 相对"天"：长词优先，避免"大后天"被当成"后天"
    if "大后天" in text:
        dates.add((base + timedelta(days=3)).date())
    elif "后天" in text:
        dates.add((base + timedelta(days=2)).date())
    if "明天" in text:
        dates.add((base + timedelta(days=1)).date())
    if "今天" in text or "今日" in text:
        dates.add(base.date())
    if "昨天" in text:
        dates.add((base - timedelta(days=1)).date())
    if "前天" in text:
        dates.add((base - timedelta(days=2)).date())

    monday = base.date() - timedelta(days=base.weekday())
    for match in _WEEKDAY.finditer(text):
        prefix = match.group(1) or "这"
        weekday = _CN_WEEKDAY.get(match.group(2))
        if weekday is None:
            continue
        dates.add(monday + timedelta(weeks=_WEEK_OFFSET[prefix], days=weekday))
    for match in _WEEKEND.finditer(text):
        prefix = match.group(1) or "这"
        dates.add(monday + timedelta(weeks=_WEEK_OFFSET[prefix], days=6))

    return dates


def quote_months(quote: str, send_time) -> set[tuple[int, int]]:
    """quote 里的"这个月/下个月/上个月"换算成 (年, 月)。"""
    base = parse_send_time(send_time)
    if base is None:
        return set()
    months = set()
    for match in _MONTH_REL.finditer(_plain(quote)):
        offset = _WEEK_OFFSET.get(match.group(1), 0)
        year, month = base.year, base.month + offset
        while month > 12:
            month -= 12
            year += 1
        while month < 1:
            month += 12
            year -= 1
        months.add((year, month))
    return months


def time_supports(value, quote: str, send_time) -> bool:
    """时间字段能否由 quote 推导：换算后的日期必须命中，或原文写了同一日期。"""
    value_date = parse_value_date(value)
    if value_date is None:
        return False
    text = _plain(quote)
    if value_date in quote_dates(quote, send_time):
        return True
    if (value_date.year, value_date.month) in quote_months(quote, send_time):
        return True
    if f"{value_date.year}-{value_date.month:02d}-{value_date.day:02d}" in text:
        return True
    if f"{value_date.month}月{value_date.day}日" in text:
        return True
    if f"{value_date.month}月{value_date.day}号" in text:
        return True
    return False


def location_supports(value, quote: str) -> bool:
    """地点字段能否由 quote 推导（别名归一后做子串匹配）。"""
    normalized = normalize_location(value)
    return bool(normalized) and normalized in normalize_location(quote)


def participants_supported(value, quote: str) -> bool:
    """参与人：每个名字都必须出现在 quote 里。"""
    if not isinstance(value, list) or not value:
        return False
    text = _plain(quote)
    return all(_plain(name) and _plain(name) in text for name in value)


def file_supports(value, quote: str) -> bool:
    """文件名：去扩展名后，原文字串包含或用滑动窗口做>=0.8 的模糊匹配。"""
    stem = normalize_file_stem(value)
    if not stem:
        return False
    text = _plain(quote)
    if stem in text:
        return True
    if not text:
        return False
    window = len(stem)
    for start in range(0, max(1, len(text) - window + 1)):
        if SequenceMatcher(None, stem, text[start : start + window]).ratio() >= 0.8:
            return True
    return False


def supports(field: str, value, quote: str, send_time) -> bool:
    """按字段类型判断 quote 能否推导该值。"""
    if field in ("time", "deadline"):
        return time_supports(value, quote, send_time)
    if field == "location":
        return location_supports(value, quote)
    if field == "participants":
        return participants_supported(value, quote)
    if field == "file_name":
        return file_supports(value, quote)
    return False


def bind_evidence(event: dict, corpus: dict) -> list[FieldEvidence]:
    """把 event 的各字段绑定到第一条能支撑它的 source 上。

    绑定不到的字段不会出现在结果里（由调用方置 null）。
    """
    sources = event.get("sources") or []
    evidences: list[FieldEvidence] = []
    for field in FIELD_ORDER:
        value = event.get(field)
        if not value:
            continue
        for source in sources:
            msg_id = source.get("msg_id")
            quote = source.get("quote") or ""
            entry = corpus.get(msg_id)
            if entry is None:
                continue
            if not supports(field, value, quote, send_time_of(entry)):
                continue
            method = (
                "quote_substring"
                if _plain(str(value)) in _plain(quote)
                else "token_match"
            )
            evidences.append(
                FieldEvidence(
                    field=field,
                    value=value,
                    msg_id=msg_id,
                    quote=quote,
                    method=method,
                )
            )
            break
    return evidences


def location_candidates(sources: list[dict], corpus: dict) -> list[dict]:
    """从通过 L1 的 quote 里收集出现过的地点（用于冲突检测）。"""
    found = []
    seen = set()
    for source in sources:
        quote = source.get("quote") or ""
        text = _plain(quote)
        for term in LOCATION_TERMS:
            if term not in text:
                continue
            value = normalize_location(term)
            key = (source.get("msg_id"), value)
            if key in seen:
                continue
            seen.add(key)
            found.append(
                {"msg_id": source.get("msg_id"), "quote": quote, "value": value}
            )
    return found


def time_candidates(sources: list[dict], corpus: dict) -> list[dict]:
    """从 quote 里收集可换算出的日期（用于时间冲突检测）。"""
    found = []
    seen = set()
    for source in sources:
        msg_id = source.get("msg_id")
        quote = source.get("quote") or ""
        entry = corpus.get(msg_id)
        for value in sorted(quote_dates(quote, send_time_of(entry))):
            key = (msg_id, value.isoformat())
            if key in seen:
                continue
            seen.add(key)
            found.append(
                {"msg_id": msg_id, "quote": quote, "value": value.isoformat()}
            )
    return found
