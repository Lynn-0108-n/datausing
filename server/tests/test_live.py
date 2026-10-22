"""实时旁路测试：增量 pcap 跟随 + 外部推送 + 跟随窗口落库。

用「代码写 pcap 文件、按真实节奏追加」的方式模拟持续抓包，
所以用例不依赖管理员权限，也不依赖 Npcap。
"""

from __future__ import annotations

import struct
import time
from pathlib import Path

import pytest

from app.api.schemas import TrafficIngestOut
from app.core.config import get_settings
from app.traffic import LiveError, PcapFollower, write_pcap
from app.traffic.live import reset_live_manager_for_tests
from tests.test_traffic import demo_pcap, tcp_frame

PHONE = "13800138000"
ID_CARD = "11010519491231002X"


def _record(ts: float, frame: bytes) -> bytes:
    sec = int(ts)
    usec = int(round((ts - sec) * 1_000_000))
    return struct.pack("<IIII", sec, usec, len(frame), len(frame)) + frame


def _pcap_header() -> bytes:
    return struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1)


def _synth_frame(port: int, body: str) -> bytes:
    return tcp_frame(port, body.encode("utf-8"))


def _pcapng_shb(endian: str = "<") -> bytes:
    """Section Header Block：magic + 字节序标记 + 版本 + section 长度(-1)。"""
    body = struct.pack(endian + "IHHq", 0x1A2B3C4D, 1, 0, -1)
    total = 12 + len(body)
    return struct.pack(endian + "II", 0x0A0D0D0A, total) + body + struct.pack(endian + "I", total)


def _pcapng_idb(endian: str = "<", linktype: int = 1, snaplen: int = 65535) -> bytes:
    body = struct.pack(endian + "HHI", linktype, 0, snaplen)
    total = 12 + len(body)
    return struct.pack(endian + "II", 1, total) + body + struct.pack(endian + "I", total)


def _pcapng_epb(ts: float, frame: bytes, endian: str = "<", iface_id: int = 0) -> bytes:
    ts_us = int(round(ts * 1_000_000))
    body = struct.pack(
        endian + "IIIII", iface_id, ts_us >> 32, ts_us & 0xFFFFFFFF, len(frame), len(frame)
    ) + frame
    body += b"\x00" * ((-len(frame)) % 4)  # 块总长度必须 4 字节对齐
    total = 12 + len(body)
    return struct.pack(endian + "II", 6, total) + body + struct.pack(endian + "I", total)


@pytest.fixture()
def live_manager():
    reset_live_manager_for_tests()
    yield
    reset_live_manager_for_tests()


@pytest.fixture()
def data_dir(tmp_env) -> Path:
    return get_settings().data_dir


def _wait(predicate, timeout: float = 8.0, interval: float = 0.1):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return None


# ---------------- 增量解析 ----------------


def test_follower_reads_incrementally(tmp_path: Path) -> None:
    path = tmp_path / "live.pcap"
    frame_a = _synth_frame(50001, '{"phone":"13800138000"}')
    frame_b = _synth_frame(50002, "客户张伟，电话 13700137000")
    path.write_bytes(_pcap_header() + _record(100.0, frame_a))

    follower = PcapFollower(path)
    first = follower.poll()
    assert len(first) == 1
    assert first[0].ts == pytest.approx(100.0)
    assert follower.linktype == 1

    # 半条记录：不能报错、也不产出
    payload = _record(101.0, frame_b)
    path.write_bytes(path.read_bytes() + payload[:20])
    assert follower.poll() == []

    # 补上剩余字节 → 解析出第二条
    path.write_bytes(path.read_bytes() + payload[20:])
    second = follower.poll()
    assert len(second) == 1
    assert second[0].ts == pytest.approx(101.0)


def test_follower_waits_for_file(tmp_path: Path) -> None:
    follower = PcapFollower(tmp_path / "not-yet.pcap")
    assert follower.poll() == []


def test_follower_rejects_pcapng(tmp_path: Path) -> None:
    # 坏块长度：明确报错，而不是把垃圾当报文
    bad = bytearray(_pcapng_shb() + _pcapng_idb())
    bad[4:8] = struct.pack("<I", 0xFFFFFFF)  # 首块长度非法
    path = tmp_path / "bad.pcapng"
    path.write_bytes(bytes(bad))
    with pytest.raises(LiveError):
        PcapFollower(path).poll()


def test_follower_reads_pcapng_incrementally(tmp_path: Path) -> None:
    """pcapng（dumpcap/Wireshark 默认格式）也要能边写边读。"""
    path = tmp_path / "live.pcapng"
    frame_a = _synth_frame(50001, '{"phone":"13800138000"}')
    frame_b = _synth_frame(50002, "客户张伟，电话 13700137000")
    path.write_bytes(_pcapng_shb() + _pcapng_idb())

    follower = PcapFollower(path)
    assert follower.poll() == []
    assert follower.mode == "pcapng"
    assert follower.linktype == 1

    # 完整 EPB → 一帧，时间戳按微秒还原
    path.write_bytes(path.read_bytes() + _pcapng_epb(150.25, frame_a))
    first = follower.poll()
    assert len(first) == 1
    assert first[0].ts == pytest.approx(150.25)
    assert first[0].frame == frame_a

    # 半个块：不报错、不产出
    epb = _pcapng_epb(151.5, frame_b)
    path.write_bytes(path.read_bytes() + epb[: len(epb) // 2])
    assert follower.poll() == []

    # 补齐剩余字节 → 第二帧
    path.write_bytes(path.read_bytes() + epb[len(epb) // 2 :])
    second = follower.poll()
    assert len(second) == 1
    assert second[0].ts == pytest.approx(151.5)


def test_follower_reads_big_endian_pcapng(tmp_path: Path) -> None:
    """SHB 声明大端时后续块按大端解析。"""
    path = tmp_path / "be.pcapng"
    frame = _synth_frame(50003, '{"email":"zhang.san@example.com"}')
    path.write_bytes(
        _pcapng_shb(">") + _pcapng_idb(">") + _pcapng_epb(200.0, frame, endian=">")
    )
    packets = PcapFollower(path).poll()
    assert len(packets) == 1
    assert packets[0].ts == pytest.approx(200.0)
    assert packets[0].frame == frame


def test_follower_skips_non_ethernet_pcapng(tmp_path: Path) -> None:
    """接口链路类型不是以太网时跳过（服务端只解以太网）。"""
    path = tmp_path / "raw.pcapng"
    path.write_bytes(_pcapng_shb() + _pcapng_idb(linktype=101))  # 101 = RAW IP
    path.write_bytes(path.read_bytes() + _pcapng_epb(300.0, _synth_frame(50004, "hello")))
    follower = PcapFollower(path)
    assert follower.poll() == []
    assert follower.linktype == 101


def test_live_follow_pcapng_end_to_end(client, database, data_dir) -> None:
    """端到端：跟随 pcapng 文件 → 追加 EPB → 生成任务并能查到命中。"""
    pcap_path = data_dir / "live-ng.pcapng"
    pcap_path.parent.mkdir(parents=True, exist_ok=True)
    pcap_path.write_bytes(_pcapng_shb() + _pcapng_idb())

    started = client.post(
        "/api/v1/traffic/live/start",
        json={"pcap_path": "live-ng.pcapng", "interval_ms": 120, "window_s": 0.3},
    )
    assert started.status_code == 201, started.text
    live_id = started.json()["live_id"]

    body = f'{{"name":"李四","phone":"{PHONE}","id_card":"{ID_CARD}"}}'
    pcap_path.write_bytes(pcap_path.read_bytes() + _pcapng_epb(400.0, _synth_frame(51010, body)))

    def get_live() -> dict:
        sessions = client.get("/api/v1/traffic/live").json()["sessions"]
        return next((item for item in sessions if item["live_id"] == live_id), {})

    assert _wait(lambda: get_live().get("tasks", 0) >= 1), get_live()
    session = get_live()
    assert session["packets"] >= 1
    assert session["segments"] >= 1
    detail = _wait(
        lambda: client.get(f"/api/v1/tasks/{session['last_task_id']}").json()["status"] == "done"
    )
    assert detail
    task = client.get(f"/api/v1/tasks/{session['last_task_id']}").json()
    assert task["total_findings"] >= 1
    assert task["source"].startswith("live:")

    stopped = client.post(f"/api/v1/traffic/live/{live_id}/stop")
    assert stopped.status_code == 200
    assert stopped.json()["running"] is False


# ---------------- 外部推送 ----------------


def test_ingest_creates_task(client, database) -> None:
    response = client.post(
        "/api/v1/traffic/ingest",
        json={
            "source": "go-netmon",
            "segments": [
                {"path": "tcp 10.0.0.5:5001→203.0.113.10:80 POST /api/order", "text": '{"name":"张三","phone":"13800138000","id_card":"11010519491231002X"}'}
            ],
        },
    )
    assert response.status_code == 201, response.text
    out = TrafficIngestOut(**response.json())
    assert out.segments == 1 and out.task_id

    task = _wait(lambda: client.get(f"/api/v1/tasks/{out.task_id}").json()["status"] == "done")
    assert task, "任务没有在超时内完成"
    detail = client.get(f"/api/v1/tasks/{out.task_id}").json()
    assert detail["total_findings"] == 1
    assert detail["source"] == "ingest:go-netmon"

    findings = client.get(f"/api/v1/tasks/{out.task_id}/findings").json()["items"]
    assert findings[0]["level"] == "L3"
    assert "已屏蔽" in findings[0]["masked"]
    assert ID_CARD not in findings[0]["masked"]


# ---------------- 实时跟随 ----------------


def test_live_follow_and_stop(client, database, data_dir: Path, live_manager) -> None:
    pcap_path = data_dir / "live.pcap"
    pcap_path.parent.mkdir(parents=True, exist_ok=True)
    pcap_path.write_bytes(_pcap_header())

    started = client.post(
        "/api/v1/traffic/live/start",
        json={"pcap_path": "live.pcap", "interval_ms": 120, "window_s": 0.3},
    )
    assert started.status_code == 201, started.text
    live = started.json()
    live_id = live["live_id"]
    assert live["running"] is True
    assert live["pcap_path"].endswith("live.pcap")

    # 追加一帧带敏感正文的报文，等跟随线程发现它并生成任务
    body = f'{{"name":"张三","phone":"{PHONE}","id_card":"{ID_CARD}"}}'
    pcap_path.write_bytes(pcap_path.read_bytes() + _record(200.0, _synth_frame(51001, body)))

    def get_live() -> dict:
        sessions = client.get("/api/v1/traffic/live").json()["sessions"]
        return next((item for item in sessions if item["live_id"] == live_id), {})

    assert _wait(lambda: get_live().get("tasks", 0) >= 1), get_live()
    session = get_live()
    assert session["packets"] >= 1
    assert session["segments"] >= 1
    task_id = session["last_task_id"]
    assert task_id

    detail = _wait(lambda: client.get(f"/api/v1/tasks/{task_id}").json()["status"] == "done")
    assert detail
    task = client.get(f"/api/v1/tasks/{task_id}").json()
    assert task["total_findings"] >= 1
    assert task["source"].startswith("live:")

    # 告警联动：窗口任务落库时同事务生成告警，会话状态能按会话聚合看到
    assert _wait(lambda: get_live().get("alert_total", 0) >= 1), get_live()
    session = get_live()
    assert session["alert_open"] >= 1
    assert session["alert_by_level"].get("L3", 0) >= 1
    assert session["last_alert_id"] and session["last_alert_title"]

    stopped = client.post(f"/api/v1/traffic/live/{live_id}/stop")
    assert stopped.status_code == 200
    assert stopped.json()["running"] is False
    assert stopped.json()["stopped_at"]

    # 已停止的会话可清理
    cleared = client.delete("/api/v1/traffic/live")
    assert cleared.status_code == 200
    assert "cleared" in cleared.json()


def test_live_start_accepts_data_prefixed_relative_path(client, database, data_dir: Path, live_manager) -> None:
    """前端/README 默认传 data/live.pcap，不能被解析成 data/data/live.pcap。"""
    target = data_dir / "live-prefixed.pcap"
    target.write_bytes(_pcap_header())
    started = client.post(
        "/api/v1/traffic/live/start",
        json={"pcap_path": "data/live-prefixed.pcap", "interval_ms": 300, "window_s": 1.0},
    )
    assert started.status_code == 201, started.text
    live_id = started.json()["live_id"]
    assert Path(started.json()["pcap_path"]).resolve() == target.resolve()
    assert client.post(f"/api/v1/traffic/live/{live_id}/stop").status_code == 200


def test_live_rejects_path_outside_data_dir(client, database, live_manager) -> None:
    response = client.post(
        "/api/v1/traffic/live/start",
        json={"pcap_path": "C:/Windows/System32/drivers/etc/hosts"},
    )
    assert response.status_code == 400
    assert "数据目录" in response.json()["detail"]


def test_live_duplicate_start_conflict(client, database, data_dir: Path, live_manager) -> None:
    (data_dir / "dup.pcap").write_bytes(_pcap_header())
    first = client.post(
        "/api/v1/traffic/live/start",
        json={"pcap_path": "dup.pcap", "interval_ms": 120, "window_s": 1.0},
    )
    assert first.status_code == 201
    second = client.post(
        "/api/v1/traffic/live/start",
        json={"pcap_path": "dup.pcap", "interval_ms": 120, "window_s": 1.0},
    )
    assert second.status_code == 409
    client.post(f"/api/v1/traffic/live/{first.json()['live_id']}/stop")


def test_live_stop_unknown_returns_404(client, database, live_manager) -> None:
    response = client.post("/api/v1/traffic/live/deadbeef/stop")
    assert response.status_code == 404


def test_demo_pcap_still_imports(client, database) -> None:
    """实时旁路不改变离线导入行为（回归）。"""
    response = client.post(
        "/api/v1/traffic/import",
        files={"pcap": ("demo.pcap", demo_pcap(), "application/vnd.tcpdump.pcap")},
    )
    assert response.status_code == 201, response.text
    assert response.json()["sessions"] == 3
    # write_pcap 与测试里手工拼的 pcap 应能互相读取（帧结构约定一致）
    assert len(list(__import__("app.traffic", fromlist=["iter_packets"]).iter_packets(write_pcap([])))) == 0


# ---------------- 长跑健壮性（体检清单 audit-core P2-5/P3-6/P3-7/P3-10） ----------------


def test_follower_resets_after_file_rotated_shorter(tmp_path: Path) -> None:
    """抓包工具轮转后文件变短：必须从头重读，而不是 seek 越界后永远读空。"""
    path = tmp_path / "rotated.pcap"
    frame = _synth_frame(40000, f"手机 {PHONE}")
    path.write_bytes(_pcap_header() + _record(1.0, frame))
    follower = PcapFollower(path)
    assert len(follower.poll()) == 1

    # 同路径换成更短的新文件（模拟 dumpcap 轮转）
    path.write_bytes(_pcap_header())
    assert follower.poll() == []
    new_frame = _synth_frame(40001, "新正文")
    with path.open("ab") as handle:
        handle.write(_record(2.0, new_frame))
    packets = follower.poll()
    assert len(packets) == 1, "轮转后应从新文件头继续解析"


def test_live_bounds_seen_and_pending(monkeypatch, tmp_path: Path) -> None:
    """去重集合与待落库队列都要有上限，长时间失败不能无限吃内存。"""
    from app.identify import DetectOptions
    from app.traffic import live as live_mod
    from app.traffic.pcap import RawPacket

    monkeypatch.setattr(live_mod, "MAX_SEEN_KEYS", 5)
    monkeypatch.setattr(live_mod, "MAX_PENDING_SEGMENTS", 3)
    session = live_mod._LiveSession(
        live_id="bounded",
        pcap_path=tmp_path / "x.pcap",
        source="live:x",
        interval_ms=10,
        window_s=999.0,
        options=DetectOptions(),
        db=None,  # type: ignore[arg-type]
        pipeline=None,  # type: ignore[arg-type]
    )
    for index in range(10):
        frame = _synth_frame(41000 + index, f"手机 {PHONE} #{index}")
        live_mod._ingest_packets(session, [RawPacket(ts=float(index), linktype=1, frame=frame)])
    assert len(session._seen) <= 5
    assert len(session._pending) <= 3
    assert session.dropped > 0


def test_flush_window_requeues_on_create_failure(monkeypatch, tmp_path: Path) -> None:
    """任务创建失败时正文不能丢：放回队头等下一个窗口重试。"""
    from app.identify import DetectOptions
    from app.parsers.base import Segment
    from app.traffic import live as live_mod

    monkeypatch.setattr(live_mod, "MAX_PENDING_SEGMENTS", 10)

    def boom(*args, **kwargs):
        raise RuntimeError("db is gone")

    monkeypatch.setattr(live_mod, "create_task", boom)
    session = live_mod._LiveSession(
        live_id="retry",
        pcap_path=tmp_path / "x.pcap",
        source="live:x",
        interval_ms=10,
        window_s=0.1,
        options=DetectOptions(),
        db=None,  # type: ignore[arg-type]
        pipeline=None,  # type: ignore[arg-type]
        _pending=[Segment(path="$", text="正文一"), Segment(path="$", text="正文二")],
    )
    live_mod._flush_window(session)
    assert [segment.text for segment in session._pending] == ["正文一", "正文二"]
    assert "create_task" in session.last_error


def test_live_concurrent_start_only_one_session(database, data_dir: Path, live_manager) -> None:
    """并发 start 同一文件只能成功一个，否则会重复落库 findings/审计。"""
    import threading

    from app.identify import DetectionPipeline
    from app.traffic.live import LiveError, get_live_manager

    path = data_dir / "race.pcap"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(demo_pcap())
    manager = get_live_manager()
    outcomes: list[str] = []
    barrier = threading.Barrier(2)

    def attempt() -> None:
        barrier.wait()
        try:
            manager.start(
                db=database,
                pipeline=DetectionPipeline(),
                pcap_path=path,
                interval_ms=60,
                window_s=0.5,
                options=__import__("app.identify", fromlist=["DetectOptions"]).DetectOptions(),
            )
            outcomes.append("ok")
        except LiveError:
            outcomes.append("dup")

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert sorted(outcomes) == ["dup", "ok"]
    manager.stop_all()
