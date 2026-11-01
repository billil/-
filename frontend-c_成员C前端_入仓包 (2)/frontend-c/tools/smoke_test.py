# -*- coding: utf-8 -*-
"""群忆 · 前端联调自检（成员 C 交付件，不需要浏览器）

它做的事情和浏览器完全一致：取静态资源、经 `/api` 同源代理访问后端、校验返回值形状。

用法：
    # A. 服务已经开着（最常见：后端 8000 + 前端 serve.py 5173）
    python tools/smoke_test.py --backend-dir ../member-d/backend

    # B. 换端口 / 换地址
    python tools/smoke_test.py --ui http://127.0.0.1:5173 --token <令牌>

    # C. 不清理测试数据（默认会删掉自己导入的"自检"消息）
    python tools/smoke_test.py --backend-dir ../member-d/backend --keep

退出码：0 全部通过；2 有失败项。
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

STATIC = [
    "/", "/index.html", "/assets/styles.css", "/assets/app.js", "/assets/api.js",
    "/assets/util.js", "/assets/message-actions.js", "/assets/views/overview.js",
    "/assets/views/library.js", "/assets/views/search.js", "/assets/views/detail.js",
    "/assets/views/groups.js", "/assets/views/notifications.js",
    "/assets/views/system.js",
]

# 前端"探测式接入"的可选接口：后端有就自动用真实数据，没有就 404 降级。
# 这里只做**可用性报告**，不计入失败——它们不属于当前后端契约。
OPTIONAL_ROUTES = [
    ("GET", "/v1/me"),
    ("GET", "/v1/groups"),
    ("GET", "/v1/subscriptions"),
    ("GET", "/v1/notifications"),
    ("GET", "/v1/sync/status"),
]

PASS, FAIL, SKIP = "OK  ", "FAIL", "SKIP"
results = []


def record(tag, name, detail=""):
    results.append((tag, name, detail))
    print("%s  %-46s %s" % (tag, name, detail))


def fetch(ui, path, method="GET", body=None, token=None, timeout=40):
    """返回 (status, payload_or_bytes)。"""
    headers = {}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(ui + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except OSError as exc:
        return 0, str(exc).encode()


def jget(ui, path, token, method="GET", body=None, timeout=40):
    code, blob = fetch(ui, path, method=method, body=body, token=token, timeout=timeout)
    try:
        return code, json.loads(blob.decode("utf-8"))
    except Exception:
        return code, {"_raw": blob[:300].decode("utf-8", "replace")}


def read_token(backend_dir):
    cred = os.path.join(backend_dir, "state", "credentials.json")
    if not os.path.exists(cred):
        return ""
    try:
        data = json.load(open(cred, encoding="utf-8"))
    except Exception:
        return ""
    if isinstance(data, str):
        return data
    # 后端真实结构：{"<空间令牌>": "<空间标识>"}，令牌是 KEY，空间名是 VALUE
    for key in ("token", "key", "secret"):
        if isinstance(data.get(key), str):
            return data[key]
    for k, v in data.items():
        if isinstance(k, str) and len(k) >= 8:
            return k
    return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ui", default="http://127.0.0.1:5173", help="前端地址（serve.py 起的那个）")
    ap.add_argument("--backend-dir", default=os.path.join("..", "member-d", "backend"),
                    help="后端目录，用于读取 state/credentials.json 里的令牌")
    ap.add_argument("--token", default="", help="直接指定令牌，优先级高于 --backend-dir")
    ap.add_argument("--keep", action="store_true", help="保留本次导入的测试消息")
    args = ap.parse_args()

    ui = args.ui.rstrip("/")
    token = args.token or read_token(args.backend_dir)
    if not token:
        print("[!] 没有令牌。用 --token 指定，或让后端先启动一次生成 state/credentials.json。")
        return 2

    print("前端地址 : %s" % ui)
    print("=" * 72)

    # ---------- 1. 静态资源 ----------
    print("\n-- 静态资源 --")
    for path in STATIC:
        code, blob = fetch(ui, path)
        record(PASS if (code == 200 and len(blob) > 0) else FAIL, path, "%s  %d bytes" % (code, len(blob)))

    # ---------- 2. 代理与鉴权 ----------
    print("\n-- 同源代理与鉴权 --")
    code, body = jget(ui, "/api/v1/coverage", None)
    record(PASS if code == 401 else FAIL, "无令牌 -> 401（而不是跨域失败）",
           "status=%s code=%s" % (code, (body.get("error") or {}).get("code")))

    code, body = jget(ui, "/api/v1/coverage", "invalid-token-1234567890")
    record(PASS if code == 401 else FAIL, "错误令牌 -> 401", "status=%s" % code)

    code, body = jget(ui, "/api/v1/does-not-exist", token)
    record(PASS if code == 404 else FAIL, "未知接口 -> 404", "status=%s" % code)

    code, caps = jget(ui, "/api/v1/capabilities", token)
    record(PASS if (code == 200 and "simulation" in caps) else FAIL, "GET /v1/capabilities",
           "mode=%s simulation=%s" % (caps.get("mode"), caps.get("simulation")))

    code, cov0 = jget(ui, "/api/v1/coverage", token)
    record(PASS if (code == 200 and "scope" in cov0) else FAIL, "GET /v1/coverage（含 scope）",
           "imported=%s indexed=%s" % (cov0.get("imported_count"), cov0.get("indexed_count")))

    # ---------- 3. 导入（含字段逐字校验） ----------
    print("\n-- 导入与校验 --")
    stamp = str(int(__import__("time").time()))
    event_key = "smoke_event_" + stamp
    good = {
        "group_label": "自检-计算机学院通知群",
        "content": "【自检】程序设计竞赛报名，时间：2026年11月8日 19:00，地点：三江楼 305。",
        "sent_at": "2026-10-27T10:00:00+08:00",
        "metadata": {
            "event_key": event_key,
            "title": "自检-程序设计竞赛",
            "revision_kind": "initial",
            "source_confirmed": True,
            "fields": {"时间": "2026年11月8日 19:00", "地点": "三江楼 305"},
        },
    }
    bad = json.loads(json.dumps(good, ensure_ascii=False))
    bad["metadata"]["fields"] = {"时间": "2026年11月9日 19:00"}  # 原文里没有 → 必须被拒

    code, imp = jget(ui, "/api/v1/imports",
                     token, "POST",
                     {"messages": [good, bad], "idempotency_key": "smoke-" + stamp})
    ok = code == 200 and imp.get("success_count") == 1 and imp.get("failure_count") == 1
    record(PASS if ok else FAIL, "导入：1 条合法 + 1 条字段不在原文",
           "success=%s failure=%s" % (imp.get("success_count"), imp.get("failure_count")))
    errs = imp.get("errors") or []
    if errs:
        record(PASS if errs[0].get("index") == 1 else FAIL, "字段逐字校验被后端拒绝",
               "%s %s" % (errs[0].get("code"), (errs[0].get("message") or "")[:28]))

    msg_id = ((imp.get("items") or [{}])[0]).get("message_id")
    record(PASS if msg_id else FAIL, "导入返回 message_id", str(msg_id))

    replay_key = "smoke-replay-" + stamp
    jget(ui, "/api/v1/imports", token, "POST", {"messages": [good], "idempotency_key": replay_key})
    code, rep = jget(ui, "/api/v1/imports", token, "POST", {"messages": [good], "idempotency_key": replay_key})
    record(PASS if (code == 200 and rep.get("replayed") is True) else FAIL,
           "同幂等键重放 -> replayed=true", "replayed=%s" % rep.get("replayed"))

    code, conf = jget(ui, "/api/v1/imports", token, "POST",
                      {"messages": [dict(good, content=good["content"] + "补一句")],
                       "idempotency_key": replay_key})
    record(PASS if code == 409 else FAIL, "同幂等键不同内容 -> 409",
           "status=%s code=%s" % (code, (conf.get("error") or {}).get("code")))

    # ---------- 4. 分页 / 详情 ----------
    print("\n-- 消息与详情 --")
    code, page = jget(ui, "/api/v1/messages?limit=5&offset=0", token)
    record(PASS if (code == 200 and "total" in page) else FAIL, "GET /v1/messages 分页",
           "items=%s total=%s" % (len(page.get("items") or []), page.get("total")))

    code, _ = jget(ui, "/api/v1/messages?limit=0", token)
    record(PASS if code == 400 else FAIL, "limit 越界 -> 400", "status=%s" % code)

    code, one = jget(ui, "/api/v1/messages/%s" % msg_id, token)
    record(PASS if (code == 200 and one.get("id") == msg_id) else FAIL, "GET /v1/messages/:id",
           "revision=%s" % one.get("revision"))

    code, _ = jget(ui, "/api/v1/messages/not-exist-id", token)
    record(PASS if code == 404 else FAIL, "不存在的消息 -> 404", "status=%s" % code)

    # ---------- 5. 事件版本 ----------
    print("\n-- 事件与版本 --")
    code, events = jget(ui, "/api/v1/events", token)
    items = events.get("items") or []
    record(PASS if code == 200 else FAIL, "GET /v1/events", "events=%s" % len(items))
    mine = [e for e in items if e.get("event_key") == event_key]
    if mine:
        record(PASS if "source_note" in mine[0] else FAIL, "事件含 source_note 免责原文",
               str(mine[0].get("source_note"))[:30])
        record(PASS if len(mine[0].get("messages") or []) >= 1 else FAIL, "事件含版本列表",
               "versions=%s" % len(mine[0].get("messages") or []))
    else:
        record(SKIP, "事件详情", "本轮导入未被 /v1/events 聚合")

    # ---------- 6. query 五种状态 ----------
    print("\n-- 智能查找 --")
    code, q1 = jget(ui, "/api/v1/query", token, "POST", {"question": "程序设计竞赛 报名"})
    sid = q1.get("session_id")
    record(PASS if (code == 200 and sid and "ai" in q1 and "coverage" in q1) else FAIL,
           "POST /v1/query 返回信封", "status=%s" % q1.get("status"))
    record(PASS if q1.get("status") in ("answer", "clarify", "not_found", "uncertain", "error") else FAIL,
           "status 属于五值之一", str(q1.get("status")))
    record(PASS if (q1.get("ai") or {}).get("simulation") in (True, False) else FAIL,
           "ai.simulation 可读（前端据此标注模拟/真实）",
           "simulation=%s" % (q1.get("ai") or {}).get("simulation"))

    if q1.get("status") in ("answer", "uncertain"):
        fields = q1.get("fields") or []
        if fields:
            f0 = fields[0]
            content = one.get("content", "")
            record(PASS if f0.get("quote", "") in content else FAIL,
                   "字段 quote 逐字出现在原文（高亮可真实定位）", "field=%s" % f0.get("name"))
        cites = q1.get("citations") or []
        record(PASS if cites else SKIP, "answer/uncertain 带 citations", "citations=%s" % len(cites))

    code, q2 = jget(ui, "/api/v1/query", token, "POST", {"question": "报名"})
    if q2.get("status") == "clarify":
        opts = q2.get("options") or []
        record(PASS if opts else FAIL, "clarify 给出候选", "options=%s" % len(opts))
        if opts:
            code, q3 = jget(ui, "/api/v1/query", token, "POST",
                            {"question": "报名", "session_id": q2.get("session_id"),
                             "condition_updates": {"event_key": opts[0]["event_key"]}})
            record(PASS if q3.get("status") in ("answer", "uncertain", "not_found") else FAIL,
                   "带 event_key 追问后收敛", str(q3.get("status")))
            record(PASS if (q3.get("condition_version") or 0) > (q2.get("condition_version") or 0) else FAIL,
                   "condition_version 递增",
                   "%s -> %s" % (q2.get("condition_version"), q3.get("condition_version")))
    else:
        record(SKIP, "clarify 候选选择", "当前库未命中多候选（status=%s）" % q2.get("status"))

    code, q4 = jget(ui, "/api/v1/query", token, "POST", {"question": "完全不存在的关键词xyzzy987"})
    record(PASS if (code == 200 and q4.get("status") == "not_found") else FAIL,
           "无结果 -> not_found，且 HTTP 仍为 200", "status=%s http=%s" % (q4.get("status"), code))

    # ---------- 7. 会话 ----------
    print("\n-- 会话 --")
    code, sess = jget(ui, "/api/v1/sessions/%s" % sid, token)
    record(PASS if (code == 200 and len(sess.get("turns") or []) >= 1) else FAIL,
           "GET /v1/sessions/:id 恢复会话", "turns=%s" % len(sess.get("turns") or []))

    code, _ = jget(ui, "/api/v1/sessions/nope-session-id", token)
    record(PASS if code == 404 else FAIL, "无效会话 -> 404", "status=%s" % code)

    # ---------- 8. 修订 -> 会话失效 ----------
    print("\n-- 修订与失效 --")
    code, patched = jget(ui, "/api/v1/messages/%s" % msg_id, token, "PATCH",
                         {"content": good["content"] + "（地点改为三江楼 306）"})
    record(PASS if (code == 200 and patched.get("revision") == 2) else FAIL,
           "PATCH 修订 -> revision=2", "revision=%s" % patched.get("revision"))

    code, _ = jget(ui, "/api/v1/sessions/%s" % sid, token)
    record(PASS if code == 404 else FAIL, "修订后旧会话失效 -> 404（前端应提示新建）", "status=%s" % code)

    code, _ = jget(ui, "/api/v1/messages/%s" % msg_id, token, "PATCH", {"unknown": 1})
    record(PASS if code in (400, 422) else FAIL, "PATCH 未知字段被拒", "status=%s" % code)

    # ---------- 9. 索引重建 ----------
    print("\n-- 索引 --")
    code, rb = jget(ui, "/api/v1/index/rebuild", token, "POST", {})
    record(PASS if (code == 200 and "indexed" in rb) else FAIL, "POST /v1/index/rebuild",
           "indexed=%s" % rb.get("indexed"))

    # ---------- 10. 可选接口可用性（不计失败） ----------
    print("\n-- 前端「探测式接入」接口可用性（不计入失败项） --")
    ready, missing = [], []
    for method, path in OPTIONAL_ROUTES:
        code, _ = fetch(ui, "/api" + path, method=method, token=token, timeout=15)
        if code == 200:
            ready.append(path)
            print("  已就绪  %-24s 200 -> 前端自动切换为后端真实数据" % path)
        elif code == 404:
            missing.append(path)
            print("  未实现  %-24s 404 -> 前端显示「待后端实现」并降级" % path)
        else:
            missing.append(path)
            print("  异常    %-24s %s -> 前端显示错误并提供重试" % (path, code))
    print("  小结：就绪 %d / 共 %d。" % (len(ready), len(OPTIONAL_ROUTES)))
    if missing:
        print("  未就绪：%s" % "、".join(missing))
        print("  说明：这些是 A 成员方案里新增的接口，当前后端尚未提供；前端已按“探测 + 降级”处理，"
              "不会因此报错或白屏。后端上线后无需改前端代码。")

    # ---------- 11. 清理 ----------
    print("\n-- 清理 --")
    if args.keep:
        record(SKIP, "清理测试数据", "--keep")
    else:
        cleaned = 0
        for _ in range(10):
            code, page = jget(ui, "/api/v1/messages?limit=100&offset=0", token)
            if code != 200:
                break
            ids = [m["id"] for m in (page.get("items") or [])
                   if "【自检】" in (m.get("content") or "")]
            if not ids:
                break
            for mid in ids:
                jget(ui, "/api/v1/messages/%s" % mid, token, "DELETE")
                cleaned += 1
        record(PASS, "清理本次导入的【自检】消息", "删除 %d 条" % cleaned)

    # ---------- 汇总 ----------
    print("\n" + "=" * 72)
    n_fail = sum(1 for r in results if r[0] == FAIL)
    n_skip = sum(1 for r in results if r[0] == SKIP)
    print("共 %d 项：通过 %d，失败 %d，跳过 %d" %
          (len(results), len(results) - n_fail - n_skip, n_fail, n_skip))
    if n_fail:
        print("\n失败明细：")
        for tag, name, detail in results:
            if tag == FAIL:
                print("  - %s  (%s)" % (name, detail))
        return 2
    print("全部通过：前端与仓库内后端契约一致。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
