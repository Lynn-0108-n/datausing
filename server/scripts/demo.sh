#!/usr/bin/env bash
# FlowMask 一键演示（Linux/macOS）：假设服务已在运行，只调用 HTTP 接口。
set -euo pipefail
BASE="${1:-http://127.0.0.1:8090}"
API="$BASE/api/v1"
say() { printf "\n=== %s\n" "$1"; }
ok() { printf "  [OK]   %s\n" "$1"; }

say "0/7 健康检查"
curl -sS "$API/health" | python3 -c 'import json,sys; h=json.load(sys.stdin); print(f"  [OK]   服务在，运行 {h[\"uptime_s\"]}s")'

TEXT='客户张三，手机 13800138000，身份证 11010519491231002X，收货地址：福建省福州市鼓楼区五四路 100 号；接口凭据 api_key=sk-demo-1234567890abcdef。'

say "1/7 即时检测"
curl -sS -X POST "$API/detect" -H "Content-Type: application/json" \
  --data-binary "$(python3 -c 'import json,sys; print(json.dumps({"text": sys.argv[1]}))' "$TEXT")" \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); fs=d.get("findings") or []; lv=fs[0]["level"] if fs else "SAFE"; tp=",".join(fs[0].get("types") or []) if fs else "-"; print(f"  [OK]   分级 {lv}，类别 {tp}，命中 {len(fs)} 条")'

say "2/7 建任务 + 审计链"
TASK=$(curl -sS -X POST "$API/tasks/text" -H "Content-Type: application/json" \
  --data-binary "$(python3 -c 'import json,sys; print(json.dumps({"text": sys.argv[1]}))' "$TEXT")" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["task_id"])')
sleep 1
curl -sS "$API/tasks/$TASK" | python3 -c 'import json,sys; t=json.load(sys.stdin); print(f"  [OK]   任务 {t[\"id\"][:10]}… 状态 {t[\"status\"]}，命中 {t[\"total_findings\"]} 条")'

say "3/7 Excel 文件检测 + 脱敏导出"
SHEET="$(cd "$(dirname "$0")/.." && pwd)/samples/demo-sheet.xlsx"
if [ -f "$SHEET" ]; then
  curl -sS -F "file=@$SHEET" "$API/detect/file" \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); seg=d.get("segments"); lv=sorted({f["level"] for f in d.get("findings") or []}); print("  [OK]   xlsx 解析 %s 个片段，分级 %s" % (seg, lv))'
  MASKED="$(mktemp /tmp/flowmask-demo-XXXXXX.xlsx)"
  CODE=$(curl -sS -o "$MASKED" -w '%{http_code}' -F "file=@$SHEET" -F "strategy=partial" -F "fmt=auto" "$API/detect/file/export")
  python3 - "$CODE" "$MASKED" <<'PY'
import sys
import zipfile

code, path = sys.argv[1], sys.argv[2]
assert code == "200", f"导出失败：HTTP {code}"
with zipfile.ZipFile(path) as archive:
    sheet = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
assert "13812345678" not in sheet, "导出 xlsx 里仍出现原文手机号"
assert "姓名" in sheet, "导出 xlsx 丢了表头"
print("  [OK]   导出 xlsx 可被 zip 解析，表头保留、原文手机号已替换为掩码")
PY
  rm -f "$MASKED"
else
  printf "  找不到 %s，跳过（可跑 server/samples/make_demo_xlsx.py 生成）\n" "$SHEET"
fi

say "4/7 隐私增强导出"
curl -sS "$API/tasks/$TASK/export/privacy?format=json&epsilon=1.0&l=3" \
  | python3 -c 'import json,sys; p=json.load(sys.stdin)["meta"]["privacy"]; print(f"  [OK]   total={p[\"total\"]}，dp_by_level={p[\"dp_by_level\"]}，l-多样性 ok={p[\"l_diversity\"][\"ok\"]}")'

say "5/7 审计链校验"
curl -sS "$API/audit/verify" | python3 -c 'import json,sys; v=json.load(sys.stdin); print(f"  [OK]   审计链 {v[\"total\"]} 条，ok={v[\"ok\"]}")'

say "6/7 统计看板"
curl -sS "$API/stats" | python3 -c 'import json,sys; s=json.load(sys.stdin); print(f"  [OK]   任务 {s[\"tasks\"]} 个，命中 {s[\"total_findings\"]} 条")'

say "7/7 打开前端"
ok "浏览器打开 $BASE/app/（或零依赖单页 $BASE/ui/）"
printf "\n演示完成。\n"