# 词云环 · 系统架构文档

## 1. 系统架构图

```
┌────────────────────────────────────────────────────────────────────┐
│                   数据源 (Data Source)                               │
│  welive.exe export-session --readable → 聊天.md (Markdown)          │
│  (welive 工具位于 original_project/ 目录)                           │
└──────────────────────────────┬─────────────────────────────────────┘
                               │
                               ▼
┌────────────────────────────────────────────────────────────────────┐
│           Phase 1: 四通道词提取 (scripts/extract_words_v3.py)       │
│                                                                    │
│  ┌──────────┐  ┌──────────────┐  ┌──────────────┐  ┌───────────┐  │
│  │ 通道 A    │  │ 通道 B       │  │ 通道 C       │  │ 通道 D    │  │
│  │ jieba    │  │ n-gram+PMI  │  │ pypinyin     │  │ 纯拉丁词  │  │
│  │ 分词     │  │ +邻接熵      │  │ 谐音梗检测   │  │ PMI+熵    │  │
│  │ ×0.6降权 │  │ 新词+15加分  │  │ 相似度≥75%   │  │ 黑名单过滤 │  │
│  └────┬─────┘  └──────┬───────┘  └──────┬───────┘  └─────┬─────┘  │
│       └───────────────┴─────────────────┴─────────────────┘        │
│                               │                                    │
│       配额合并 → 加权评分 → Top 1500 候选词                          │
│       (jieba 55% / ngram 35% / latin 8% / pinyin 2%)              │
└──────────────────────────────┬─────────────────────────────────────┘
                               │ output_v3/candidate_words.json (1500词)
                               ▼
┌────────────────────────────────────────────────────────────────────┐
│        Phase 2: 人工筛选 (scripts/visual_filter.html)              │
│                                                                    │
│  键盘操作: A=保留  R=删除  S=跳过  Ctrl+Z=撤销                     │
│  自动保存到 localStorage，支持进度恢复                               │
│  产出: keep.txt + remove.txt → 放入 output_v3/                      │
└──────────────────────────────┬─────────────────────────────────────┘
                               │ keep.txt / remove.txt
                               ▼
┌────────────────────────────────────────────────────────────────────┐
│        Phase 3: 合并归类 (scripts/merge_categorize.py)              │
│                                                                    │
│  keep.txt + skip_words.txt → 15类语义归类 → merged_keep.md         │
│                                                                    │
│  类别: 🏫学校 📍地点 🍜食物 👤人名 🎮游戏 💻网络用语 🎵谐音梗     │
│        💕情感 🏠日常 👗服饰 🗣️虚词 🔤拉丁词 📋其他 ❓未分类      │
│                                                                    │
│  注意: merge_categorize.py 产出为初始模板                           │
│        → 用户必须手动编辑: 调整分类、增删词、改 [keep]/[skip]       │
└──────────────────────────────┬─────────────────────────────────────┘
                               │ output_v3/merged_keep.md
                               ▼
┌────────────────────────────────────────────────────────────────────┐
│        Phase 4: 环数据构建 (scripts/build_ring.py)                  │
│                                                                    │
│  解析 merged_keep.md → 交叉 candidate_words.json 获取权重           │
│  手动新增词 → 使用平均权重                                          │
│  权重 → power-curve 字号映射 (14~72px)                              │
│  产出: ring_words.json                                             │
└──────────────────────────────┬─────────────────────────────────────┘
                               │ output_v3/ring_words.json
                               ▼
┌────────────────────────────────────────────────────────────────────┐
│        Phase 5: 词云环可视化 (scripts/word_ring.html)               │
│                                                                    │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  放置引擎          │  渲染引擎          │  交互引擎          │   │
│  │  • Fisher-Yates    │  • 4副本无缝       │  • 拖拽/滚轮/键盘  │   │
│  │  • 贪心最短行      │  • inline CSS      │  • 自动滚动         │   │
│  │  • +1px等宽补全    │  • translateY垂直  │  • 设置面板(⚙️)    │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                    │
│  输出: 浏览器中无限水平滚动的横版词云环 (自包含单文件)              │
└────────────────────────────────────────────────────────────────────┘
```

## 2. 数据流

```
聊天.md 
  │
  │ extract_words_v3.py
  │   正则清洗: 去时间戳/ID/URL/纯数字/系统消息/撤回消息
  │   分句 → 4通道并行提取 → 加权合并 → 配额排序
  ▼
candidate_words.json    [{word, count, pmi, left_entropy, right_entropy,
                          source, reason, homophones, score} × 1500]
  │
  │ visual_filter.html
  │   人工 A保留/R删除/S跳过 (键盘操作)
  ▼
keep.txt + remove.txt + skip_words.json
  │   (visual_filter.html 直接导出所有文件)
  │
  │ merge_categorize.py
  │   读取 keep.txt + skip.txt → 15类语义归类 → 产出初始 merged_keep.md 模板
  │
  │ 用户手动编辑 merged_keep.md
  │   * 词 \[keep] / * 词 \[skip]  ← 最终决定
  │   ## 分类标题                    ← 15类语义归类
  ▼
merged_keep.md           # ★ 用户编辑的最终词表
  │
  │ build_ring.py
  │   匹配 candidate_words.json 元数据 → 计算权重/字号
  │   手动新增词用平均权重
  ▼
ring_words.json          [{word, tag, count, weight, fontSize,
                           pmi, left_entropy, right_entropy,
                           source, reason, homophone} × ]
  │
  │ word_ring.html
  │   EMBEDDED_WORDS (内嵌词) 或 fetch ring_words.json
  │   Fisher-Yates随机打乱 → 贪心最短行放置 → +1px等宽补全 → 4副本渲染
  ▼
浏览器词云环 (无线水平滚动、自包含单HTML文件)
```

## 3. 核心算法

### 3.1 通道 A: jieba 分词

使用 jieba 精确模式对清洗后的文本分词，统计词频。jieba 返回大量通用高频词（如"我们"、"这个"），因此应用 ×0.6 降权系数，为新词发现腾出配额空间。

### 3.2 通道 B: n-gram + PMI + 邻接熵

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

### 3.3 通道 C: 拼音谐音梗检测

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

### 3.4 通道 D: 纯拉丁词检测

检测拼音首字母缩写（如 `yyds` = 永远的神）。

```
算法流程:
1. 正则提取纯拉丁词: /[a-zA-Z]{2,5}/
2. PMI + 邻接熵 过滤 (阈值比中文更严格)
3. 80+ 黑名单过滤常见英文碎片 (er, re, ing, the, and, com, jpg...)
4. 最终评分 ≥ 25 → 进入候选
```

**参数**: `LATIN_WORD_MIN_LEN=2, MAX_LEN=5, MIN_COUNT=3, PMI_MIN=4.0, ENTROPY_MIN=0.8, QUOTA=50`

### 3.5 通道合并评分与配额

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

### 3.6 权重计算 (build_ring.py)

```python
# 词云环权重 (比评分更简单，更注重视觉分布)
weight = log2(count + 1) * 5 + PMI * 3 + (left_entropy + right_entropy) * 2

# 手动新增词 → 使用所有已知词的平均权重
avg_weight = sum(found_weights) / len(found_weights)

# Power-curve 字号映射 (压缩极端值)
ratio = (weight - minWeight) / (maxWeight - minWeight)
ratio = ratio ^ 0.55                              # POWER_CURVE < 1 → 压缩高端
fontSize = 14 + ratio * (72 - 14)                 # 14~72px
```

## 4. 词云环引擎设计

### 4.1 放置算法 (computePlacements)

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

### 4.2 渲染引擎

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

### 4.3 交互引擎

| 交互 | 实现 |
|------|------|
| 拖拽滚动 | mousedown/touchstart → 记录起始位置 → mousemove/touchmove 更新 currentX |
| 滚轮 | wheel 事件 → deltaY → currentX |
| 键盘 | ← → 方向键 |
| 自动滚动 | 空格键切换 / 设置面板默认开启 |
| 设置面板 | ⚙️ 按钮 → 模态框 (行数/速度/颜色) |

## 5. 文件格式规格

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

### merged_keep.md

```markdown
## 🍜 食物/饮品 (35词)

* 奶茶 \[keep]
* 咖啡 \[keep]
* 橙c \[skip]

## 👤 人名/称呼 (28词)

* 草莓酱 \[keep]
```

**规则**:
- `## 类别名` → 分类标题（渲染时跳过）
- `* 词 \[keep]` → 最终展示的词
- `* 词 \[skip]` → 存在但不特别关注（正常渲染）
- `* 词`（无后缀）→ 默认 `keep`

## 6. 关键技术决策

| 决策 | 理由 |
|------|------|
| jieba 降权 ×0.6 | jieba 返回大量通用高频词，需为新词腾出配额空间 |
| 非词典词 +15 加分 | 聊天中真正有趣的词往往是词典外的网络新词 |
| 删除中英混用 bonus | v2 的中英混用加分引入过多噪声（如"K歌"、"A股"） |
| 拉丁词黑名单 80+ | 通道 D 不加黑名单会返回大量英文碎片 |
| 纯拉丁词独立通道 | v2 中拉丁词混入中文评分导致评分体系混乱 |
| power-curve 字号 (^0.55) | 避免少数超高频词独占视觉，使分布更均匀 |
| 贪心最短行放置 | 自然均衡，无需碰撞检测，比随机放置+碰撞更简洁可靠 |
| +1px 等宽补全 | 强制每行宽度精确相等，确保环首尾完美相接 |
| 4 副本渲染 | 只需维护一个滚动偏移，边界自动循环 |
| Fisher-Yates 随机 | 解耦视觉呈现与权重，每次重排都不同 |
| 随机颜色 | 解耦颜色与词来源/权重，视觉效果更丰富自然 |
| word_ring.html 自包含 | 内嵌词数据 → 手机浏览器直接打开可用，无需服务器 |


