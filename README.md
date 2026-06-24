# 词云环 (Word Ring)

> 从和亲友的微信聊天记录中提取高频词/梗/谐音词，在无限水平滚动的词云环上可视化。

```
聊天.md → extract_words_v3.py → candidate_words.json → visual_filter.html（人工筛选）
→ merge_categorize.py → merged_keep.md → build_ring.py → ring_words.json
→ word_ring.html（浏览器直接打开）
```

## 项目结构

```
project-root/
├── README.md                  # ← 本文件 (项目入口，人类 + AI Agent 通用)
├── ARCHITECTURE.md            # 系统架构 + 算法详解
├── DEV_DOCUMENT.md            # 工程文档 + 环境配置 + 故障排除
├── .gitignore
│
├── scripts/                   # ★ 所有脚本 (7个)
│   ├── extract_words_v3.py    #   四通道词提取 (核心，1400+行)
│   ├── visual_filter.html     #   人工筛选器 (A保留/R删除/S跳过) + 导出skip_words.json
│   ├── merge_categorize.py    #   合并归类 (15语义类别)
│   ├── build_ring.py          #   环数据构建 (merged_keep → ring_words)
│   ├── word_ring.html         # ★ 词云环可视化 (自包含单文件)
│   ├── stopwords.txt          #   中文停用词表
│   └── requirements.txt       #   jieba + pypinyin
│
├── output_v3/                 # ★ 当前版本数据
│   ├── candidate_words.json   #   1500候选词 (含PMI/熵/谐音元数据)
│   ├── candidate_words.txt    #   候选词预览
│   ├── keep.txt / remove.txt  #   人工筛选结果
│   ├── skip_words.json/.txt   #   跳过词 (visual_filter.html 直接导出)
│   ├── merged_keep.md         # ★ 最终词表 (用户手动编辑)
│   └── ring_words.json        # ★ 环输入数据 (build_ring.py 产出)
│
├── original_project/          # 原始下载项目 (参考保留，gitignored)
└── temp/                      # 用户私有数据 (gitignored)
```

## 谁需要读什么

| 角色 | 文件 |
|------|------|
| **首次接触项目的 AI Agent** | README.md → ARCHITECTURE.md |
| **人类开发者** | README.md → DEV_DOCUMENT.md |
| **要修改提取逻辑** | `scripts/extract_words_v3.py` + DEV_DOCUMENT.md §2 |
| **要重新筛选词** | `scripts/visual_filter.html`（浏览器打开，自包含） |
| **要调整词云环样式** | `scripts/word_ring.html`（自包含单文件） |
| **要更新词库** | 编辑 `output_v3/merged_keep.md` → 运行 `build_ring.py` |
| **要换聊天数据源** | DEV_DOCUMENT.md §4.4 |

## 快速开始

### 环境

```bash
pip install jieba pypinyin
```

### 完整流程

```bash
# 1. 提取候选词 (≈8-10分钟)
python scripts/extract_words_v3.py temp/聊天.md --output-dir output_v3

# 2. 人工筛选 → 浏览器打开 scripts/visual_filter.html
#    拖入 candidate_words.json → A保留/R删除/S跳过 → 导出全部文件到 output_v3/

# 3. 合并归类
python scripts/merge_categorize.py output_v3

# 4. 手动编辑 output_v3/merged_keep.md（调整分类、增删词、改 [keep]/[skip]）

# 5. 构建环数据
python scripts/build_ring.py

# 6. 浏览器直接打开 scripts/word_ring.html（自包含，无需服务器）
```

## 核心概念

### 四通道提取

| 通道 | 方法 | 目标 | 评分 |
|------|------|------|------|
| **A** | jieba 分词 ×0.6降权 | 常规高频词 | 降权腾空间 |
| **B** | n-gram + PMI + 邻接熵 +15加分 | 网络新词发现 | 非词典词提权 |
| **C** | pypinyin 拼音空间映射 相似度≥75% | 谐音梗检测 | 拼音编辑距离 |
| **D** | 纯拉丁词 PMI≥4.0 + 黑名单过滤 | 拼音缩写 | 80+碎片过滤 |

### 权重公式

```
weight = log2(count+1)×5 + PMI×3 + (左熵+右熵)×2
fontSize = 14 + ratio^0.55 × 58     (power-curve, 14~72px)
```

### 词云环算法

1. **Fisher-Yates 随机打乱** — 解耦视觉与权重
2. **贪心最短行放置** — 每次选当前最短的行放词，自然均衡
3. **+1px 等宽补全** — 强制所有行精确等宽，首尾无缝
4. **4 副本渲染** — 无限水平循环滚动

## 可视化操作

| 操作 | 方式 |
|------|------|
| 水平滚动 | 拖拽 / 滚轮 / ← → 键 |
| 自动滚动 | 空格键 或 设置面板（默认开启） |
| 设置面板 | ⚙️ 按钮 → 行数(3~16) / 速度(5档) / 颜色(20色多选) |
| 重排 | 设置面板 → 确认 |

## 数据来源

微信聊天记录由 [weflow](https://github.com/hicccc77/WeFlow) 导出为 Markdown。


## 更新词库

```bash
# 编辑 output_v3/merged_keep.md 后
python scripts/build_ring.py
# 刷新浏览器即可
```

`word_ring.html` 内嵌了词语数据（`EMBEDDED_WORDS`），如词库有更新需重新嵌入（见 DEV_DOCUMENT.md §4.6）。

## 许可

个人项目。
