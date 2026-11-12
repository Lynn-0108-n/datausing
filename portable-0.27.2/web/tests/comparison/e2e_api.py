# -*- coding: utf-8 -*-
"""FlowMask comparison end-to-end smoke test against isolated server (port 8395)."""
import json
import re
import os
import time
import urllib.error
import urllib.request
import uuid
from http.cookiejar import CookieJar

BASE = os.environ.get("FLOWMASK_BASE", "http://127.0.0.1:8395").rstrip("/")
API = BASE + "/api/v1"
HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLES = os.path.join(HERE, "samples")
RUN = os.path.join(HERE, "_run")
os.makedirs(RUN, exist_ok=True)


class Client:
    def __init__(self, cookies=True):
        self.jar = CookieJar() if cookies else None
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar)
        ) if cookies else urllib.request.build_opener()

    def request(self, method, url, data=None, headers=None):
        req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
        try:
            with self.opener.open(req, timeout=120) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    def json(self, method, url, data=None, headers=None):
        status, hdrs, body = self.request(method, url, data, headers)
        try:
            payload = json.loads(body.decode("utf-8")) if body else None
        except Exception:
            payload = {"raw": body.decode("utf-8", "replace")}
        return status, hdrs, payload


def multipart(files, fields):
    boundary = "----flowmask" + uuid.uuid4().hex
    chunks = []
    for name, value in fields.items():
        chunks.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n" % (boundary, name, value)).encode())
    for name, path in files:
        filename = os.path.basename(path)
        with open(path, "rb") as fh:
            content = fh.read()
        chunks.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"; filename=\"%s\"\r\nContent-Type: text/plain\r\n\r\n" % (boundary, name, filename)).encode())
        chunks.append(content)
        chunks.append(b"\r\n")
    chunks.append(("--%s--\r\n" % boundary).encode())
    return b"".join(chunks), {"Content-Type": "multipart/form-data; boundary=" + boundary}


def poll(client, task_id, timeout=180):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status, _, payload = client.json("GET", API + "/tasks/" + task_id)
        if status != 200:
            raise RuntimeError("task fetch failed: %s %s" % (status, payload))
        if payload.get("status") in ("done", "failed"):
            return payload
        time.sleep(0.4)
    raise RuntimeError("poll timeout for " + task_id)


results = []
def report(ok, name, detail=""):
    results.append((ok, name, detail))
    print(("PASS  " if ok else "FAIL  ") + name + ("  | " + str(detail) if detail else ""))


def hget(headers, name):
    for key, value in headers.items():
        if key.lower() == name.lower():
            return value
    return None


client = Client()

# 0. health
status, _, health = client.json("GET", API + "/health")
report(status == 200 and health.get("status") == "ok", "health", health.get("version"))

# 1. batch upload A + B (same session cookie)
body, headers = multipart(
    [("files", os.path.join(SAMPLES, "evidence-a.txt")), ("files", os.path.join(SAMPLES, "evidence-b.txt"))],
    {},
)
status, _, batch = client.json("POST", API + "/tasks/batch?min_level=SAFE", body, headers)
items = batch.get("items", []) if isinstance(batch, dict) else []
report(status == 201 and batch.get("accepted") == 2, "batch upload accepted", "%s %s" % (status, batch.get("accepted")))
task_a = next((i["task_id"] for i in items if i.get("filename") == "evidence-a.txt"), None)
task_b = next((i["task_id"] for i in items if i.get("filename") == "evidence-b.txt"), None)

ta = poll(client, task_a)
tb = poll(client, task_b)
report(ta.get("status") == "done", "task A done", "status=%s segs=%s" % (ta.get("status"), ta.get("segments")))
report(tb.get("status") == "done", "task B done", "findings=%s" % tb.get("findings"))

# 2. comparison A
status, hdrs, payload = client.json("GET", API + "/tasks/%s/comparison" % task_a)
# 安全：不把对比 payload（含原文）写入磁盘
report(status == 200, "comparison A http 200", status)
report(str(hget(hdrs, "Cache-Control") or "").lower() == "no-store", "comparison no-store header", hget(hdrs, "Cache-Control"))
report(str(hget(hdrs, "Pragma") or "").lower() == "no-cache", "comparison no-cache pragma", hget(hdrs, "Pragma"))
report(payload.get("offset_unit") == "unicode-codepoint", "offset_unit", payload.get("offset_unit"))
report(payload.get("representation") == "parsed-segments", "representation", payload.get("representation"))
report(len(payload.get("segments", [])) > 0, "segments present", len(payload.get("segments", [])))

changes = []
for seg in payload.get("segments", []):
    changes.extend(seg.get("changes") or [])
report(len(changes) > 0, "changes present", len(changes))

# 3. structural validation of changes
valid_slices = True
single_line_highlights = 0
ascending = True
overlap = False
for seg in payload.get("segments", []):
    orig, masked, cs = seg.get("original", ""), seg.get("masked", ""), seg.get("changes") or []
    prev_end = -1
    for c in cs:
        s, e, ms, me = c.get("start"), c.get("end"), c.get("masked_start"), c.get("masked_end")
        if not all(isinstance(v, int) for v in (s, e, ms, me)):
            valid_slices = False
            continue
        if not (0 <= s < e <= len(orig) and 0 <= ms < me <= len(masked)):
            valid_slices = False
            continue
        if s < prev_end:
            overlap = True
        prev_end = e
        if not orig[s:e] or not masked[ms:me]:
            valid_slices = False
        if "\n" not in orig[s:e]:
            single_line_highlights += 1
report(valid_slices, "all changes slice inside original/masked", len(changes))
report(single_line_highlights == len(changes), "all highlights are sub-line (not whole line)", "%s/%s" % (single_line_highlights, len(changes)))
report(not overlap, "changes non-overlapping per segment")
report(all(changes[i]["start"] <= changes[i + 1]["start"] for i in range(max(0, len(changes) - 1))), "changes sorted by start (as returned)")

# 4. emoji / codepoint-vs-utf16 proof
joined_original = "\n".join(seg.get("original", "") for seg in payload.get("segments", []))
emoji_index = joined_original.find("😀")
report(emoji_index >= 0, "emoji present in original")
cp_shift = None
if emoji_index >= 0 and changes:
    # find first change after emoji and compare codepoint offset vs utf16 offset
    later = [c for c in changes if c["start"] > emoji_index]
    if later:
        cp = later[0]["start"]
        utf16 = len(joined_original[:cp].encode("utf-16-le")) // 2
        cp_shift = (cp, utf16)
        report(utf16 > cp, "codepoint offset != utf16 offset after emoji", "cp=%s utf16=%s" % (cp, utf16))

# 5. download == '\n'.join(masked)
status, hdrs, body = client.request("GET", API + "/tasks/%s/comparison/download" % task_a)
text = body.decode("utf-8")
expected = "\n".join(seg.get("masked", "") for seg in payload.get("segments", []))
report(status == 200, "download http 200", status)
report(text == expected, "download bytes == joined masked segments")
report("attachment" in str(hget(hdrs, "Content-Disposition") or "").lower(), "download attachment header", hget(hdrs, "Content-Disposition"))
report(str(hget(hdrs, "Cache-Control") or "").lower() == "no-store", "download no-store header", hget(hdrs, "Cache-Control"))
# 安全：不把下载内容写入磁盘

# 6. comparison B: no changes
status, _, payload_b = client.json("GET", API + "/tasks/%s/comparison" % task_b)
changes_b = [c for seg in payload_b.get("segments", []) for c in (seg.get("changes") or [])]
report(status == 200 and len(changes_b) == 0, "comparison B has no changes", "%s %s" % (status, len(changes_b)))

# 7. anonymous -> 403
anon = Client(cookies=False)
status, _, payload_anon = anon.json("GET", API + "/tasks/%s/comparison" % task_a)
report(status == 403 and payload_anon.get("code") == "SNAPSHOT_FORBIDDEN", "anonymous gets 403 SNAPSHOT_FORBIDDEN", "%s %s" % (status, payload_anon.get("code")))

# 8. other session -> 403
other = Client()
status, _, payload_other = other.json("GET", API + "/tasks/%s/comparison" % task_a)
report(status == 403, "other browser session gets 403", "%s %s" % (status, payload_other.get("code")))

# 9. single-file task -> 410
body, headers = multipart([("file", os.path.join(SAMPLES, "evidence-a.txt"))], {})
status, _, single = client.json("POST", API + "/tasks", body, headers)
ts = poll(client, single["task_id"])
status, _, payload_single = client.json("GET", API + "/tasks/%s/comparison" % single["task_id"])
report(ts.get("status") == "done" and status == 410 and payload_single.get("code") == "SNAPSHOT_EXPIRED",
       "single-file task comparison 410 SNAPSHOT_EXPIRED", "%s %s" % (status, payload_single.get("code")))

# 10. large doc -> task done, comparison 413
body, headers = multipart([("files", os.path.join(SAMPLES, "evidence-large.txt"))], {})
status, _, large = client.json("POST", API + "/tasks/batch?min_level=SAFE", body, headers)
large_id = large["items"][0].get("task_id")
if large_id:
    tl = poll(client, large_id, 240)
    status, _, payload_large = client.json("GET", API + "/tasks/%s/comparison" % large_id)
    report(tl.get("status") == "done" and status == 413 and payload_large.get("code") == "COMPARISON_TOO_LARGE",
           "large doc comparison 413 COMPARISON_TOO_LARGE", "%s %s" % (status, payload_large.get("code")))
else:
    report(False, "large doc submitted", large["items"][0].get("error"))

# 11. static frontend uses the locally built bundle
dist_dir = os.path.abspath(os.path.join(HERE, "..", "..", "dist"))
with open(os.path.join(dist_dir, "index.html"), encoding="utf-8") as fh:
    dist_html = fh.read()
match = re.search(r"assets/(index-[A-Za-z0-9_-]+\.js)", dist_html)
served_html = client.request("GET", BASE + "/app/")[2].decode("utf-8", "replace")
report(bool(match) and match.group(1) in served_html,
       "served frontend uses local dist bundle", match.group(1) if match else None)
report("分级脱敏" in served_html, "served frontend title intact")

print("\nSUMMARY: %s passed, %s failed, %s total" % (sum(1 for ok, _, _ in results if ok), sum(1 for ok, _, _ in results if not ok), len(results)))
open(os.path.join(RUN, "task-ids.json"), "w", encoding="utf-8").write(json.dumps({"taskA": task_a, "taskB": task_b}))
if any(not ok for ok, _, _ in results):
    raise SystemExit(1)
