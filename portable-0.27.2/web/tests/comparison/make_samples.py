# -*- coding: utf-8 -*-
"""Generate comparison smoke-test documents (UTF-8, no BOM)."""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "samples")
os.makedirs(ROOT, exist_ok=True)

names = ["张三", "李四", "王五", "赵六", "钱七"]
phones = ["13800138000", "13912345678", "13700001111", "15011112222", "18633334444"]
emails = ["zhangsan@example.com", "lisi@example.org", "wangwu@test.cn", "zhaoliu@corp.com", "qianqi@demo.net"]
ids = [
    "11010519491231002X",
    "110105198802020021",
    "310101199003070013",
    "440106198808080017",
    "510107199511110025",
]
banks = ["6222020200112233445", "6217000010001234567", "6228480402564890018"]
addrs = [
    "福建省福州市鼓楼区五四路 100 号",
    "北京市朝阳区建国路 88 号",
    "上海市浦东新区世纪大道 1 号",
]

lines = [
    "FlowMask 脱敏前后对比联调样本 A",
    "说明：本文件仅用于本地功能验证，内容为虚构数据。",
    "",
    "患者姓名：张三",
    "手机：13800138000",
    "身份证：11010519491231002X",
    "备注：😀 emoji 在前，邮箱 zhangsan@example.com，结束。",
    "",
]
for i in range(120):
    lines.append(
        "记录 %03d ｜ 姓名：%s ｜ 手机：%s ｜ 邮箱：%s ｜ 身份证：%s ｜ 银行卡：%s"
        % (i + 1, names[i % 5], phones[i % 5], emails[i % 5], ids[i % 5], banks[i % 3])
    )
    if i % 7 == 0:
        lines.append("        地址：%s（缩进行，用于验证缩进保留）" % addrs[i % 3])
    if i % 11 == 0:
        lines.append("")
    if i == 60:
        lines.append("段落中段：这里只有普通文字，没有任何敏感信息。")

text_a = "\n".join(lines) + "\n"

clean_lines = ["FlowMask 对比工作台联调样本 B（无敏感信息）"]
for i in range(30):
    clean_lines.append("第 %02d 行：普通业务文字，不含任何敏感字段。" % (i + 1))
text_b = "\n".join(clean_lines) + "\n"

large_line = "普通文本行，不含敏感信息。" * 4
text_large = large_line + "\n" + ("长文档内容 " * 40 + "\n") * 4800


def write(name, text):
    path = os.path.join(ROOT, name)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    print(name, len(text), "chars", os.path.getsize(path), "bytes")


write("evidence-a.txt", text_a)
write("evidence-b.txt", text_b)
write("evidence-large.txt", text_large)
