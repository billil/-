"""提示词注入防护。

sanitize(text)：
1. 单条文本截断到 2000 字符（含前置标记后仍不超过 2000）；
2. 命中注入模式（忽略以上指令 / ignore previous instructions / system: / 你现在是 /
   开发者模式 等，大小写不敏感）时，命中片段转为全角字符，并在整段前置 [群聊内容] 标记；
3. 未命中时只做截断，正常中文消息不受影响。

注意：只处理"进入大模型"的文本，不改动入库原文，保证引用校验仍可逐字溯源。
"""

import re

MAX_TEXT_LEN = 2000
INJECTION_MARKER = "[群聊内容]"

_INJECTION_PATTERNS = [
    # 中文：忽略/无视 + 以上/之前/前面 + 的所有指令
    r"(?:忽略|无视|跳过)(?:以上|之前|前面|上面)?(?:的)?(?:所有|全部)?(?:指令|提示词|提示|规则|要求)",
    # 英文：ignore/disregard/forget ... previous instructions
    r"(?:ignore|disregard|forget)\s+(?:all\s+)?(?:the\s+)?(?:previous|above|prior|earlier)\s+(?:instructions?|prompts?|rules?)",
    # 伪系统角色标记
    r"system\s*[:：]",
    r"你现在是",
    r"开发者模式",
    r"developer\s+mode",
    r"you\s+are\s+now",
]

_INJECTION_RE = re.compile("|".join(_INJECTION_PATTERNS), re.IGNORECASE)


def _to_fullwidth(text: str) -> str:
    """把命中片段中的 ASCII 字符转成全角，破坏模型对指令词的识别。"""
    chars = []
    for ch in text:
        code = ord(ch)
        if 0x21 <= code <= 0x7E:
            chars.append(chr(code + 0xFEE0))
        elif ch == " ":
            chars.append("\u3000")
        else:
            chars.append(ch)
    return "".join(chars)


def sanitize(text) -> str:
    """清洗单条消息正文，返回可安全送入大模型的文本。"""
    if not isinstance(text, str):
        return ""

    truncated = text[:MAX_TEXT_LEN]
    hit = False

    def _replace(match: re.Match) -> str:
        nonlocal hit
        hit = True
        return _to_fullwidth(match.group(0))

    cleaned = _INJECTION_RE.sub(_replace, truncated)
    if hit:
        keep = MAX_TEXT_LEN - len(INJECTION_MARKER)
        cleaned = INJECTION_MARKER + cleaned[:keep]
    return cleaned
