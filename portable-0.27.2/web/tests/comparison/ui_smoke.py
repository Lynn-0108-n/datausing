# -*- coding: utf-8 -*-
"""FlowMask 对比工作台 UI 冒烟测试（Playwright + 本机 Chrome）。"""
import json
import os
import re
import sys

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLES = os.path.join(HERE, "samples")
SHOTS = os.path.join(HERE, "shots")
TMP_WORK = os.path.join(HERE, "_run")
os.makedirs(SHOTS, exist_ok=True)
os.makedirs(TMP_WORK, exist_ok=True)
os.environ["TMP"] = TMP_WORK
os.environ["TEMP"] = TMP_WORK
os.environ["TMPDIR"] = TMP_WORK

BASE = os.environ.get("FLOWMASK_BASE", "http://127.0.0.1:8395").rstrip("/")

results = []
def check(ok, name, detail=""):
    results.append((ok, name, detail))
    print(("PASS  " if ok else "FAIL  ") + name + ("  | " + str(detail) if detail else ""))

def shot(page, name):
    path = os.path.join(SHOTS, name)
    page.screenshot(path=path)
    return path

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, channel="chrome")
    context = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
    page = context.new_page()
    console_errors = []
    def _on_console(msg):
        if msg.type != "error":
            return
        text = msg.text
        # 404 favicon / 故意拦截的 410 不属于真实控制台错误
        if "404 (Not Found)" in text or "410 (Gone)" in text:
            return
        console_errors.append(text)

    page.on("console", _on_console)
    page.on("pageerror", lambda exc: console_errors.append("pageerror: " + str(exc)))

    page.goto(BASE + "/app/", wait_until="networkidle")
    check(page.title().startswith("FlowMask"), "app title", page.title())
    shot(page, "01-home.png")

    page.set_input_files("input.file-input", [os.path.join(SAMPLES, "evidence-a.txt"), os.path.join(SAMPLES, "evidence-b.txt")])
    page.get_by_role("button", name="检测").click()
    page.wait_for_selector('button:has-text("对比"):not([disabled])', timeout=90000)
    check(True, "batch finished, 对比 button enabled")
    opened_task = {}
    def _on_compare_response(resp):
        m = re.search(r"/tasks/([0-9a-f]+)/comparison$", resp.url)
        if m and resp.status == 200:
            opened_task["id"] = m.group(1)
    page.on("response", _on_compare_response)
    shot(page, "02-batch-results.png")

    page.get_by_role("button", name="对比").first.click()
    page.wait_for_selector(".dc-dialog", state="visible", timeout=30000)
    page.wait_for_timeout(800)
    task_id = opened_task.get("id")
    shot(page, "03-compare-dialog.png")

    dlg = page.locator(".dc-dialog")
    box = dlg.bounding_box()
    ratio = box["width"] / page.viewport_size["width"]
    check(0.85 <= ratio <= 0.93, "dialog width ~88% of viewport", "%.3f" % ratio)
    height_ratio = box["height"] / page.viewport_size["height"]
    check(0.80 <= height_ratio <= 0.90, "dialog height 80-90% viewport", "%.3f" % height_ratio)

    with page.expect_download(timeout=15000) as dl_info:
        page.get_by_role("button", name="下载脱敏文档").click()
    dl = dl_info.value
    with open(dl.path(), encoding="utf-8") as fh:
        dl_text = fh.read()
    resp = page.request.get(BASE + "/api/v1/tasks/%s/comparison/download" % task_id)
    api_text = resp.body().decode("utf-8")
    check(dl.suggested_filename == "flowmask-masked.txt", "download filename", dl.suggested_filename)
    check(dl_text == api_text, "dialog download equals server download")

    header = dlg.inner_text()
    check("已脱敏" in header, "header shows masked status")
    check("规则命中" in header, "header labels rule hits explicitly")
    check("差异" in header or "命中" in header, "header shows diff/hit counter")

    orig_marks = page.locator(".dc-mark-orig").count()
    masked_marks = page.locator(".dc-mark-masked").count()
    check(orig_marks > 0 and masked_marks > 0, "both sides have character-level highlights", "orig=%s masked=%s" % (orig_marks, masked_marks))
    check(orig_marks == masked_marks, "highlight counts match across sides", "%s/%s" % (orig_marks, masked_marks))

    sub_line = page.evaluate("""() => {
      const pane = document.querySelector('.dc-pane');
      const content = document.querySelector('.dc-content');
      if (!pane || !content) return null;
      const w = content.clientWidth || content.scrollWidth;
      const marks = [...document.querySelectorAll('.dc-mark')];
      return {contentWidth: w, markCount: marks.length,
              maxMarkWidth: marks.reduce((m, el) => Math.max(m, el.getBoundingClientRect().width), 0)};
    }""")
    check(sub_line and sub_line["maxMarkWidth"] < sub_line["contentWidth"] * 0.9,
          "highlights are sub-line width", sub_line)

    hscroll = page.evaluate("""() => {
      const panes = [...document.querySelectorAll('.dc-scroll')];
      if (!panes.length) return null;
      const p = panes[0];
      const before = p.scrollLeft;
      p.scrollLeft = 120;
      p.dispatchEvent(new Event('scroll'));
      const after = p.scrollLeft;
      p.scrollLeft = 0;
      return {canScroll: p.scrollWidth > p.clientWidth, before, after, sw: p.scrollWidth, cw: p.clientWidth};
    }""")
    check(hscroll and hscroll["canScroll"] and hscroll["after"] > 0, "long line horizontal scroll works", hscroll)

    ln_sticky = page.evaluate("""() => {
      const ln = document.querySelector('.dc-ln');
      return ln ? getComputedStyle(ln).position : null;
    }""")
    check(ln_sticky == "sticky", "line numbers are sticky on horizontal scroll", ln_sticky)

    page.locator(".dc-mark-orig").first.click()
    page.wait_for_selector(".dc-field-card", state="visible", timeout=10000)
    card_text = page.locator(".dc-field-card").inner_text()
    check(any(k in card_text for k in ("类别", "级别", "策略", "状态")), "field info card content", card_text.replace("\n", " / ")[:160])
    shot(page, "04-field-card.png")
    page.locator(".dc-dialog").click(position={"x": 5, "y": 5})
    page.wait_for_timeout(300)

    page.get_by_role("button", name="下一处").click()
    page.wait_for_timeout(600)
    active = page.locator(".dc-mark.is-active").count()
    check(active >= 1, "next-diff highlights an active change", active)
    shot(page, "05-next-diff.png")
    page.get_by_role("button", name="上一处").click()
    page.wait_for_timeout(400)

    page.evaluate("""() => {
      const panes = [...document.querySelectorAll('.dc-scroll')];
      if (panes.length >= 2) { panes[0].scrollTop += 600; panes[0].dispatchEvent(new Event('scroll')); }
    }""")
    page.wait_for_timeout(600)
    after = page.evaluate("() => { const panes=[...document.querySelectorAll('.dc-scroll')]; return panes.length>=2 ? panes[1].scrollTop : -1; }")
    check(after > 0, "sync scrolling moves second pane", "secondPaneScrollTop=%s" % after)

    page.get_by_text("仅原文", exact=True).click()
    page.wait_for_timeout(400)
    check(page.locator(".dc-pane:visible").count() >= 1, "single view: original rendered")
    shot(page, "06-original-only.png")
    page.get_by_text("仅脱敏文档", exact=True).click()
    page.wait_for_timeout(400)
    check(page.locator(".dc-pane:visible").count() >= 1, "single view: masked rendered")
    page.get_by_text("左右对比", exact=True).click()
    page.wait_for_timeout(400)
    check(page.locator(".dc-pane:visible").count() == 2, "split view restored")

    page.locator(".dc-toolbar .el-switch").nth(1).click()
    page.wait_for_timeout(600)
    check(page.locator(".dc-line-wrap").count() > 0, "wrap mode renders wrap lines")
    shot(page, "07-wrap-mode.png")
    page.locator(".dc-toolbar .el-switch").nth(1).click()
    page.wait_for_timeout(400)

    page.keyboard.press("Escape")
    page.wait_for_selector(".dc-dialog", state="detached", timeout=10000)
    check(page.locator(".dc-dialog").count() == 0, "Escape closes dialog")

    storage = page.evaluate("""() => ({local: JSON.stringify(localStorage), session: JSON.stringify(sessionStorage)})""")
    sensitive_hits = [s for s in ("张三", "13800138000", "11010519491231002X", "zhangsan@example.com") if s in storage["local"] or s in storage["session"]]
    check(not sensitive_hits, "no sensitive original text in web storage", sensitive_hits)

    page.route("**/tasks/*/comparison", lambda route: route.fulfill(
        status=410,
        content_type="application/json",
        body=json.dumps({"detail": "检测快照已过期或重启失效，请重新检测", "code": "SNAPSHOT_EXPIRED", "error_id": "ui-test-410"}),
    ))
    page.get_by_role("button", name="对比").first.click()
    page.wait_for_selector(".dc-state", state="visible", timeout=15000)
    err_text = page.locator(".dc-dialog").inner_text()
    check("快照已过期" in err_text, "410 friendly error state", err_text.replace("\n", " / ")[:180])
    check("ui-test-410" in err_text, "error shows error_id")
    page.wait_for_timeout(600)
    shot(page, "08-error-410.png")
    page.unroute("**/tasks/*/comparison")
    page.keyboard.press("Escape")
    page.wait_for_selector(".dc-dialog", state="detached", timeout=10000)

    narrow = browser.new_context(viewport={"width": 900, "height": 800})
    npage = narrow.new_page()
    npage.goto(BASE + "/app/", wait_until="networkidle")
    npage.set_input_files("input.file-input", [os.path.join(SAMPLES, "evidence-a.txt")])
    npage.get_by_role("button", name="检测").click()
    npage.wait_for_selector('button:has-text("对比"):not([disabled])', timeout=90000)
    npage.get_by_role("button", name="对比").first.click()
    npage.wait_for_selector(".dc-dialog", state="visible", timeout=30000)
    npage.wait_for_timeout(800)
    nbox = npage.locator(".dc-dialog").bounding_box()
    check(nbox["width"] / 900 >= 0.9, "narrow dialog ~96% width", "%.3f" % (nbox["width"] / 900))
    check(0.80 <= nbox["height"] / 800 <= 0.90, "narrow dialog height 80-90% viewport", "%.3f" % (nbox["height"] / 800))
    check(npage.locator(".dc-pane:visible").count() == 1, "narrow mode defaults to single pane")
    labels = npage.locator(".dc-view-switch").inner_text() if npage.locator(".dc-view-switch").count() else npage.locator(".el-radio-group").first.inner_text()
    check("左右对比" not in labels, "narrow mode hides split option", labels.replace("\n", " / "))
    shot(npage, "09-narrow-dialog.png")
    narrow.close()

    check(not console_errors, "no console/page errors during UI flow", console_errors[:3])
    context.close()
    browser.close()

print("\nSUMMARY: %s passed, %s failed, %s total" % (
    sum(1 for ok, _, _ in results if ok),
    sum(1 for ok, _, _ in results if not ok),
    len(results),
))
if any(not ok for ok, _, _ in results):
    sys.exit(1)
