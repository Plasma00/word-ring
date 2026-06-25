# 词云环 (Word Ring)

> 从微信聊天记录提取高频词/梗/谐音词 → 无限水平滚动词云环可视化

```
聊天记录.md → Web 三阶段管道 → 词云环演示 HTML
```

## 快速开始

### 安装

```bash
pip install flask jieba pypinyin
```

### 启动

```bash
python app.py
# → 浏览器打开 http://localhost:5000
```

或双击 `启动.bat`（Windows）自动启动服务器并打开浏览器。

### 三阶段管道

| 阶段 | 操作 | 说明 |
|------|------|------|
| **① 提取** | 上传聊天记录 `.md` 文件 | SSE 流式显示进度，自动提取候选词 |
| **② 筛选** | A 保留 / R 删除 / S 跳过 | 逐个审核候选词，支持撤销和进度恢复 |
| **③ 环** | 查看词云环 | 无限水平滚动，拖拽/滚轮/键盘交互 |

Stage 3 可下载 **自包含演示 HTML**（双击即可打开，无需服务器）。

## 项目结构

```
project-root/
├── app.py                      # Flask 后端（三阶段管道 API）
├── frontend/
│   └── app.html                # 前端 SPA（提取→筛选→环 三合一）
├── scripts/
│   ├── extract_words.py        # 四通道词提取引擎
│   ├── build_ring.py           # CLI 环数据构建（备选）
│   ├── word_ring.html          # 独立词云环演示（含示例数据）
│   ├── stopwords.txt           # 中文停用词表
│   └── requirements.txt        # Python 依赖
├── 启动.bat                    # Windows 一键启动
├── README.md                   # 本文件
├── ARCHITECTURE.md             # 系统架构 + 算法详解
├── DEV_DOCUMENT.md             # 工程文档 + 环境配置 + 故障排除
└── .gitignore
```

## API 端点

| 方法 | 端点 | 说明 |
|------|------|------|
| `GET` | `/` | 三阶段前端页面 |
| `POST` | `/api/extract` | 上传聊天文件 + 参数 → SSE 流式提取 |
| `GET` | `/api/session/<id>/candidates` | 获取提取完成的候选词 |
| `POST` | `/api/build-ring` | 提交保留词 → 返回环数据 |
| `GET` | `/api/health` | 健康检查 |

## CLI 备选流程

```bash
# 1. 提取
python scripts/extract_words.py Files/chat_history.md --output-dir Files

# 2. 构建环
python scripts/build_ring.py

# 3. 浏览器打开 scripts/word_ring.html
```

## 核心算法

### 四通道提取

| 通道 | 方法 | 目标 |
|------|------|------|
| **A** | jieba 分词 ×0.6 降权 | 常规高频词 |
| **B** | n-gram + PMI + 邻接熵 +15 加分 | 网络新词发现 |
| **C** | pypinyin 拼音空间映射 相似度≥75% | 谐音梗检测 |
| **D** | 纯拉丁词 PMI≥4.0 + 黑名单过滤 | 拼音缩写 |

### 权重公式

```
weight = log2(count+1)×5 + PMI×3 + (左熵+右熵)×2
fontSize = 14 + ratio^0.55 × 58     (power-curve, 14~72px)
```

### 词云环算法

1. **Fisher-Yates 随机打乱** — 解耦视觉与权重
2. **贪心最短行放置** — 每次选最短行放置，自然均衡
3. **+1px 等宽补全** — 所有行精确等宽，首尾无缝
4. **4 副本渲染** — 无限水平循环滚动

## 可视化交互

| 操作 | 方式 |
|------|------|
| 水平滚动 | 拖拽 / 滚轮 / ← → 键 |
| 自动滚动 | 空格键切换（默认开启） |
| 设置面板 | ⚙️ 按钮 → 行数(3~16) / 速度(5档) / 颜色(20色多选) |

## 许可

个人项目，由 Claude Code 完成。
