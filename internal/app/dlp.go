// 在线 DLP 检测：把采集链路的载荷投递到有界队列，由 worker 池异步做 L1 识别。
//
// 设计要点（与《设计文档》§2.2/§2.3 一致）：
//   - 不进主链路：采集侧只复制一次载荷快照并投递，队列满就丢帧并计数，绝不阻塞抓包；
//   - 只落脱敏副本：findings 的 masked 字段是脱敏后的载荷摘要，原始敏感值不入库；
//   - 失败可观测：丢弃帧数（dlp_dropped）、分析帧数（dlp_frames）、命中数（dlp_hits）
//     通过 /api/v1/traffic/now 暴露，避免"静默丢"。
package app

import (
	"errors"
	"fmt"
	"log"
	"strings"
	"sync"
	"sync/atomic"
	"time"
	"unicode/utf8"

	"github.com/wukiQAQ/datausing/internal/config"
	"github.com/wukiQAQ/datausing/internal/dlp"
	"github.com/wukiQAQ/datausing/internal/parser"
	"github.com/wukiQAQ/datausing/internal/storage"
)

// dlpPreviewRunes 是落库的脱敏摘要最大字符数：够看清"什么被替换成了什么"，
// 又不会让一条 finding 把详情页/导出文件撑爆。
const dlpPreviewRunes = 200

// dlpJob 是一次待分析载荷的快照。payload 必须是复制品：capture.Source 可能复用
// 底层读缓冲，异步 worker 直接引用会导致数据竞争或内容被后续报文覆盖。
type dlpJob struct {
	ts             time.Time
	srcIP, dstIP   string
	srcPort        uint16
	dstPort        uint16
	proto          string
	payload        []byte
	payloadBytes   int
	payloadTruncated bool
}

// dlpPipeline 是有界队列 + 固定 worker 池。
type dlpPipeline struct {
	cfg      config.DLPConfig
	minLevel dlp.Level
	store    storage.FindingStore
	jobs     chan dlpJob
	wg       sync.WaitGroup
	closeOne sync.Once

	frames  atomic.Uint64 // 已分析的非空载荷数
	hits    atomic.Uint64 // 达到 minLevel 并成功落库的条数
	dropped atomic.Uint64 // 队列满被丢弃的载荷数
}

// newDLPPipeline 校验配置并启动 worker。
func newDLPPipeline(store storage.FindingStore, cfg config.DLPConfig) (*dlpPipeline, error) {
	if store == nil {
		return nil, errors.New("当前存储后端不支持 findings（需要 sqlite 或 file 后端）")
	}
	if !cfg.Enabled {
		return nil, errors.New("DLP 未启用")
	}
	minLevel, err := dlp.ParseLevel(cfg.MinLevel)
	if err != nil {
		return nil, fmt.Errorf("dlp.min_level 无效：%w", err)
	}
	if cfg.Workers <= 0 {
		cfg.Workers = 1
	}
	if cfg.QueueSize <= 0 {
		cfg.QueueSize = 1
	}
	if cfg.MaxPayload <= 0 {
		cfg.MaxPayload = 65536
	}
	p := &dlpPipeline{
		cfg:      cfg,
		minLevel: minLevel,
		store:    store,
		jobs:     make(chan dlpJob, cfg.QueueSize),
	}
	for i := 0; i < cfg.Workers; i++ {
		p.wg.Add(1)
		go p.worker()
	}
	return p, nil
}

// submit 非阻塞投递：队列满时计数并丢帧，保证抓包主循环不被 DLP 拖慢。
func (p *dlpPipeline) submit(job dlpJob) {
	select {
	case p.jobs <- job:
	default:
		if n := p.dropped.Add(1); n == 1 || n%1000 == 0 {
			log.Printf("[dlp] 待分析队列已满，累计丢弃 %d 帧（可调大 dlp.queue_size 或 dlp.workers）", n)
		}
	}
}

func (p *dlpPipeline) worker() {
	defer p.wg.Done()
	for job := range p.jobs {
		p.analyze(job)
	}
}

// analyze 对单条载荷做 L1 识别→评分分级→脱敏落库。
func (p *dlpPipeline) analyze(job dlpJob) {
	text := strings.TrimSpace(string(job.payload))
	if text == "" {
		return
	}
	p.frames.Add(1)
	res := dlp.Analyze(text)
	if len(res.Hits) == 0 || res.Level < p.minLevel {
		return
	}
	finding := storage.DLPFinding{
		Ts:           job.ts,
		SrcIP:        job.srcIP,
		SrcPort:      job.srcPort,
		DstIP:        job.dstIP,
		DstPort:      job.dstPort,
		Proto:        job.proto,
		Level:        res.Level.String(),
		Score:        res.Score,
		Types:        dlp.TypeKeys(res.TypeNames()),
		Hits:         len(res.Hits),
		Masked:       maskedPreview(dlp.Mask(text, res)),
		Model:        "l1", // 采集端只做 L1；L2/L3 由 server/ 的推理服务负责
		PayloadBytes: job.payloadBytes,
		Direct:       res.Direct,
	}
	if job.payloadTruncated {
		// 截断是可观测的信息：说明这条结果不是基于完整正文得出的，展示时不要当成全量结论。
		finding.Reasons = []string{fmt.Sprintf("载荷超过 max_payload=%d 字节，仅分析前 %d 字节", p.cfg.MaxPayload, p.cfg.MaxPayload)}
	}
	if err := p.store.AppendFinding(finding); err != nil {
		log.Printf("[dlp] 检测结果落库失败: %v", err)
		return
	}
	p.hits.Add(1)
}

// close 停止接收新任务并等待在途任务写完；可重复调用。
func (p *dlpPipeline) close() {
	p.closeOne.Do(func() {
		close(p.jobs)
		p.wg.Wait()
	})
}

// maskedPreview 把脱敏后的载荷压成单行并截断：落库的是"可直接展示的摘要"，
// 不是原始正文；多行 JSON/HTTP 报文压平后详情页更好读。
func maskedPreview(masked string) string {
	flat := strings.Join(strings.Fields(masked), " ")
	if utf8.RuneCountInString(flat) <= dlpPreviewRunes {
		return flat
	}
	runes := []rune(flat)
	return string(runes[:dlpPreviewRunes]) + "…"
}

// protoName 把协议号转成展示名（与 cmd/flowmask 的字段契约一致）。
func protoName(p uint8) string {
	switch p {
	case parser.ProtoTCP:
		return "TCP"
	case parser.ProtoUDP:
		return "UDP"
	case parser.ProtoICMP:
		return "ICMP"
	case parser.ProtoICMPv6:
		return "ICMPv6"
	default:
		return fmt.Sprintf("proto-%d", p)
	}
}
