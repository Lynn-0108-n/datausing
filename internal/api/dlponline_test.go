package api

import (
	"net/http/httptest"
	"testing"
	"time"

	"github.com/wukiQAQ/datausing/internal/aggregator"
	"github.com/wukiQAQ/datausing/internal/config"
	"github.com/wukiQAQ/datausing/internal/flow"
	"github.com/wukiQAQ/datausing/internal/storage"
	"github.com/wukiQAQ/datausing/internal/webui"
)

// newDLPTestServer 装配一个注入了在线检测计数的测试服务。
func newDLPTestServer(t *testing.T, stats func() (uint64, uint64, uint64)) *httptest.Server {
	t.Helper()
	cfg := config.Default()
	store, err := storage.OpenFileStore(t.TempDir(), time.Hour)
	if err != nil {
		t.Fatalf("OpenFileStore: %v", err)
	}
	t.Cleanup(func() { _ = store.Close() })
	srv := New(cfg, aggregator.New(100), flow.NewTable(time.Minute, 5*time.Minute, nil), store, webui.FS)
	if stats != nil {
		srv.SetDLPStats(stats)
	}
	ts := httptest.NewServer(srv.Handler())
	t.Cleanup(ts.Close)
	return ts
}

// TestStatsReportsOnlineDLPCounters 校验 /traffic/now 下发在线检测计数：
// 客户端据此显示"在线检测中/队列丢帧"，而不是只看历史结果。
func TestStatsReportsOnlineDLPCounters(t *testing.T) {
	ts := newDLPTestServer(t, func() (uint64, uint64, uint64) { return 12, 3, 1 })
	var m map[string]any
	getJSON(t, ts.URL+"/api/v1/traffic/now", &m)
	if online, _ := m["dlp_online"].(bool); !online {
		t.Fatalf("dlp_online=%v, want true（已注入计数源）", m["dlp_online"])
	}
	for key, want := range map[string]float64{"dlp_frames": 12, "dlp_hits": 3, "dlp_dropped": 1} {
		got, ok := m[key].(float64)
		if !ok || got != want {
			t.Errorf("%s=%v, want %v", key, m[key], want)
		}
	}
}

// TestStatsDLPDisabledByDefault 校验未启用在线检测时计数为 0 且 dlp_online=false，
// 避免前端把"没启用"误判成"已启用但一直没数据"。
func TestStatsDLPDisabledByDefault(t *testing.T) {
	ts := newDLPTestServer(t, nil)
	var m map[string]any
	getJSON(t, ts.URL+"/api/v1/traffic/now", &m)
	if online, _ := m["dlp_online"].(bool); online {
		t.Fatalf("dlp_online=%v, want false", m["dlp_online"])
	}
	for _, key := range []string{"dlp_frames", "dlp_hits", "dlp_dropped"} {
		if got, ok := m[key].(float64); !ok || got != 0 {
			t.Errorf("%s=%v, want 0", key, m[key])
		}
	}
}
