package dlp

// TypeKeys 把展示用的中文类别名转成存储/接口用的英文键（id_card、phone…）。
//
// 解析不出来的原样保留：宁可查到怪值，也不要静默丢数据。
// 在线检测（internal/app）与离线 CLI（cmd/flowmask）共用这一份转换，
// 否则两边对同一批命中的 types 字段可能写出不同键，前端筛选会对不上。
func TypeKeys(labels []string) []string {
	keys := make([]string, 0, len(labels))
	for _, label := range labels {
		if typ, ok := ParseEntityType(label); ok {
			keys = append(keys, string(typ))
			continue
		}
		keys = append(keys, label)
	}
	return keys
}
