"""WorkBuddy 大模型通路自检：配置 → 模型目录 → 一次真实对话。

用法（在 backend 目录下）：

    # PowerShell
    $env:LLM_PROVIDER="workbuddy"
    $env:WB_ENDPOINT="https://<你的应用域名>"
    $env:WB_PUBLISHABLE_KEY="<publishableKey>"
    $env:WB_MODEL="<模型 id>"
    python -m scripts.check_workbuddy

    # 只看 openai 兼容通路
    python -m scripts.check_workbuddy --provider openai

不打印密钥；任何一步失败都返回非 0 退出码，便于接进 CI 或答辩前的自检。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app.providers import (  # noqa: E402
    ProviderUnavailable,
    WorkBuddyProvider,
    get_provider,
    provider_status,
)


def _mask(value: str) -> str:
    if not value:
        return "(空)"
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}…{value[-4:]}（长度 {len(value)}）"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="检查大模型通路是否可用")
    parser.add_argument(
        "--provider",
        choices=["openai", "workbuddy"],
        help="临时覆盖 LLM_PROVIDER（仅本次运行生效）",
    )
    parser.add_argument("--timeout", type=float, default=30.0, help="单次调用超时（秒）")
    parser.add_argument("--skip-chat", action="store_true", help="只查配置和模型目录，不发对话请求")
    args = parser.parse_args(argv)

    if args.provider:
        os.environ["LLM_PROVIDER"] = args.provider

    status = provider_status()
    print("[1/3] 配置")
    print(f"      provider = {status['provider']}")
    print(f"      model    = {status.get('model') or '(空)'}")
    print(f"      endpoint = {status.get('endpoint') or '(空)'}")
    print(f"      configured = {status['configured']}")
    if status.get("missing"):
        print(f"      缺少环境变量：{'、'.join(status['missing'])}")
    if status.get("reason"):
        print(f"      原因：{status['reason']}")
    if not status["configured"]:
        print("\n结论：配置不完整，先补环境变量再运行。")
        return 2

    try:
        provider = get_provider()
    except ProviderUnavailable as exc:
        print(f"\n结论：provider 装配失败 —— {exc}")
        return 2

    if isinstance(provider, WorkBuddyProvider):
        print("\n[2/3] 模型目录 GET /.cloud/llm/models")
        print(f"      publishableKey = {_mask(provider.publishable_key)}")
        try:
            models = provider.list_models(timeout=args.timeout)
        except ProviderUnavailable as exc:
            print(f"      失败：{exc}（code={exc.code} status={exc.status}）")
            return 3
        ids = [str(m.get("id") or m.get("name") or "") for m in models]
        print(f"      可用模型 {len(models)} 个：{', '.join(ids) or '(空)'}")
        if provider.model not in ids:
            print(
                f"      注意：当前 WB_MODEL={provider.model} 不在目录里，"
                "对话请求可能被网关拒绝（request_model_not_found）"
            )
    else:
        print("\n[2/3] 模型目录：openai 兼容接口无统一目录接口，跳过")

    if args.skip_chat:
        print("\n[3/3] 已跳过对话请求")
        return 0

    print("\n[3/3] 对话请求（要求模型返回 JSON）")
    messages = [
        {"role": "system", "content": "You are a helper. Reply with JSON only."},
        {"role": "user", "content": '返回 {"ok": true} 这一个 JSON 对象，不要额外文字。'},
    ]
    try:
        text = provider.complete(messages, timeout=args.timeout)
    except ProviderUnavailable as exc:
        print(f"      失败：{exc}（code={exc.code} status={exc.status}）")
        return 4
    except (TimeoutError, OSError) as exc:
        print(f"      超时/网络错误：{exc}")
        return 5

    print(f"      原始返回（前 200 字）：{text[:200]!r}")
    try:
        json.loads(text.strip().strip("`").replace("json\n", "", 1))
    except (json.JSONDecodeError, ValueError):
        print("      注意：返回不是合法 JSON，业务里会按 2002 重试一次后失败")
        return 0

    print("\n结论：通路可用，返回可解析 JSON。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
