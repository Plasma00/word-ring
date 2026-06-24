# 词云环 · 工程文档

面向开发者和 AI Agent 的完整工程参考。如果你是 AI Agent 第一次接触本项目，先读 `README.md`。

## 1. 环境要求

| 依赖 | 版本 | 用途 | 安装 |
|------|------|------|------|
| Python | ≥3.10 | 所有提取/构建脚本 | [python.org](https://python.org) |
| jieba | ≥0.42.1 | 通道 A 分词 | `pip install jieba` |
| pypinyin | ≥0.49.0 | 通道 C 拼音谐音检测 | `pip install pypinyin` |
| 浏览器 | Chrome/Firefox/Safari 最新版 | 筛选器 + 词云环可视化 | 无需安装 |

```bash
pip install jieba pypinyin
```

## 2. 脚本详解

### 2.1 extract_words_v3.py — 四通道词提取

**文件**: `scripts/extract_words_v3.py` (~1400行)  
**输入**: welive 导出的 Markdown 聊天记录  
**输出**:
- `output_v3/candidate_words.json` — 1500 候选词 (含 PMI/熵/谐音/拉丁词元数据)
- `output_v3/candidate_words.txt` — 文本预览 (含梗解释)
- `output_v3/all_jieba_words.txt` — jieba 分词全量结果 (调试用，不提交 Git)

**用法**:
```bash
python scripts/extract_words_v3.py <聊天文件.md> [--output-dir <输出目录>]
```

**可调参数** (脚本内 `# 配置` 注释块):

```python
# —— 基础 ——
NGRAM_MIN = 2              # 最短 n-gram
NGRAM_MAX = 5              # 最长 n-gram
MIN_COUNT_JIEBA = 2        # jieba 词最低频次
MIN_COUNT_NGRAM = 3        # n-gram 词最低频次
MAX_CANDIDATES = 1500      # 候选词总量上限

# —— 评分 ——
JIEBA_SCORE_MULTIPLIER = 0.6   # jieba 降权系数
NOT_IN_DICT_BONUS = 15         # 非词典词加分

# —— PMI / 熵 阈值 ——
PMI_MIN = 3.0             # PMI 硬阈值 (低于直接淘汰)
ENTROPY_MIN = 1.0         # 邻接熵硬阈值
PMI_SOFT_MIN = 1.5        # PMI 软阈值 (高频词 count≥20 放宽)
ENTROPY_SOFT_MIN = 0.6    # 熵软阈值
HIGH_FREQ_THRESHOLD = 20  # "高频" 门槛

# —— 拼音谐音检测 ——
PINYIN_SIMILARITY_MIN = 0.75  # 拼音相似度阈值
MAX_PINYIN_EDIT_DIST = 1      # 拼音编辑距离上限

# —— 拉丁词 ——
LATIN_WORD_MIN_LEN = 2    # 拉丁词最短
LATIN_WORD_MAX_LEN = 5    # 拉丁词最长
LATIN_WORD_MIN_COUNT = 3  # 拉丁词最低频次
LATIN_WORD_PMI_MIN = 4.0  # 拉丁词 PMI 阈值 (比中文严)
LATIN_WORD_ENTROPY_MIN = 0.8  # 拉丁词熵阈值
LATIN_WORD_QUOTA = 50     # 拉丁词配额
```

**性能参考** (232k 行聊天记录, Intel i7):
| 阶段 | 耗时 |
|------|------|
| 正则清洗 | ~2 秒 |
| jieba 分词 | ~15 秒 |
| n-gram + PMI/熵 | ~5 分钟 |
| 拼音检测 | ~2 分钟 |
| 合并排序 | ~1 秒 |
| **总计** | **~8-10 分钟** |

### 2.2 visual_filter.html — 人工筛选器

**文件**: `scripts/visual_filter.html` (21KB, 单文件)  
**用法**: 浏览器打开 → 拖入 `candidate_words.json`

| 操作 | 按键 |
|------|------|
| 保留 | A |
| 删除 | R |
| 跳过 | S |
| 撤销 | Ctrl+Z |

**特性**:
- 自动保存进度到 localStorage (键: `visual_filter_progress_v2`)
- 第一轮完成后自动进入"跳过词回顾"模式
- 导出: 下载 `keep.txt` / `remove.txt` / `skip.txt`
- 独立于服务器运行 (file:// 协议可用)

### 2.3 merge_categorize.py — 合并归类

**文件**: `scripts/merge_categorize.py` (289行)

```bash
python scripts/merge_categorize.py [output_v3]
```

将 `keep.txt` + `skip_words.txt` 合并，按 15 个语义类别归类:

```
🏫 学校/教育    📍 地点/城市    🍜 食物/饮品
👤 人名/称呼    🎮 游戏/娱乐    💻 网络用语/梗
🎵 谐音梗       💕 情感/关系    🏠 日常生活
👗 服饰/物品    🗣️ 日常用语/虚词  🔤 英文缩写/拉丁词
📋 其他中文词   ❓ 未分类
```

> ⚠️ 此脚本产出 `merged_keep.md` 仅为**初始模板**。用户必须手动编辑该文件：调整分类、增删词条、修改 `[keep]` / `[skip]` 标记。

### 2.4 build_ring.py — 环数据构建

**文件**: `scripts/build_ring.py` (203行)

```bash
python scripts/build_ring.py
```

**输入**: `output_v3/merged_keep.md` + `output_v3/candidate_words.json`  
**输出**: `output_v3/ring_words.json`

**权重逻辑**:
1. 词在 `candidate_words.json` 中找到 → 使用实际 count/PMI/熵计算权重
2. 手动新增的词 → 使用所有已知词的平均权重
3. 权重 → 字号: power-curve 映射 (14~72px, POWER_CURVE=0.55)

**可调参数**:
```python
FONT_MIN = 14
FONT_MAX = 72
POWER_CURVE = 0.55   # <1 压缩极端值，避免少数高频词垄断视觉
```

### 2.5 word_ring.html — 词云环可视化

**文件**: `scripts/word_ring.html` (~600行, 97KB, 自包含单文件)

**数据加载优先级**:
1. 内嵌 `EMBEDDED_WORDS` (词已嵌入) → 直接渲染
2. HTTP 模式: `fetch('../output_v3/ring_words.json')`
3. 手动: 拖拽 JSON 到页面

**自包含特性**:
- 词数据已嵌入 HTML 中 (`const EMBEDDED_WORDS = [...]`)
- 手机浏览器直接打开即可运行，无需服务器
- 如果词库更新，需重新运行嵌入脚本

**设置面板参数**:
| 参数 | 范围 | 默认 |
|------|------|------|
| 行数 | 3 ~ 16 | 8 |
| 滚动速度 | 5 档 (最慢→最快) | 中速 |
| 颜色调色盘 | 20 色多选 | 全选 |

**恢复 tooltip 功能** (代码中已注释保留):
```javascript
// 取消注释以恢复悬停详情
// chip.addEventListener('mouseenter', (e) => showTooltip(e, w));
// chip.addEventListener('mouseleave', hideTooltip);
```

## 3. 合并词表格式 (merged_keep.md)

```markdown
# 合并保留词表


**规则**:
- `## 类别名 (N词)` → 分类标题 (渲染时跳过)
- `* 词 \[keep]` → 最终展示
- `* 词 \[skip]` → 存在但不特别关注 (正常渲染)
- `* 词`（无后缀）→ 默认 `keep`
- `# 标题` → 文件标题 (跳过)

## 4. 扩展指南

### 4.1 更新词库

编辑 `output_v3/merged_keep.md`，在对应分类下添加/删除词:

```markdown
* 新词 \[keep]
```

然后:
```bash
python scripts/build_ring.py
# 刷新浏览器
```

新增词自动使用平均权重。

### 4.2 调整权重

如果某个词的字体大小不合适:
1. 在 `candidate_words.json` 中找到该词，修改 `count` 值
2. 运行 `build_ring.py` 重新构建

### 4.3 修改颜色调色盘

编辑 `word_ring.html` 中的 `ALL_COLORS` 数组:

```javascript
const ALL_COLORS = [
  '#e94560', '#f5a623', '#7cd67c', // ... 最多 20 种
];
```

### 4.4 更换聊天数据源

```bash
# 1. 用 welive 导出新聊天记录 (welive 在 original_project/ )
original_project/welive export-session --session-id "wxid_xxx" \
  --out temp/新聊天.md --readable --parse-content

# 2. 重新提取 (输出到新目录)
python scripts/extract_words_v3.py temp/新聊天.md --output-dir output_v4

# 3. 后续步骤相同...
```

### 4.5 恢复 tooltip 交互

`word_ring.html` 中 `showTooltip` / `hideTooltip` 函数已完整保留在注释中。取消注释以下行:

```javascript
// chip.addEventListener('mouseenter', (e) => showTooltip(e, w));
// chip.addEventListener('mouseleave', hideTooltip);
```

同时恢复 `showTooltip` / `hideTooltip` 函数块，以及 CSS `.tooltip` 样式。

### 4.6 重新嵌入词库到 HTML

当 `ring_words.json` 更新后:

```bash
python -c "
import json
with open('scripts/word_ring.html', 'r', encoding='utf-8') as f:
    html = f.read()
with open('output_v3/ring_words.json', 'r', encoding='utf-8') as f:
    words = json.load(f)
import re
new_data = 'const EMBEDDED_WORDS = ' + json.dumps(words, ensure_ascii=False, indent=2) + ';'
html = re.sub(r'const EMBEDDED_WORDS = \[.*?\];', new_data, html, flags=re.DOTALL)
with open('scripts/word_ring.html', 'w', encoding='utf-8') as f:
    f.write(html)
print('Done')
"
```

## 5. 故障排除

### 5.1 jieba 分词无输出

```bash
python -c "import jieba; print(jieba.__version__)"
python -c "import jieba; print(list(jieba.cut('我爱北京天安门')))"
```

### 5.2 pypinyin 警告

```
⚠️ 未安装 pypinyin，谐音梗检测将降级为拼音近似
```

```bash
pip install pypinyin
```

不安装也可运行——通道 C 会使用简化的拼音近似算法。

### 5.3 词云环不显示

- 正常使用: 浏览器直接打开 `word_ring.html`，内嵌数据自动加载
- 如果使用 fetch 模式: 确保 HTTP 服务器在项目根目录运行 (`python -m http.server 8000`)
- 手动加载: 拖拽 `ring_words.json` 到页面

### 5.4 Windows 终端编码问题

所有 Python 脚本已包含 Windows UTF-8 修复:

```python
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
```

如果仍有乱码:
```powershell
$env:PYTHONIOENCODING = "utf-8"
```

### 5.5 设置面板调整后不生效

点击设置面板的"确认"按钮才会应用并重排。如果直接关闭面板 (点遮罩/取消)，设置会回滚到上次确认的值。

### 5.6 手机浏览器打开后无反应

确保 `word_ring.html` 文件完整 (约 97KB)，内嵌的 `EMBEDDED_WORDS` 数据在文件末尾。如果文件被截断或不完整，词云环无法渲染。

## 6. 版本历史

| 版本 | 主要变化 |
|------|---------|
| v1 (原始) | 双通道 (jieba + ngram)，输出到微信小程序 3D 球面 |
| v2 (原始) | 加入拼音谐音检测 (通道 C)，改进评分体系 |
| v3 | **本项目起点**: 深度清洗 (系统消息/撤回/URL)、jieba 降权、非词典词提权、删除中英混用 bonus、纯拉丁词通道 D、配额合并 |
| v3.1 | 人工筛选器 (visual_filter.html)、跳过词处理 |
| v3.2 | 词云环可视化 (替代 3D 球面)、碰撞检测、无缝循环 |
| v3.3 | 贪心最短行放置、+1px等宽补全、随机颜色、设置面板 |
| v3.4 | extract_skip.py 嵌入 visual_filter.html、original_project/ gitignored、CLAUDE.md 合并到 README.md、项目结构整理 |

## 7. 完整文件清单

```
scripts/                          # 核心脚本 (★ = 主要入口)
├── extract_words_v3.py           # ★ 四通道词提取 (核心管道)
├── visual_filter.html            # ★ 人工筛选器 (A/R/S键盘，直接导出skip_words.json)
├── merge_categorize.py           #   合并归类 (15类别)
├── build_ring.py                 #   环数据构建
├── word_ring.html                # ★ 词云环可视化 (自包含单文件)
├── stopwords.txt                 #   中文停用词表
└── requirements.txt              #   Python 依赖

output_v3/                        # 当前版本数据
├── candidate_words.json          #   1500候选词 (提取产出)
├── candidate_words.txt           #   候选词预览
├── keep.txt                      #   保留词 (人工筛选)
├── remove.txt                    #   删除词 (人工筛选)
├── skip_words.json               #   跳过词 (visual_filter.html 直接导出)
├── skip_words.txt                #   跳过词预览
├── merged_keep.md                # ★ 最终词表 (用户编辑)
└── ring_words.json               # ★ 环输入数据 (build_ring 产出)

temp/                             # 用户私有数据 (gitignored)
├── 聊天.md                       #   微信聊天记录
└── ...

项目根目录:
├── README.md                     # 项目入口 (人类 + AI Agent 通用)
├── ARCHITECTURE.md               # 系统架构
├── DEV_DOCUMENT.md               # ← 本文件
└── .gitignore
```
