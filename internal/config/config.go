// Package config 负责加载系统配置。
// MVP 使用 TOML 的轻量子集：支持 [section] 与 key = value（字符串/整数/布尔/浮点），
// 未知字段忽略，缺失字段使用默认值，便于后续扩展配置项。
package config

import (
	"bufio"
	"fmt"
	"os"
	"strconv"
	"strings"
	"time"
)

// AlertConfig 汇总可选告警配置；阈值为 0 表示不启用对应规则。
type AlertConfig struct {
	Enabled            bool    // 是否启用告警引擎
	BpsThreshold       float64 // traffic.bps 阈值（bit/s）
	BpsForSecs         int     // 持续超过阈值多少秒触发
	PpsThreshold       float64 // traffic.pps 阈值（包/s）
	PpsForSecs         int
	ConnsThreshold     float64 // traffic.conns 并发连接阈值
	ConnsForSecs       int
	DropsThreshold     float64 // traffic.drops 每秒丢包阈值
	DropsForSecs       int
	Webhook            string // 触发通知 URL（可选）
	AutoCapture        bool   // 告警触发时是否自动导出抓包（默认关闭）
	AutoCaptureSeconds int    // 自动抓包时长（秒）
}

// UpdateConfig 汇总自升级配置（默认关闭，需显式开启）。
type UpdateConfig struct {
	Enabled         bool
	AllowedPrefixes []string // 允许下载的 URL 前缀，为空则一律拒绝
	MaxMB           int      // 单文件大小上限（MB）
	Service         string   // 重启用的 systemd 单元名
}

// PluginConfig 汇总界面插件框架配置（默认不加载任何插件）。
type PluginConfig struct {
	Dir string // 界面插件清单目录（*.json 声明式清单）；为空表示不启用
}

// DLPConfig 汇总在线 DLP 接入配置（默认关闭，显式开启后采集链路才做内容级检测）。
//
// 为什么放在配置里而不是写死：在线检测会按 workers 数量起常驻 goroutine，
// 并对每个含载荷的报文做一次规则匹配；不需要 DLP 的纯流量监控场景应能完全不付这份成本。
type DLPConfig struct {
	Enabled    bool   // 是否在采集链路里做在线 L1 检测
	Mode       string // live | offline：仅用于日志展示，实际模式由 core.source 决定
	QueueSize  int    // 待分析载荷队列长度，满则丢帧并计入 dlp_dropped
	Workers    int    // 识别 worker 数（默认 4）
	MaxPayload int    // 单条载荷最多分析的字节数，超长截断（默认 64KB）
	MinLevel   string // 低于该级别的命中不落库：SAFE/L1/L2/L3（默认 SAFE）
}

// Config 汇总各模块需要的运行参数。
type Config struct {
	MachineID  string        // 机器标识，未来多机对比时的标签
	Source     string        // 数据源：synthetic | replay | live
	Iface      string        // 监控网卡（live 模式使用）
	Filter     string        // 采集过滤表达式（pcap 常用子集，用户态匹配；空表示不过滤）
	ReplayFile string        // pcap 回放文件路径
	Offline    bool          // 离线模式：用报文时间戳驱动聚合与过期，回放结束后不退出进程（默认 false）
	Pps        int           // synthetic 数据源每秒产生包数
	Readers    int           // live 抓包并行队列数（1~8，默认 1）
	CPUPin     bool          // 多队列时是否把读循环绑定到不同 CPU（默认 false）
	Tick       time.Duration // 指标聚合周期
	DataDir    string        // 数据存储目录
	Storage    string        // 存储后端：sqlite | file
	SQLitePath string        // sqlite 数据库文件路径
	Retention  time.Duration // 数据保留时长
	Listen     string        // HTTP 监听地址
	APIToken   string        // 可选 API 访问令牌（Bearer Token），空表示不鉴权
	Alert      AlertConfig   // 可选告警规则
	Update     UpdateConfig  // 可选自升级配置
	Plugins    PluginConfig  // 可选界面插件目录
	DLP        DLPConfig     // 可选在线 DLP 检测
}

// Default 返回开箱即用的默认配置（synthetic 数据源，便于无网卡环境演示）。
func Default() *Config {
	return &Config{
		MachineID:  "host-1",
		Source:     "synthetic",
		Iface:      "eth0",
		Pps:        200,
		Readers:    1,
		Tick:       time.Second,
		DataDir:    "data",
		Storage:    "sqlite",
		SQLitePath: "data/netmon.db",
		Retention:  24 * time.Hour,
		Listen:     ":8080",
		Alert: AlertConfig{
			BpsForSecs:         30,
			PpsForSecs:         30,
			ConnsForSecs:       30,
			DropsForSecs:       30,
			AutoCaptureSeconds: 15,
		},
		Update: UpdateConfig{
			Enabled: false,
			MaxMB:   64,
			Service: "netmon",
		},
		DLP: DLPConfig{
			Enabled:    false,
			Mode:       "live",
			QueueSize:  4096,
			Workers:    4,
			MaxPayload: 65536,
			MinLevel:   "SAFE",
		},
	}
}

// Load 从 TOML 文件加载配置，解析失败时返回错误。
//
// 注意：这里会剥掉文件开头的 UTF-8 BOM。Windows 上很多工具（例如 PowerShell 5.1 的
// `Set-Content -Encoding utf8`、记事本「另存为 UTF-8」）会写出带 BOM 的文件，
// 而 strings.TrimSpace 不剥 U+FEFF，会让首行变成 "\uFEFF[core]"、匹配不上 section 头，
// 于是整个 [core] 段被静默忽略（表现为「部署成功了但配置没生效」，非常难查）。
func Load(path string) (*Config, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()

	values := map[string]string{}
	section := ""
	sc := bufio.NewScanner(f)
	first := true
	for sc.Scan() {
		line := sc.Text()
		if first {
			line = strings.TrimPrefix(line, "\uFEFF")
			first = false
		}
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		if strings.HasPrefix(line, "[") && strings.HasSuffix(line, "]") {
			section = strings.TrimSpace(line[1 : len(line)-1])
			continue
		}
		eq := strings.Index(line, "=")
		if eq < 0 {
			continue
		}
		key := strings.TrimSpace(line[:eq])
		val := strings.TrimSpace(line[eq+1:])
		if section != "" {
			key = section + "." + key
		}
		values[key] = unquote(val)
	}
	if err := sc.Err(); err != nil {
		return nil, err
	}

	cfg := Default()
	cfg.apply(values)
	if cfg.Source == "replay" && cfg.ReplayFile == "" {
		return nil, fmt.Errorf("source=replay 时必须配置 replay_file")
	}
	return cfg, nil
}

func (c *Config) apply(v map[string]string) {
	if s, ok := v["core.machine_id"]; ok {
		c.MachineID = s
	}
	if s, ok := v["core.source"]; ok {
		c.Source = s
	}
	if s, ok := v["core.offline"]; ok {
		if b, err := strconv.ParseBool(s); err == nil {
			c.Offline = b
		}
	}
	if s, ok := v["capture.iface"]; ok {
		c.Iface = s
	}
	if s, ok := v["capture.filter"]; ok {
		c.Filter = s
	}
	if s, ok := v["capture.replay_file"]; ok {
		c.ReplayFile = s
	}
	if s, ok := v["capture.cpu_pin"]; ok {
		if b, err := strconv.ParseBool(s); err == nil {
			c.CPUPin = b
		}
	}
	if n, ok := v["capture.readers"]; ok {
		if i, err := strconv.Atoi(n); err == nil && i > 0 {
			c.Readers = i
		}
	}
	if n, ok := v["capture.pps"]; ok {
		if i, err := strconv.Atoi(n); err == nil && i > 0 {
			c.Pps = i
		}
	}
	if n, ok := v["core.tick_ms"]; ok {
		if i, err := strconv.Atoi(n); err == nil && i > 0 {
			c.Tick = time.Duration(i) * time.Millisecond
		}
	}
	if s, ok := v["storage.data_dir"]; ok {
		c.DataDir = s
	}
	if s, ok := v["storage.storage"]; ok {
		c.Storage = s
	}
	if s, ok := v["storage.sqlite_path"]; ok {
		c.SQLitePath = s
	}
	if n, ok := v["storage.retention_hours"]; ok {
		if i, err := strconv.Atoi(n); err == nil && i > 0 {
			c.Retention = time.Duration(i) * time.Hour
		}
	}
	if s, ok := v["api.listen"]; ok {
		c.Listen = s
	}
	if s, ok := v["api.token"]; ok {
		c.APIToken = s
	}
	if s, ok := v["alert.enabled"]; ok {
		if b, err := strconv.ParseBool(s); err == nil {
			c.Alert.Enabled = b
		}
	}
	if f, ok := v["alert.bps_threshold"]; ok {
		if x, err := strconv.ParseFloat(f, 64); err == nil && x > 0 {
			c.Alert.BpsThreshold = x
		}
	}
	if n, ok := v["alert.bps_for_secs"]; ok {
		if i, err := strconv.Atoi(n); err == nil && i > 0 {
			c.Alert.BpsForSecs = i
		}
	}
	if f, ok := v["alert.pps_threshold"]; ok {
		if x, err := strconv.ParseFloat(f, 64); err == nil && x > 0 {
			c.Alert.PpsThreshold = x
		}
	}
	if n, ok := v["alert.pps_for_secs"]; ok {
		if i, err := strconv.Atoi(n); err == nil && i > 0 {
			c.Alert.PpsForSecs = i
		}
	}
	if f, ok := v["alert.conns_threshold"]; ok {
		if x, err := strconv.ParseFloat(f, 64); err == nil && x > 0 {
			c.Alert.ConnsThreshold = x
		}
	}
	if n, ok := v["alert.conns_for_secs"]; ok {
		if i, err := strconv.Atoi(n); err == nil && i > 0 {
			c.Alert.ConnsForSecs = i
		}
	}
	if f, ok := v["alert.drops_threshold"]; ok {
		if x, err := strconv.ParseFloat(f, 64); err == nil && x > 0 {
			c.Alert.DropsThreshold = x
		}
	}
	if n, ok := v["alert.drops_for_secs"]; ok {
		if i, err := strconv.Atoi(n); err == nil && i > 0 {
			c.Alert.DropsForSecs = i
		}
	}
	if s, ok := v["alert.webhook"]; ok {
		c.Alert.Webhook = s
	}
	if s, ok := v["alert.auto_capture"]; ok {
		if b, err := strconv.ParseBool(s); err == nil {
			c.Alert.AutoCapture = b
		}
	}
	if s, ok := v["update.enabled"]; ok {
		if b, err := strconv.ParseBool(s); err == nil {
			c.Update.Enabled = b
		}
	}
	if s, ok := v["update.allowed_prefixes"]; ok {
		c.Update.AllowedPrefixes = nil
		for _, part := range strings.Split(s, ",") {
			if p := strings.TrimSpace(part); p != "" {
				c.Update.AllowedPrefixes = append(c.Update.AllowedPrefixes, p)
			}
		}
	}
	if n2, ok := v["update.max_mb"]; ok {
		if i, err := strconv.Atoi(n2); err == nil && i > 0 {
			c.Update.MaxMB = i
		}
	}
	if s, ok := v["update.service"]; ok {
		c.Update.Service = s
	}
	if s, ok := v["plugins.dir"]; ok {
		c.Plugins.Dir = s
	}
	if s, ok := v["dlp.enabled"]; ok {
		if b, err := strconv.ParseBool(s); err == nil {
			c.DLP.Enabled = b
		}
	}
	if s, ok := v["dlp.mode"]; ok {
		if s == "live" || s == "offline" {
			c.DLP.Mode = s
		}
	}
	if n, ok := v["dlp.queue_size"]; ok {
		if i, err := strconv.Atoi(n); err == nil && i > 0 && i <= 65536 {
			c.DLP.QueueSize = i
		}
	}
	if n, ok := v["dlp.workers"]; ok {
		if i, err := strconv.Atoi(n); err == nil && i > 0 && i <= 64 {
			c.DLP.Workers = i
		}
	}
	if n, ok := v["dlp.max_payload"]; ok {
		if i, err := strconv.Atoi(n); err == nil && i > 0 {
			c.DLP.MaxPayload = i
		}
	}
	if s, ok := v["dlp.min_level"]; ok {
		if _, err := strconv.Atoi(s); err != nil {
			// 级别名（SAFE/L1/L2/L3）在这里只做大写归一，合法性由 app 层用 dlp.ParseLevel 校验：
			// config 包不依赖识别层，避免为一处校验引入包依赖。
			c.DLP.MinLevel = strings.ToUpper(strings.TrimSpace(s))
		}
	}
	if n, ok := v["alert.auto_capture_seconds"]; ok {
		if i, err := strconv.Atoi(n); err == nil && i > 0 {
			c.Alert.AutoCaptureSeconds = i
		}
	}
}

func unquote(s string) string {
	s = strings.TrimSpace(s)
	if len(s) >= 2 && ((s[0] == '"' && s[len(s)-1] == '"') || (s[0] == '\'' && s[len(s)-1] == '\'')) {
		return s[1 : len(s)-1]
	}
	return s
}
