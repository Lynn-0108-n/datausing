# -*- coding: utf-8 -*-
"""长文档虚拟滚动 / 差异导航 / 换行禁用 的浏览器验证（约 40 万字符）。"""
import os
import re
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLES = os.path.join(HERE, "samples")
SHOTS = os.path.join(HERE, "shots")
TMP_WORK = os.path.join(HERE, "_run")
os.makedirs(SAMPLES, exist_ok=True)
os.makedirs(SHOTS, exist_ok=True)
os.makedirs(TMP_WORK, exist_ok=True)
os.environ["TMP"] = TMP_WORK
os.environ["TEMP"] = TMP_WORK
os.environ["TMPDIR"] = TMP_WORK

from playwright.sync_api import sync_playwright

BASE = os.environ.get("FLOWMASK_BASE", "http://127.0.0.1:8395").rstrip("/")
LONG = os.path.join(SAMPLES, "evidence-mid.txt")

# 约 40 万字符、约 8000 行，敏感字段周期性出现
if not os.path.exists(LONG) or os.path.getsize(LONG) < 300_000:
    lines = ["FlowMask 长文档对比样本（约 40 万字符，仅本地验证）"]
    for i in range(8000):
        if i % 3 == 0:
            lines.append("记录 %05d：姓名：张三 ｜ 手机：13800138000 ｜ 邮箱：zhangsan@example.com ｜ 说明：普通业务文字用于拉长行宽与行数。" % i)
        else:
            lines.append("记录 %05d：普通业务文字，不含敏感信息，用于验证虚拟滚动与差异映射。" % i)
    with open(LONG, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
print("sample:", LONG, os.path.getsize(LONG), "bytes")

results = []


def check(ok, name, detail=""):
    results.append((ok, name, detail))
    print(("PASS  " if ok else "FAIL  ") + name + ("  | " + str(detail) if detail else ""))


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, channel="chrome")
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))

    page.goto(BASE + "/app/", wait_until="networkidle")
    page.set_input_files("input.file-input", [LONG])
    page.get_by_role("button", name="检测").click()
    page.wait_for_selector('button:has-text("对比"):not([disabled])', timeout=180000)

    started = time.time()
    page.get_by_role("button", name="对比").first.click()
    page.wait_for_selector(".dc-dialog", state="visible", timeout=30000)
    page.wait_for_timeout(1200)
    open_ms = int((time.time() - started) * 1000)
    check(open_ms < 15000, "dialog opens for ~400k-char document", "%s ms" % open_ms)

    pane = page.locator(".dc-pane").first
    total_lines = page.evaluate("""() => {
      const el = document.querySelector('.dc-content');
      return el ? Math.round(parseFloat(el.style.height) / 22) : -1;
    }""")
    rendered = page.locator(".dc-line").count()
    check(total_lines > 4000, "long document line count > 4000", total_lines)
    check(0 < rendered < 120, "virtual scrolling renders viewport-only lines", "%s / %s" % (rendered, total_lines))

    wrap_switch = page.locator(".dc-toolbar .el-switch").nth(1)
    wrap_disabled = wrap_switch.evaluate("""el => el.classList.contains('is-disabled') || el.querySelector('input')?.disabled === true""")
    check(wrap_disabled, "auto-wrap disabled for very long document")

    before = page.evaluate("() => document.querySelectorAll('.dc-line').length")
    page.locator(".dc-scroll").first.evaluate("el => { el.scrollTop = el.scrollHeight; el.dispatchEvent(new Event('scroll')); }")
    page.wait_for_timeout(600)
    after_top = page.evaluate("() => document.querySelector('.dc-scroll').scrollTop")
    check(after_top > 0, "long document scrolling works", after_top)

    page.locator(".dc-scroll").first.evaluate("el => { el.scrollTop = 0; el.dispatchEvent(new Event('scroll')); }")
    page.wait_for_timeout(300)
    page.get_by_role("button", name="下一处").click()
    page.wait_for_timeout(500)
    active = page.locator(".dc-mark.is-active").count()
    check(active > 0, "diff navigation works in long document", active)

    page.screenshot(path=os.path.join(SHOTS, "11-longdoc.png"))
    check(not errors, "no page errors on long document", errors[:2])
    browser.close()

total = len(results)
passed = sum(1 for ok, _, _ in results if ok)
print("\nSUMMARY: %s passed, %s failed, %s total" % (passed, total - passed, total))
raise SystemExit(0 if passed == total else 1)
