# 词云环 · 工程文档

面向开发者和 AI Agent 的完整工程参考。如果你是 AI Agent 第一次接触本项目，先读 `README.md`。

## 1. 环境要求

| 依赖 | 版本 | 用途 | 安装 |
|------|------|------|------|
| Python | ≥3.10 | 提取脚本 + Flask 服务器 | [python.org](https://python.org) |
| Flask | ≥3.0.0 | Web 服务器 | `pip install flask` |
| jieba | ≥0.42.1 | 通道 A 分词 | `pip install jieba` |
| pypinyin | ≥0.49.0 | 通道 C 拼音谐音检测 | `pip install pypinyin` |
| 浏览器 | Chrome/Firefox/Safari 最新版 | 三阶段 Web 界面 | 无需安装 |

```bash
pip install flask jieba pypinyin
```

## 2. 聊天记录格式要求

`extract_words.py` 要求输入文件为 welive 工具导出的 Markdown 格式（使用 `--readable` 参数）。

### 格式规范

```
## YYYY-MM-DD

- `HH:MM:SS` **wxid_xxxxxxxxxxxxxx**: 消息内容
```

| 元素 | 格式 | 示例 |
|------|------|------|
| 日期行 | `## YYYY-MM-DD` | `## 2024-01-15` |
| 消息行 | `` - `HH:MM:SS` **wxid_xxx**: 内容 `` | `` - `09:15:30` **wxid_alice_1988**: 早上好！ `` |
| 发送者ID | `wxid_` 开头 | `wxid_alice_1988` |

### 关键规则

1. **日期行**：以 `## ` 开头，后跟 `YYYY-MM-DD` 格式的日期。解析器将此行之后的消息都归到该日期下，直到遇到下一个日期行。
2. **消息行**：以 `` - ` `` 开头，包含时间戳 `` HH:MM:SS ``、发送者（`**wxid_xxx**`）和消息内容，三者以冒号+空格分隔。
3. **空行**：自动跳过。
4. **其他行**：不符合消息格式的行会被忽略。

### 自动清洗

脚本会自动过滤以下内容（无需手动处理）：
- 系统消息：`[图片]` `[链接]` `[小程序]` `[文件]` `[语音]` `[视频]` `[表情]` 等
- 撤回消息：XML撤回 和 纯文本撤回
- URL 链接和裸域名
- 纯数字/纯标点/纯 emoji 消息

## 3. Web 服务器 (app.py)

### 3.1 启动

```bash
python app.py
# → http://localhost:5000
```

或双击 `启动.bat`（Windows，自动启动服务器 + 打开浏览器）。

服务器配置：`host=0.0.0.0, port=5000, debug=True, threaded=True`

### 3.2 API 端点

#### GET / — 前端页面

返回 `frontend/app.html`（三阶段 SPA），强制 `mimetype='text/html'`（因 `static_folder=None` 禁用了 MIME 自动检测）。

#### GET /api/health — 健康检查

```json
{"status": "ok", "version": "4.0"}
```

#### POST /api/extract — SSE 流式提取

**请求**: `multipart/form-data`
- `file`: 聊天记录 `.md` 文件
- `params`: JSON 字符串（可选，包含提取参数）

**响应**: `text/event-stream`

```
data: {"type":"progress","text":"..."}
data: {"type":"complete","count":1500,"session_id":"abc123"}
data: {"type":"error","message":"..."}
```

**参数默认值**:

```python
max_candidates = 1500
pmi_min = 3.0
entropy_min = 1.0
ngram_min = 2
ngram_max = 5
jieba_multiplier = 0.6
not_in_dict_bonus = 15
latin_quota = 50
no_pinyin = False
sender = None  # 不传则提取所有发送者
```

**实现细节**:
- 使用 `subprocess.Popen` 调用 `scripts/extract_words.py`
- `PYTHONUNBUFFERED=1` 强制无缓冲输出
- 逐行读取 stdout → yield SSE 事件
- 完成后读取 `candidate_words.json` 存入 session

#### GET /api/session/\<id\>/candidates — 获取候选词

**响应** (200):
```json
{
  "session_id": "abc123",
  "count": 1500,
  "candidates": [{...}, ...]
}
```

**响应** (202): `{"error": "提取仍在进行中"}` — 前端自动重试（最多 3 次，指数退避）

**响应** (404): `{"error": "Session 不存在或已过期"}`

**响应** (500): `{"error": "提取失败"}`

#### POST /api/build-ring — 构建环数据

**请求**:
```json
{"keep_words": [{"word": "...", "count": 42, "pmi": 5.2, ...}, ...]}
```

**响应**:
```json
{
  "ring_words": [{"word": "...", "tag": "keep", "count": 42, "weight": 38.5, "fontSize": 48.0, ...}],
  "stats": {"total": 200, "weight_min": 5.0, "weight_max": 85.0, "weight_avg": 32.5, "font_min": 14.0, "font_max": 72.0, "source_dist": {"jieba": 120, "ngram": 60, ...}}
}
```

**算法**: 内联权重计算（与 `build_ring.py` 相同），无需调用外部脚本。

### 3.3 Session 管理

```python
sessions: dict[str, dict] = {}
sessions_lock = threading.Lock()
```

Session 结构:
```python
{
    'status': 'running' | 'done' | 'error',
    'dir': str,           # session 目录路径
    'chat_file': str,     # 上传的聊天文件路径
    'count': int,         # 候选词数量
    'candidates': list,   # 候选词数组
}
```

- 存储于内存，服务器重启后丢失
- Session ID: `uuid.uuid4().hex[:12]`
- 文件存储: `Files/uploads/<session_id>/`

## 4. 前端 (frontend/app.html)

### 4.1 架构

单文件 SPA，三阶段切换。状态集中管理在 `STATE` 对象中。

### 4.2 Stage 1: 提取

- 文件上传 + 参数表单
- SSE 流式读取 (`resp.body.getReader()` + `ReadableStream`)
- 实时控制台日志
- 完成后自动拉取候选词（带重试逻辑）

### 4.3 Stage 2: 筛选

- 逐个展示候选词（含来源/频次/PMI/熵元数据）
- 键盘操作: A=保留, R=删除, S=跳过, Ctrl+Z=撤销
- 跳过词自动进入第二轮回顾
- 自定义词输入（逗号/换行分隔）
- 进度自动保存到 `localStorage`（键: `wr_pipeline_stage2`）
- 恢复进度时检测候选词总数是否匹配（不匹配则丢弃旧进度）

### 4.4 Stage 3: 环

- 贪心最短行放置 + 4 副本渲染
- 拖拽/滚轮/键盘/触摸交互
- 设置面板（行数/速度/颜色）
- 下载 `ring_words.json`
- **下载自包含演示 HTML** (`downloadStandaloneHTML()`): 生成完整单文件 HTML，含内嵌数据 + CSS + JS 引擎，双击即可演示

### 4.5 关键技术点

- **SSE 解析**: 手动处理 UTF-8 分片 + 缓冲区，兼容不完整帧
- **重试逻辑**: `fetchCandidates` 对 202 响应最多重试 3 次，指数退避
- **兜底拉取**: SSE 流结束后若 candidates 仍为空，自动调用 `fetchCandidates`
- **模板字符串安全**: `<\/script>` 转义防止 HTML 解析器提前闭合

## 5. 脚本详解

### 5.1 extract_words.py — 四通道词提取

**文件**: `scripts/extract_words.py` (~1400行)
**输入**: welive 导出的 Markdown 聊天记录
**输出**:
- `candidate_words.json` — 1500 候选词 (含 PMI/熵/谐音/拉丁词元数据)
- `candidate_words.txt` — 文本预览 (含梗解释)
- `all_jieba_words.txt` — jieba 分词全量结果 (调试用)

**用法**:
```bash
python scripts/extract_words.py <聊天文件.md> [--output-dir <输出目录>] [参数...]
```

**可调参数**:

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

### 5.2 build_ring.py — CLI 环数据构建

**文件**: `scripts/build_ring.py` (203行)

```bash
python scripts/build_ring.py
```

**输入**: `Files/merged_keep.md` + `Files/candidate_words.json`
**输出**: `Files/ring_words.json`

这是 CLI 备选方案。Web 管道中权重计算已内联到 `app.py` 的 `/api/build-ring` 端点。

### 5.3 word_ring.html — 独立词云环演示

**文件**: `scripts/word_ring.html` (~600行, 自包含单文件)

内嵌示例词数据（`EMBEDDED_WORDS`），双击浏览器直接打开即可看到效果。无需服务器。

可通过拖拽 `ring_words.json` 加载自定义数据。作为 Web 管道 Stage 3 和自包含导出功能的参考实现。

## 6. 扩展指南

### 6.1 调整提取参数

通过 Web 界面（Stage 1 的参数表单）调整，或直接修改 `extract_words.py` 中的默认值。

### 6.2 修改颜色调色盘

编辑 `frontend/app.html` 中的 `ALL_COLORS` 数组：

```javascript
const ALL_COLORS = [
  '#e94560', '#f5a623', '#7cd67c', // ... 最多 20 种
];
```

同步修改 `downloadStandaloneHTML()` 函数中的内嵌颜色数组。

### 6.3 更换聊天数据源

```bash
# 1. 用 welive 导出新聊天记录
# 2. 通过 Web 界面上传新文件，或:
python scripts/extract_words.py Files/新聊天.md --output-dir Files
```

### 6.4 重新嵌入词库到 word_ring.html

当需要更新 `word_ring.html` 中的示例数据时:

```bash
python -c "
import json, re
with open('scripts/word_ring.html', 'r', encoding='utf-8') as f:
    html = f.read()
with open('Files/ring_words.json', 'r', encoding='utf-8') as f:
    words = json.load(f)
new_data = 'const EMBEDDED_WORDS = ' + json.dumps(words, ensure_ascii=False, indent=2) + ';'
html = re.sub(r'const EMBEDDED_WORDS = \[.*?\];', new_data, html, flags=re.DOTALL)
with open('scripts/word_ring.html', 'w', encoding='utf-8') as f:
    f.write(html)
print('Done')
"
```

## 7. 故障排除

### 7.1 Flask 启动失败

```bash
pip install flask
python -c "import flask; print(flask.__version__)"
```

### 7.2 jieba 分词无输出

```bash
python -c "import jieba; print(jieba.__version__)"
python -c "import jieba; print(list(jieba.cut('我爱北京天安门')))"
```

### 7.3 pypinyin 警告

```
⚠️ 未安装 pypinyin，谐音梗检测将降级为拼音近似
```

```bash
pip install pypinyin
```

### 7.4 页面显示 HTML 代码

确保通过 `http://localhost:5000` 访问（非 `file://` 协议）。`app.py` 已配置 `mimetype='text/html'` 强制正确 MIME 类型。

### 7.5 Windows 终端编码问题

所有 Python 脚本已包含 Windows UTF-8 修复:

```python
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
```

如果仍有乱码:
```powershell
$env:PYTHONIOENCODING = "utf-8"
```

### 7.6 SSE 提取完成后页面不更新

前端 `fetchCandidates` 内置重试逻辑（最多 3 次，指数退避）。如果仍失败，检查浏览器控制台错误日志。后端 `get_candidates` 端点会打印 DEBUG 日志到服务器终端。

### 7.7 手机浏览器打开后无反应

`word_ring.html` 是自包含文件，确保文件完整。如果使用 Web 管道 Stage 3，需确保 Flask 服务器可被手机访问（同一局域网）。

## 8. 版本历史

| 版本 | 主要变化 |
|------|---------|
| v1 (原始) | 双通道 (jieba + ngram)，输出到微信小程序 3D 球面 |
| v2 (原始) | 加入拼音谐音检测 (通道 C)，改进评分体系 |
| v3 | 深度清洗、jieba 降权、非词典词提权、纯拉丁词通道 D、配额合并 |
| v3.1 | 人工筛选器 (visual_filter.html)、跳过词处理 |
| v3.2 | 词云环可视化、碰撞检测、无缝循环 |
| v3.3 | 贪心最短行放置、+1px等宽补全、随机颜色、设置面板 |
| v3.4 | 项目结构整理 |
| v4.0 | Flask Web 三阶段管道: SSE 流式提取 + 在线筛选 + 环渲染 + 自包含 HTML 导出 |
| v4.1 | SSE 完成状态同步修复、自包含导出 `<\/script>` 转义、项目清理（删除旧 visual_filter.html / merge_categorize.py） |

## 9. 完整文件清单

```
project-root/
├── app.py                      # Flask 后端
├── 启动.bat                    # Windows 一键启动
├── frontend/
│   └── app.html                # 前端 SPA (三阶段)
├── scripts/
│   ├── extract_words.py        # 四通道词提取引擎
│   ├── build_ring.py           # CLI 环数据构建
│   ├── word_ring.html          # 独立词云环演示 (含示例数据)
│   ├── stopwords.txt           # 中文停用词表
│   └── requirements.txt        # Python 依赖 (flask + jieba + pypinyin)
├── Files/                      # 用户数据 (gitignored)
│   └── uploads/                # Session 临时文件
├── README.md                   # 项目入口
├── ARCHITECTURE.md             # 系统架构
├── DEV_DOCUMENT.md             # ← 本文件
└── .gitignore
```
