# 词云环 · 系统架构文档

## 1. 系统架构图

```
┌────────────────────────────────────────────────────────────────────┐
│                   数据源 (Data Source)                               │
│  welive.exe export-session --readable → chat_history.md (Markdown)   │
└──────────────────────────────┬─────────────────────────────────────┘
                               │
                               ▼
┌────────────────────────────────────────────────────────────────────┐
│                    Flask Web 服务器 (app.py)                        │
│                                                                    │
│  GET  /                        → frontend/app.html (三阶段 SPA)    │
│  POST /api/extract             → SSE 流式提取 (subprocess)         │
│  GET  /api/session/<id>/candidates → 候选词 JSON                   │
│  POST /api/build-ring          → 权重计算 + 环数据                 │
│                                                                    │
│  特性: SSE text/event-stream | threading.Lock session 存储          │
│        | stream_with_context | 内联权重算法                         │
└──────────────────────────────┬─────────────────────────────────────┘
                               │
          ┌────────────────────┼────────────────────┐
          ▼                    ▼                    ▼
┌──────────────────┐ ┌──────────────────┐ ┌──────────────────┐
│  Stage 1: 提取   │ │  Stage 2: 筛选   │ │  Stage 3: 环     │
│                  │ │                  │ │                  │
│  上传 .md 文件   │ │  逐个审核候选词  │ │  词云环渲染      │
│  SSE 实时进度    │ │  A保留 R删除     │ │  拖拽/滚轮/键盘  │
│  extract_words   │ │  S跳过 Ctrl+Z    │ │  设置面板 ⚙️     │
│  (subprocess)    │ │  localStorage    │ │  下载演示HTML    │
└──────────────────┘ └──────────────────┘ └──────────────────┘
```

### 数据流（Web 管道）

```
chat_history.md
  │
  │ POST /api/extract (multipart/form-data)
  │   → subprocess: extract_words.py
  │   → SSE 逐行推送进度
  │   → 完成后写入 sessions[session_id]
  ▼
candidate_words.json (session 内)
  │
  │ GET /api/session/<id>/candidates
  │   → 返回完整候选词数组
  ▼
Stage 2: 人工筛选 (浏览器内)
  │   A/R/S 键盘操作 → keepSet / removeSet / skipOrder
  │   localStorage 持久化进度
  ▼
keep_words (用户筛选结果 + 自定义词)
  │
  │ POST /api/build-ring
  │   → 内联权重计算 (log2(count+1)×5 + PMI×3 + 熵×2)
  │   → power-curve 字号映射 (14~72px)
  ▼
ring_words [{word, count, weight, fontSize, ...}]
  │
  ▼
Stage 3: 词云环可视化
  │   Fisher-Yates 打乱 → 贪心最短行 → 等宽补全 → 4副本渲染
  │
  ├─→ 浏览器内实时预览
  └─→ downloadStandaloneHTML() → word_ring_demo.html (自包含)
```

## 2. 后端架构

### 2.1 Session 管理

```
sessions: dict[session_id] = {
    'status': 'running' | 'done' | 'error',
    'dir': str,           # session 文件目录
    'chat_file': str,     # 上传的聊天文件路径
    'count': int,         # 候选词数量
    'candidates': list,   # 候选词数组
}
```

使用 `threading.Lock` 保证多线程安全。Session 在服务器重启后丢失。

### 2.2 SSE 流式提取

```
POST /api/extract
  Content-Type: multipart/form-data
  ↓
Response:
  Content-Type: text/event-stream
  Cache-Control: no-cache
  Connection: keep-alive

  data: {"type":"progress","text":"..."}
  data: {"type":"progress","text":"..."}
  ...
  data: {"type":"complete","count":1500,"session_id":"abc123"}
```

使用 `subprocess.Popen` 逐行读取 `extract_words.py` 的 stdout，通过 `stream_with_context` 生成 SSE 事件流。

### 2.3 权重计算（内联于 app.py）

```python
def calc_weight(info):
    """综合权重: 频次(log) + PMI + 双端熵"""
    count = info.get('count', 1)
    pmi = info.get('pmi', 0) or 0
    le = info.get('left_entropy', 0) or 0
    re = info.get('right_entropy', 0) or 0
    return round(math.log2(max(count, 1) + 1) * 5 + pmi * 3 + (le + re) * 2, 1)

def font_size(weight, w_min, w_max):
    """权重 → 字号 (power curve)"""
    if w_max == w_min:
        return (FONT_MIN + FONT_MAX) / 2
    ratio = (weight - w_min) / (w_max - w_min)
    ratio = ratio ** POWER_CURVE  # 0.55
    return round(FONT_MIN + ratio * (FONT_MAX - FONT_MIN), 1)
```

## 3. 前端架构

### 3.1 状态机

```
STATE = {
    stage: 1 | 2 | 3,
    sessionId: null | string,
    candidates: [],      # Stage 1 产出
    currentIndex: 0,     # Stage 2 进度
    keepSet: Set,        # 保留
    removeSet: Set,      # 删除
    skipOrder: [],       # 跳过（有序）
    skipSet: Set,        # 跳过（快速查找）
    history: [],         # 撤销栈
    customWords: [],     # 手动添加
    ringWords: [],       # Stage 3 输入
    ringSettings: { rows: 8, speed: 3, colors: [...] },
    ringAutoScroll: true,
    ringAutoSpeed: 0.6,
    ringRAF: null,
    ringCurrentX: 0,
}
```

### 3.2 SSE 客户端

```javascript
const resp = await fetch('/api/extract', { method: 'POST', body: formData });
const reader = resp.body.getReader();
const decoder = new TextDecoder();
let buffer = '';

while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    // 解析 SSE 帧: "data: {...}\n\n"
    // type=progress → 更新控制台日志
    // type=complete → 触发 fetchCandidates()
}
```

### 3.3 自包含 HTML 导出

`downloadStandaloneHTML()` 函数生成完整的单文件 HTML，内嵌：
- 完整的 CSS 样式（暗色主题）
- 词环数据（`EMBEDDED_WORDS` JSON）
- 放置引擎（`computePlacements`）
- 渲染引擎（4 副本 + 循环滚动）
- 交互引擎（拖拽/滚轮/键盘/触摸）
- 设置面板（行数/速度/颜色）

## 4. 核心算法

### 4.1 通道 A: jieba 分词

使用 jieba 精确模式对清洗后的文本分词，统计词频。jieba 返回大量通用高频词（如"我们"、"这个"），因此应用 ×0.6 降权系数，为新词发现腾出配额空间。

### 4.2 通道 B: n-gram + PMI + 邻接熵

这是新词发现的核心算法，完全不依赖词典。

**PMI (Pointwise Mutual Information，点互信息)** 衡量 n-gram 内部字符的凝聚度：

```
PMI(xy) = log( P(xy) / (P(x) × P(y)) )

其中:
  P(xy) = count(xy) / N_total       # n-gram在语料中出现的概率
  P(x)  = count(x)  / N_char_total  # 单个字符出现的概率
```

PMI 越高 → 字符组合越"黏在一起"→ 越像一个真正的词。

**硬阈值**: `PMI_MIN = 3.0`（低于此值直接淘汰）
**软阈值**: `PMI_SOFT_MIN = 1.5`（高频词 count≥20 时放宽）

**邻接熵 (Branching Entropy)** 衡量词的边界自由度：

```
左熵 = -Σ P(left_char | word) × log P(left_char | word)
右熵 = -Σ P(right_char | word) × log P(right_char | word)
```

熵越高 → 左右邻居越多样化 → 越是一个独立的词（而非某长词的固定片段）。

**硬阈值**: `ENTROPY_MIN = 1.0`
**软阈值**: `ENTROPY_SOFT_MIN = 0.6`

### 4.3 通道 C: 拼音谐音梗检测

基于观察：聊天中的谐音梗（如"蚌埠住了"→"绷不住了"）在拼音空间上高度相似。

```
算法流程:
1. 对被 jieba 标记为"不在词典"的 n-gram 做 pypinyin 标注 (TONE3 模式)
2. 对每个已知 jieba 词同样标注拼音
3. 计算编辑距离:
     similarity = 1 - edit_distance(pinyin_a, pinyin_b)
                    / max(len(pinyin_a), len(pinyin_b))
4. similarity ≥ 75% → 标记为谐音梗候选
```

示例: `蚌埠(beng bu) → 绷不(beng bu)` 编辑距离 0/7 → 相似度 100%。

**阈值**: `PINYIN_SIMILARITY_MIN = 0.75`
**编辑距离上限**: `MAX_PINYIN_EDIT_DIST = 1`

### 4.4 通道 D: 纯拉丁词检测

检测拼音首字母缩写（如 `yyds` = 永远的神）。

```
算法流程:
1. 正则提取纯拉丁词: /[a-zA-Z]{2,5}/
2. PMI + 邻接熵 过滤 (阈值比中文更严格)
3. 80+ 黑名单过滤常见英文碎片 (er, re, ing, the, and, com, jpg...)
4. 最终评分 ≥ 25 → 进入候选
```

**参数**: `LATIN_WORD_MIN_LEN=2, MAX_LEN=5, MIN_COUNT=3, PMI_MIN=4.0, ENTROPY_MIN=0.8, QUOTA=50`

### 4.5 通道合并评分与配额

```python
# 基础评分 (所有通道通用)
score = log2(count + 1) * 5   # 频次分
      + PMI * 3               # 凝聚度分
      + (左熵 + 右熵) * 2     # 边界自由度分

# 来源调权
if source == 'jieba':  score *= 0.6      # jieba 词降权
if word not in jieba_dict: score += 15   # 非词典词加分 (网络新词信号)

# 配额分配 (总量 MAX_CANDIDATES=1500)
#   jieba:  55% (≈825)  — 常规高频词
#   ngram:  35% (≈525)  — 新词发现
#   latin:   8% (≈120)  — 拼音缩写
#   pinyin:  2% (≈30)   — 谐音梗
```

## 5. 词云环引擎设计

### 5.1 放置算法 (computePlacements)

```
输入: wordData (N个词，每个有 fontSize → 估算像素宽度)
参数: numRows (默认8，可通过设置面板调整 3~16)

Step 1: Fisher-Yates 随机打乱词序
        → 不同大小词混合出现，每次重排结果不同

Step 2: 初始化 rowWidths[0..numRows-1] = 0
        rowWords[0..numRows-1] = []

Step 3: 贪心最短行放置
        for each word in shuffled:
          找 rowWidths 最小的行 bestRow
          x = rowWidths[bestRow]
          rowWidths[bestRow] += wordWidth + minGap
          rowWords[bestRow].push({wordObj, x, w: wordWidth})

Step 4: 强制等宽
        bandTotalWidth = max(rowWidths)  ← 精确最大值
        for each row:
          deficit = bandTotalWidth - rowWidths[row]
          将 deficit 像素分配到该行 N 个 gap 中:
            extraPerGap = floor(deficit / N)
            remainder = deficit % N
            前 remainder 个 gap 各多 1px
          → 重算每个词的 x 坐标
          → 行宽精确 = bandTotalWidth
```

**关键性质**:
- 所有行宽度精确相等 → 环首尾完美无缝
- 贪心最短行 → 各行自然均衡（无需碰撞检测）
- Fisher-Yates 随机 → 每次重排产生新鲜视觉

### 5.2 渲染引擎

```
4 份副本渲染: copy ∈ { -1, 0, 1, 2 }
  每份副本水平偏移 = copy × bandTotalWidth
  所有词在 (offsetX + 原始x) 位置渲染

滚动循环:
  currentX ∈ [-2×bandTotalWidth, 0]
  currentX > 0      → currentX -= bandTotalWidth
  currentX < -2×W   → currentX += bandTotalWidth
  CSS transform: translateX(currentX)

动画:
  requestAnimationFrame → 每帧 currentX -= autoSpeed (自动滚动)
  或手动控制 currentX (拖拽/滚轮/键盘)
```

### 5.3 交互引擎

| 交互 | 实现 |
|------|------|
| 拖拽滚动 | mousedown/touchstart → 记录起始位置 → mousemove/touchmove 更新 currentX |
| 滚轮 | wheel 事件 → deltaY → currentX |
| 键盘 | ← → 方向键 |
| 自动滚动 | 空格键切换 / 设置面板默认开启 |
| 设置面板 | ⚙️ 按钮 → 模态框 (行数/速度/颜色) |

## 6. 文件格式规格

### candidate_words.json

```json
[{
  "word": "蚌埠住了",
  "count": 42,
  "source": "pinyin",
  "pmi": 5.2,
  "left_entropy": 2.1,
  "right_entropy": 1.8,
  "score": 38.5,
  "reason": "不在词典 / 谐音梗 → 绷不住了",
  "homophones": [{"word": "绷不住了", "similarity": 0.92}]
}]
```

### ring_words.json

```json
[{
  "word": "哈哈",
  "tag": "keep",
  "count": 31907,
  "weight": 72.5,
  "fontSize": 72.0,
  "pmi": 6.8,
  "left_entropy": 3.2,
  "right_entropy": 2.9,
  "source": "jieba",
  "reason": "",
  "homophone": null
}]
```

## 7. 关键技术决策

| 决策 | 理由 |
|------|------|
| Flask + SSE 流式提取 | 实时展示进度，用户体验优于轮询 |
| 内联权重计算 | 减少文件 I/O，无需调用外部脚本 |
| jieba 降权 ×0.6 | jieba 返回大量通用高频词，需为新词腾出配额空间 |
| 非词典词 +15 加分 | 聊天中真正有趣的词往往是词典外的网络新词 |
| 拉丁词黑名单 80+ | 通道 D 不加黑名单会返回大量英文碎片 |
| 纯拉丁词独立通道 | 拉丁词混入中文评分导致评分体系混乱 |
| power-curve 字号 (^0.55) | 避免少数超高频词独占视觉，使分布更均匀 |
| 贪心最短行放置 | 自然均衡，无需碰撞检测，比随机放置+碰撞更简洁可靠 |
| +1px 等宽补全 | 强制每行宽度精确相等，确保环首尾完美相接 |
| 4 副本渲染 | 只需维护一个滚动偏移，边界自动循环 |
| Fisher-Yates 随机 | 解耦视觉呈现与权重，每次重排都不同 |
| 随机颜色 | 解耦颜色与词来源/权重，视觉效果更丰富自然 |
| 自包含 HTML 导出 | 双击即可演示，无需服务器，方便分享 |
| localStorage 进度 | 筛选进度不丢失，关闭浏览器后恢复 |
