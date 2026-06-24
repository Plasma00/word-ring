#!/usr/bin/env python3
"""
四通道聊天词频提取工具 v3（深度清洗 + 均衡评分 + 纯英文检测）
=============================================================
通道A: jieba 标准分词 → 常规高频词 (降权)
通道B: n-gram + PMI + 邻接熵 → 真·新词发现
通道C: 拼音空间映射 → 谐音梗检测 (如 "蚌埠住了" → "绷不住了")
通道D: 纯拉丁词 + PMI/熵 → 拼音首字母缩写检测 (如 "zxq", "yyds")

v3 核心改进:
  1. 深度清洗: 去时间戳/ID/URL/纯数字/系统消息([图片][链接]撤回等)
  2. 评分重构: jieba 词降权, 非词典词大幅升值, 删除中英混用 bonus
  3. 纯英文通道: PMI+邻接熵检测拼音首字母缩写人名/梗词
  4. 保留 PMI+熵+谐音梗全部 v2 能力

用法:
    python extract_words_v3.py <聊天文件.md> [--output-dir <输出目录>]

输出:
    candidate_words.json  - 候选词 JSON (含 pmi/entropy/pinyin/纯英文 元数据)
    candidate_words.txt   - 候选词文本预览 (含梗解释)
"""

import re
import sys
import os
import json
import math
import argparse
from collections import Counter, defaultdict
from pathlib import Path

# Windows 终端 UTF-8 + 无缓冲输出
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stdout.reconfigure(line_buffering=False)  # 关闭行缓冲
    # 用 PYTHONUNBUFFERED=1 环境变量彻底关闭输出缓冲
    if not os.environ.get('PYTHONUNBUFFERED'):
        os.environ['PYTHONUNBUFFERED'] = '1'

# ── 尝试导入依赖 ──────────────────────────────────────────
try:
    import jieba
except ImportError:
    print("错误: 需要安装 jieba")
    print("  pip install jieba")
    sys.exit(1)

try:
    from pypinyin import lazy_pinyin, Style
    HAS_PYPINYIN = True
except ImportError:
    print("⚠️  未安装 pypinyin，谐音梗检测将降级为拼音近似")
    print("   建议: pip install pypinyin")
    HAS_PYPINYIN = False


# ══════════════════════════════════════════════════════════════
# 配置
# ══════════════════════════════════════════════════════════════

NGRAM_MIN = 2
NGRAM_MAX = 5
MIN_COUNT_JIEBA = 2
MIN_COUNT_NGRAM = 3       # 提高到3，降低低频噪声
MAX_CANDIDATES = 1500

# ── v3 评分权重 ──────────────────────────────────────────
JIEBA_SCORE_MULTIPLIER = 0.6    # jieba 词评分系数 (<1.0 降权)
NOT_IN_DICT_BONUS = 15          # 非词典词加分 (v2 是 8, v3 大幅提高)
# 删除了 v2 的 mixed-script bonus

# ── 纯英文/拉丁词检测 (通道D) ─────────────────────────────
LATIN_WORD_MIN_LEN = 2          # 最短拉丁词长度
LATIN_WORD_MAX_LEN = 5          # 最长拉丁词长度 (首字母缩写通常 2-5)
LATIN_WORD_MIN_COUNT = 3        # 最低频次
LATIN_WORD_PMI_MIN = 4.0        # PMI 阈值 (比中文严格，字母组合随机性更低)
LATIN_WORD_ENTROPY_MIN = 0.8    # 邻接熵阈值
LATIN_WORD_QUOTA = 50           # 纯英文词配额

# ── 纯拉丁词黑名单 (常见英文词碎片，不是拼音缩写) ──────────
LATIN_FRAGMENT_BLOCKLIST = {
    # 英文常见功能词/词缀碎片
    'er', 're', 'pre', 'ing', 'ed', 'ly', 'es', 'est',
    'un', 'in', 'im', 'dis', 'over', 'under', 'out',
    'th', 'sh', 'ch', 'wh', 'gh', 'ph',
    've', 'll', 're', 'nt', 'st', 'nd', 'rd',
    # 短代词/介词/连词
    'i', 'me', 'my', 'we', 'us', 'he', 'she', 'it', 'they',
    'am', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
    'do', 'does', 'did', 'have', 'has', 'had',
    'can', 'will', 'would', 'could', 'should', 'may', 'might',
    'to', 'of', 'in', 'on', 'at', 'by', 'for', 'with',
    'as', 'an', 'if', 'or', 'so', 'no', 'go',
    'up', 'ex', 'ok', 'hi', 'oh', 'ah', 'ha',
    'the', 'and', 'not', 'but', 'you', 'all', 'get',
    'this', 'that', 'from', 'they', 'have', 'just',
    # 常见域名/品牌残片 (会被裸域名检测过滤，但残片可能残留)
    'com', 'net', 'org', 'www', 'http', 'https',
    # 中国聊天常见的拉丁短串噪音
    'jpg', 'png', 'gif', 'mp4', 'mp3', 'avi',
    'pdf', 'doc', 'txt', 'xml', 'html', 'css', 'js',
    'wa', 'wb', 'wc', 'wd', 'wf', 'wg', 'wh', 'wj',
}
# 拉丁词最小评分阈值（过滤低质量碎片）
LATIN_MIN_SCORE = 25.0

# ── PMI + 邻接熵 阈值 ─────────────────────────────────────
# 来自中文新词发现文献的经验值
PMI_MIN = 3.0             # 最低内部PMI（凝聚度）
ENTROPY_MIN = 1.0         # 最低左右邻接熵（边界自由度）
# 放宽模式：如果频次很高但 PMI/熵略低于阈值，也保留
PMI_SOFT_MIN = 1.5        # 软下限（高频词放宽）
ENTROPY_SOFT_MIN = 0.6
HIGH_FREQ_THRESHOLD = 20  # 频次 ≥ 此值视为高频，使用软下限

# ── 谐音梗检测配置 ────────────────────────────────────────
PINYIN_SIMILARITY_MIN = 0.75   # 拼音相似度阈值
MAX_PINYIN_EDIT_DIST = 1       # 拼音编辑距离（无调号）

# ── 输出配置 ──────────────────────────────────────────────
FONT_MIN = 12
FONT_MAX = 48

# ── 极小核心常见字 (仅过滤这些字全组成的 ngram) ──────────
CORE_COMMON = set(
    '的了是在我不人有他这中个大上个来们说就到也下地得着你那出看为生过自以可子时'
    '还去能然没方所对成家都开经心想样后如之实气工而但于把被让给从向到与当'
)


# ══════════════════════════════════════════════════════════════
# 正则模式
# ══════════════════════════════════════════════════════════════

MSG_PATTERN = re.compile(
    r'^- `(\d{2}:\d{2}:\d{2})` \*\*(wxid[^*]+)\*\*: (.*)$'
)
DATE_PATTERN = re.compile(r'^## (\d{4}-\d{2}-\d{2})$')
SPECIAL_TOKEN_PATTERN = re.compile(r'\[[^\]]*\]')

# ── v3 系统消息检测 ─────────────────────────────────────
# XML 撤回消息: <sysmsg type="revokemsg">...</sysmsg>
SYSMSG_XML_PATTERN = re.compile(r'<sysmsg\s')
# 纯文本撤回: "XXX" recalled a message / You recalled a message
RECALL_TEXT_PATTERN = re.compile(
    r'(recalled a message|撤回了一条消息|撤回一条消息)',
    re.IGNORECASE
)
# 系统 token 消息（整个消息就是 [图片] [链接] [小程序] [聊天记录] 等）
SYSTEM_TOKEN_ONLY = re.compile(
    r'^[\s]*(\[图片\]|\[链接\]|\[小程序\]|\[聊天记录\]|'
    r'\[文件\]|\[语音\]|\[视频\]|\[表情\]|\[动画表情\]|'
    r'\[红包\]|\[转账\]|\[位置\]|\[名片\]|\[拍一拍\]|'
    r'\[语音通话\]|\[视频通话\]|\[戳一戳\]|\[QQ音乐\]|'
    r'\[音乐\]|\[笔记\]|\[商品\]|\[文章\])[\s]*$'
)

# URL: 完整 http(s) + 裸域名（如 b23.tv, t.cn, bilibili.com）
URL_PATTERN = re.compile(r'https?://\S+')
BARE_DOMAIN_PATTERN = re.compile(
    r'\b[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?\.'
    r'(?:com|cn|net|org|tv|io|cc|me|xyz|top|info|biz|co|'
    r'gov|edu|moe|app|dev|link|wiki|site|online|store|'
    r'tech|live|world|email|blog|shop|club|中国|公司|网络|'
    r'网址|在线|我爱你|中文网)\b',
    re.IGNORECASE
)
# URL 残留（小写字母+数字的碎片，看起来像短链接或路由标识）
URL_RESIDUE_PATTERN = re.compile(
    r'\b[a-z0-9]{8,}(?:\.[a-z]{2,})?\b'  # 长于8的纯字母数字序列
)

PURE_NUMBER_PATTERN = re.compile(r'^[\d\s\.\,\+\-\*\/\%\=]+$')
PURE_PUNCT_PATTERN = re.compile(r'^[\W_]+$')
CJK_PATTERN = re.compile(r'[一-鿿㐀-䶿]')
LATIN_PATTERN = re.compile(r'[a-zA-Z]')
DIGIT_PATTERN = re.compile(r'\d')
NGRAM_CHAR_PATTERN = re.compile(r'[一-鿿㐀-䶿a-zA-Z0-9]')

# ── 停用词 ────────────────────────────────────────────────

_BUILTIN_STOPWORDS = set("""
的了吗我不是你在有和都就要会很可以这那个什么怎么
因为所以但是如果虽然而且然后不过只是还是已经非常更
最比较也全部所有每任何自己别人大家我们你们他们她们
它们这里那里哪里这些那些哪些这样那样怎样这么那么
为什么多少几地得着过把被让给对从向到在与或而但却
不仅等等啊吧吗呢哈呀哦嗯啦哟呗嘛了说看想做去来
上中下前后面里外大小时点个为以能会要用对没出对
知道觉得可能应该可以需要希望想要认为以为看见听见
感觉发现变得觉得需要不能不会没法真的确实其实大概
也许或许反正就是话说讲告诉问回答明白理解记得忘了
以为知道想能会敢肯愿意要应该必须得该可以
""".split())


def load_stopwords(filepath='stopwords.txt'):
    """从文件加载停用词"""
    stopwords = set(_BUILTIN_STOPWORDS)
    script_dir = Path(__file__).parent
    stopword_file = script_dir / filepath
    if stopword_file.exists():
        with open(stopword_file, 'r', encoding='utf-8') as f:
            for line in f:
                word = line.strip()
                if word and not word.startswith('#'):
                    stopwords.add(word)
    return stopwords


# ══════════════════════════════════════════════════════════════
# 第一步: 解析聊天记录
# ══════════════════════════════════════════════════════════════

def parse_chat_log(filepath):
    """解析微信聊天导出文件"""
    messages = []
    current_date = None

    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.rstrip('\n').rstrip('\r')
            if not line:
                continue

            m = DATE_PATTERN.match(line)
            if m:
                current_date = m.group(1)
                continue

            if not line.startswith('- `'):
                continue

            m = MSG_PATTERN.match(line)
            if not m or not current_date:
                continue

            time_str, sender, content = m.groups()
            messages.append({
                'date': current_date,
                'time': time_str,
                'sender': sender,
                'content': content.strip()
            })

    return messages


# ══════════════════════════════════════════════════════════════
# 第二步: 消息清洗 (增强版)
# ══════════════════════════════════════════════════════════════

def clean_content(content):
    """
    v3 深度清洗:
    1. 检测系统消息 (XML撤回 / 纯文本撤回 / 系统token) → 整条丢弃
    2. 去除 [xxx] 特殊 token
    3. 去除 URL、裸域名、URL 残留
    4. 去除纯数字消息
    5. 去除纯 emoji/标点
    """
    # ── v3: 系统消息检测 (先于清洗) ──────────────────────────
    # XML 撤回消息
    if SYSMSG_XML_PATTERN.search(content):
        return None
    # 纯文本撤回
    if RECALL_TEXT_PATTERN.search(content):
        return None
    # 整条消息就是系统 token ([图片] [链接] 等)
    if SYSTEM_TOKEN_ONLY.match(content):
        return None

    # 1. 去除 [xxx] 特殊 token
    cleaned = SPECIAL_TOKEN_PATTERN.sub('', content)

    # 2. 去除完整 URL
    cleaned = URL_PATTERN.sub('', cleaned)

    # 3. 去除裸域名 (如 b23.tv, bilibili.com)
    cleaned = BARE_DOMAIN_PATTERN.sub('', cleaned)

    # 4. 压缩空白
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()

    # 5. 判空
    if not cleaned:
        return None

    # 6. 纯数字消息 → 丢弃
    if PURE_NUMBER_PATTERN.match(cleaned):
        return None

    # 7. 纯 emoji/标点消息 → 丢弃
    text_only = re.sub(r'[一-鿿㐀-䶿a-zA-Z0-9]', '', cleaned).strip()
    if len(text_only) == len(cleaned):
        return None

    return cleaned


def clean_text_for_ngram(text):
    """
    针对 ngram 提取的二次清洗：
    - 去除 URL 残留（8+ 字符的纯字母数字序列，如链接 ID）
    - 保留中文 + 英文 + 数字混合区域
    """
    cleaned = URL_RESIDUE_PATTERN.sub('', text)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned


# ══════════════════════════════════════════════════════════════
# 第三步-A: 通道A — jieba 标准分词
# ══════════════════════════════════════════════════════════════

def extract_jieba_words(messages, stopwords):
    """通道A: jieba 分词统计"""
    counter = Counter()

    for msg in messages:
        cleaned = clean_content(msg['content'])
        if not cleaned:
            continue

        words = jieba.lcut(cleaned)

        for w in words:
            w = w.strip()
            if len(w) < 2:
                continue
            if w in stopwords:
                continue
            if PURE_PUNCT_PATTERN.match(w) or PURE_NUMBER_PATTERN.match(w):
                continue
            if not re.search(r'[一-鿿㐀-䶿a-zA-Z]', w):
                continue
            counter[w] += 1

    filtered = Counter({k: v for k, v in counter.items() if v >= MIN_COUNT_JIEBA})
    return filtered


# ══════════════════════════════════════════════════════════════
# 工具函数
# ══════════════════════════════════════════════════════════════

def is_in_jieba_dict(word):
    """检查词是否在 jieba 词典中"""
    tokens = jieba.lcut(word)
    return len(tokens) == 1 and tokens[0] == word


def entropy(counter):
    """从 Counter 计算信息熵"""
    total = sum(counter.values())
    if total <= 1:
        return 0.0
    ent = 0.0
    for c in counter.values():
        p = c / total
        ent -= p * math.log2(p)
    return ent


# ══════════════════════════════════════════════════════════════
# 第三步-B: 通道B — n-gram + PMI + 邻接熵
# ══════════════════════════════════════════════════════════════

def compute_corpus_stats(texts):
    """
    计算语料库级别的统计量:
    - char_counter: 单字频率
    - bigram_counter: 字符 bigram 共现频率
    - total_chars: 总字符数
    - total_bigrams: 总 bigram 数
    """
    char_counter = Counter()
    bigram_counter = Counter()
    total_bigrams = 0

    for text in texts:
        chars = NGRAM_CHAR_PATTERN.findall(text)
        for i, ch in enumerate(chars):
            char_counter[ch] += 1
            if i < len(chars) - 1:
                bigram_counter[ch + chars[i + 1]] += 1
                total_bigrams += 1

    total_chars = sum(char_counter.values())
    return char_counter, bigram_counter, total_chars, total_bigrams


def scan_ngrams_with_context(texts, n_min, n_max):
    """
    扫描所有 ngram，分两遍：
      Pass 1: 逐文本快速计数（与原始版相同，已验证可行）
      Pass 2: 仅为频次达标的候选收集左右邻居上下文（大幅缩减工作量）

    返回: {ngram: {'count': N, 'left': Counter, 'right': Counter}}
    """
    # ═══ Pass 1: 快速计数（与 v1 相同逻辑） ═══════════════
    ngram_counters = {n: Counter() for n in range(n_min, n_max + 1)}

    for text in texts:
        cleaned = clean_text_for_ngram(text)
        if not cleaned:
            continue

        chars = NGRAM_CHAR_PATTERN.findall(cleaned)
        valid_text = ''.join(chars)

        for n in range(n_min, n_max + 1):
            if len(valid_text) < n:
                continue
            for i in range(len(valid_text) - n + 1):
                ngram = valid_text[i:i + n]
                # 纯数字 → 跳过
                if DIGIT_PATTERN.match(ngram):
                    continue
                # 纯拉丁且短 → 跳过
                if ngram.isascii() and len(ngram) < 3:
                    continue
                # 至少含 CJK 或足够长的 Latin
                if CJK_PATTERN.search(ngram) or (LATIN_PATTERN.search(ngram) and len(ngram) >= 3):
                    ngram_counters[n][ngram] += 1

    # 合并所有长度的 Counter
    all_counts = Counter()
    for n in range(n_min, n_max + 1):
        all_counts.update(ngram_counters[n])

    # 筛选频次达标的候选
    qualifying = {ng for ng, cnt in all_counts.items() if cnt >= MIN_COUNT_NGRAM}
    print(f"    ngram 原始扫描: {len(all_counts):,} 型, "
          f"频次>={MIN_COUNT_NGRAM}: {len(qualifying):,} 型")

    if not qualifying:
        return {}

    # ═══ Pass 2: 仅为 qualify 候选收集上下文 ══════════════
    ngram_left = defaultdict(Counter)
    ngram_right = defaultdict(Counter)

    for text in texts:
        cleaned = clean_text_for_ngram(text)
        if not cleaned:
            continue

        chars = NGRAM_CHAR_PATTERN.findall(cleaned)
        if len(chars) < n_min:
            continue

        for n in range(n_min, min(n_max, len(chars)) + 1):
            for i in range(len(chars) - n + 1):
                ngram = ''.join(chars[i:i + n])
                if ngram not in qualifying:
                    continue

                # 左邻居
                left = chars[i - 1] if i > 0 else '<BOS>'
                ngram_left[ngram][left] += 1

                # 右邻居
                right = chars[i + n] if i + n < len(chars) else '<EOS>'
                ngram_right[ngram][right] += 1

    # ═══ 组装结果 ═════════════════════════════════════════
    results = {}
    for ngram, count in all_counts.items():
        if count < MIN_COUNT_NGRAM:
            continue
        results[ngram] = {
            'count': count,
            'left': ngram_left.get(ngram, Counter()),
            'right': ngram_right.get(ngram, Counter()),
        }

    return results


def compute_internal_pmi(ngram, char_counter, bigram_counter, total_chars, total_bigrams):
    """
    计算 ngram 的内部凝聚度 (最小 PMI)。
    对 ngram 的每一种二元切分计算 PMI，取最小值。

    PMI(ch1, ch2) = log2( P(ch1,ch2) / (P(ch1) * P(ch2)) )

    对于多字 ngram，取所有连续字对的最小 PMI：
    Solid("ABCD") = min(PMI(A,B), PMI(B,C), PMI(C,D))
    """
    if len(ngram) < 2:
        return 0.0

    min_pmi = float('inf')

    for i in range(len(ngram) - 1):
        ch1 = ngram[i]
        ch2 = ngram[i + 1]
        pair = ch1 + ch2

        pair_count = bigram_counter.get(pair, 0)
        if pair_count < 2:  # 太少，PMI 不可靠
            return 0.0

        count1 = char_counter.get(ch1, 0)
        count2 = char_counter.get(ch2, 0)

        if count1 < 2 or count2 < 2:
            return 0.0

        p_pair = pair_count / total_bigrams
        p1 = count1 / total_chars
        p2 = count2 / total_chars

        if p1 <= 0 or p2 <= 0 or p_pair <= 0:
            return 0.0

        pmi = math.log2(p_pair / (p1 * p2))
        min_pmi = min(min_pmi, pmi)

    return min_pmi


def filter_ngrams_by_pmi_entropy(
    raw_ngrams, char_counter, bigram_counter, total_chars, total_bigrams,
    jieba_words_set, stopwords, min_count,
    pmi_min=PMI_MIN, entropy_min=ENTROPY_MIN
):
    """
    用 PMI + 双端邻接熵 过滤 ngram 假词。

    过滤逻辑 (三级):
      1. 严格通过: PMI >= pmi_min AND 左熵 >= entropy_min AND 右熵 >= entropy_min
      2. 高频放宽: count >= HIGH_FREQ_THRESHOLD AND PMI >= PMI_SOFT_MIN AND
                   两端熵 >= ENTROPY_SOFT_MIN
      3. 单字词典: 2-gram 且其中一字在字典中 → 额外检查 PMI >= 8.0 (极高凝聚)
      4. 否则丢弃

    返回: {ngram: {count, pmi, left_entropy, right_entropy, source}}
    """
    candidates = {}

    for ngram, info in raw_ngrams.items():
        count = info['count']

        if count < min_count:
            continue
        if ngram in jieba_words_set:
            continue
        if ngram in stopwords:
            continue

        # URL 残留检查：纯拉丁且无 CJK，大概率是域名碎片
        has_cjk = bool(CJK_PATTERN.search(ngram))
        has_latin = bool(LATIN_PATTERN.search(ngram))
        if not has_cjk:
            # 纯拉丁/数字：除非频次很高 (>=10)，否则跳过
            if count < 10:
                continue
            # 即使高频，也要求有明确的字母构成（至少3个不同字符）
            if len(set(ngram)) < 3:
                continue

        # 全核心常见字 → 跳过
        if all(ch in CORE_COMMON for ch in ngram):
            continue

        # ── 计算 PMI ──────────────────────────────────
        pmi = compute_internal_pmi(
            ngram, char_counter, bigram_counter, total_chars, total_bigrams
        )

        # ── 计算邻接熵 ────────────────────────────────
        left_ent = entropy(info['left'])
        right_ent = entropy(info['right'])

        # ── 分级过滤 ──────────────────────────────────
        passed = False
        level = 'rejected'

        # 严格通过
        if pmi >= pmi_min and left_ent >= entropy_min and right_ent >= entropy_min:
            passed = True
            level = 'strict'
        # 高频放宽
        elif count >= HIGH_FREQ_THRESHOLD:
            if pmi >= PMI_SOFT_MIN and left_ent >= ENTROPY_SOFT_MIN and right_ent >= ENTROPY_SOFT_MIN:
                passed = True
                level = 'freq_relaxed'
        # 特殊: 含极高频常见字的 bigram → 要求极高 PMI
        elif len(ngram) == 2:
            if pmi >= 8.0 and left_ent >= ENTROPY_SOFT_MIN and right_ent >= ENTROPY_SOFT_MIN:
                passed = True
                level = 'high_pmi_only'

        if passed:
            candidates[ngram] = {
                'count': count,
                'pmi': round(pmi, 2),
                'left_entropy': round(left_ent, 2),
                'right_entropy': round(right_ent, 2),
                'filter_level': level,
                'has_cjk': has_cjk,
                'has_latin': has_latin,
            }

    return candidates


# ══════════════════════════════════════════════════════════════
# 第三步-C: 通道C — 拼音空间谐音梗检测
# ══════════════════════════════════════════════════════════════

# ── 拼音缓存 ────────────────────────────────────────────
_pinyin_cache = {}

def get_pinyin(word):
    """获取词的拼音（带缓存）"""
    if word not in _pinyin_cache:
        if HAS_PYPINYIN:
            _pinyin_cache[word] = ''.join(
                lazy_pinyin(word, style=Style.TONE3, neutral_tone_with_five=True)
            )
        else:
            _pinyin_cache[word] = ''.join(str(ord(ch) % 100) for ch in word)
    return _pinyin_cache[word]


def build_pinyin_index(jieba_words, extra_words=None, max_items=80000):
    """
    为 jieba 词表 + 额外词表建立拼音索引。
    包含有调号和无调号两个版本。

    参数:
      jieba_words: jieba 分词结果的可迭代对象
      extra_words: 额外词的可迭代对象（如 ngram 候选，用于谐音梗参照）
      max_items: 最大索引条目数（控制内存和速度）

    返回: {pinyin_str: set(words)}
    """
    pinyin_index = defaultdict(set)
    all_words = set(jieba_words)
    if extra_words:
        all_words.update(extra_words)

    # 限制数量：优先取更短的词（更有可能是梗的参照）
    sorted_words = sorted(all_words, key=lambda w: (len(w), w))[:max_items]
    total = len(sorted_words)

    for i, word in enumerate(sorted_words):
        if len(word) < 2:
            continue

        py = get_pinyin(word)
        py_notone = re.sub(r'\d', '', py)

        pinyin_index[py].add(word)
        if py_notone != py:
            pinyin_index[py_notone].add(word)

        # 进度输出（每 20000 个词）
        if (i + 1) % 20000 == 0:
            print(f"    拼音索引构建: {i+1}/{total}", flush=True)

    return dict(pinyin_index)


def pinyin_edit_distance(py1, py2):
    """
    计算两个拼音串的编辑距离（字符级）。
    用于比较两个词的发音相似度。
    """
    # 使用 Levenshtein 距离
    m, n = len(py1), len(py2)
    if m == 0:
        return n
    if n == 0:
        return m

    # 只保留前一行
    prev = list(range(n + 1))
    curr = [0] * (n + 1)

    for i in range(1, m + 1):
        curr[0] = i
        for j in range(1, n + 1):
            cost = 0 if py1[i - 1] == py2[j - 1] else 1
            curr[j] = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost)
        prev, curr = curr, prev

    return prev[n]


def pinyin_similarity(py1, py2):
    """基于编辑距离的拼音相似度"""
    max_len = max(len(py1), len(py2))
    if max_len == 0:
        return 1.0
    dist = pinyin_edit_distance(py1, py2)
    return 1.0 - (dist / max_len)


def find_homophone_matches(ngram, pinyin_index):
    """
    在拼音索引中查找与 ngram 发音相同但字形不同的词（谐音梗）。
    只做精确拼音匹配（含/不含调号），不做模糊搜索以确保性能。

    返回: [(word, similarity, in_jieba), ...]
    """
    if not HAS_PYPINYIN or len(ngram) < 2:
        return []

    ngram_py = get_pinyin(ngram)
    ngram_py_notone = re.sub(r'\d', '', ngram_py)

    seen = set()
    matches = []

    # 精确拼音匹配（有调号 + 无调号）
    for py_key in (ngram_py, ngram_py_notone):
        if py_key not in pinyin_index:
            continue
        for dict_word in pinyin_index[py_key]:
            if dict_word == ngram or dict_word in seen:
                continue
            seen.add(dict_word)
            # 必须是不同的字（如果字完全相同就不是谐音梗了）
            if dict_word == ngram:
                continue
            dict_py = get_pinyin(dict_word)
            sim = pinyin_similarity(ngram_py_notone, re.sub(r'\d', '', dict_py))
            matches.append({
                'word': dict_word,
                'similarity': round(sim, 3),
                'in_jieba': is_in_jieba_dict(dict_word),
            })

    # 排序: 必须在 jieba 词典中的优先, 再按相似度
    matches.sort(key=lambda x: (not x['in_jieba'], -x['similarity']))

    # 只返回"在 jieba 词典中"的匹配（确保参照词是真实存在的词，不是另一个 ngram 碎片）
    valid_matches = [m for m in matches if m['in_jieba']]
    return valid_matches[:5]


def detect_homophones(ngram_candidates, pinyin_index):
    """
    对每个 ngram 候选做谐音梗检测。
    pinyin_index 包含 jieba 词和原始 ngram 词的拼音映射。

    返回: 加了 homophone 字段的候选 dict。
    """
    enriched = {}
    for ngram, info in ngram_candidates.items():
        info = dict(info)
        info['homophones'] = []

        # 谐音梗条件:
        #   1. 不在 jieba 词典中（非标准用字）
        #   2. 含 CJK 字符
        #   3. PMI 下限按长度分级：2字>=6.0, 3字>=4.0, 4+字>=3.0
        #      （短词需要更强的凝聚度证明自己是"词"而不是语法碎片）
        min_pmi_for_pun = {2: 6.0, 3: 4.0}.get(len(ngram), 3.0)
        if (not is_in_jieba_dict(ngram)
                and info.get('has_cjk', True)
                and info.get('pmi', 0) >= min_pmi_for_pun):
            matches = find_homophone_matches(ngram, pinyin_index)
            if matches:
                info['homophones'] = matches
                best_sim = matches[0]['similarity']
                if best_sim >= 0.85:
                    info['pun_type'] = 'homophone'
                elif best_sim >= PINYIN_SIMILARITY_MIN:
                    info['pun_type'] = 'possible_homophone'

        enriched[ngram] = info
    return enriched


# ══════════════════════════════════════════════════════════════
# 第三步-D: 通道D — 纯拉丁词检测 (拼音首字母缩写)
# ══════════════════════════════════════════════════════════════

def extract_latin_candidates(cleaned_texts, char_counter, bigram_counter,
                              total_chars, total_bigrams, jieba_words_set):
    """
    通道D: 检测纯拉丁字母序列（拼音首字母缩写/人名/梗词）

    与中文 ngram 不同，拉丁字母序列的随机组合概率极低，
    因此 PMI 阈值更高 (LATIN_WORD_PMI_MIN=4.0)。

    检测目标:
      - 人名缩写: zxq, yjy, lzx (拼音首字母)
      - 网络梗: yyds, nsdd, awsl, xswl
      - 品牌/专名: b站 (这个有中文), nba, kfc (纯英文)

    返回: {word: {count, pmi, left_entropy, right_entropy, latin_reason, ...}}
    """
    latin_raw = Counter()
    latin_left = defaultdict(Counter)
    latin_right = defaultdict(Counter)

    # ── Pass 1: 扫描纯拉丁词 (2-5 字母) ──────────────────
    LATIN_WORD_RE = re.compile(r'[a-zA-Z]{' + str(LATIN_WORD_MIN_LEN) + r',' + str(LATIN_WORD_MAX_LEN) + r'}')

    for text in cleaned_texts:
        # 对每个文本，找所有纯拉丁词（前后各取1个字符做上下文）
        for m in LATIN_WORD_RE.finditer(text):
            word = m.group()
            # 过滤全大写或全小写的常见噪音（如 "OK", "ID" 太短）
            latin_raw[word] += 1

            start, end = m.start(), m.end()
            if start > 0:
                latin_left[word][text[start - 1]] += 1
            else:
                latin_left[word]['<BOS>'] += 1
            if end < len(text):
                latin_right[word][text[end]] += 1
            else:
                latin_right[word]['<EOS>'] += 1

    # ── 频次 + 黑名单 + 评分过滤 ──────────────────────────
    latin_filtered = {}
    for word, count in latin_raw.items():
        if count < LATIN_WORD_MIN_COUNT:
            continue

        # v3: 英文碎片黑名单
        word_lower = word.lower()
        if word_lower in LATIN_FRAGMENT_BLOCKLIST:
            continue
        # 全大写常见噪声 (OK, ID 等 2 字母全大写)
        if len(word) <= 2 and word.isupper():
            continue

        left_ent = entropy(latin_left[word])
        right_ent = entropy(latin_right[word])

        # 检查是否在 jieba 词典中
        in_jieba = word.lower() in jieba_words_set or word in jieba_words_set

        # 拉丁词评分: 频次分 + 双端邻接熵
        latin_score = math.log2(count + 1) * 5 + (left_ent + right_ent) * 3

        # 不在词典中加分
        if not in_jieba:
            latin_score += NOT_IN_DICT_BONUS * 0.5

        # v3: 拼音首字母缩写检测 (全辅音或几乎全辅音)
        has_vowels = bool(re.search(r'[aeiouAEIOU]', word))
        has_consonants = bool(re.search(r'[bcdfghjklmnpqrstvwxyzBCDFGHJKLMNPQRSTVWXYZ]', word))
        # 纯辅音 → 高概率是拼音首字母缩写
        looks_like_initials = has_consonants and not has_vowels
        if looks_like_initials and count >= 5:
            latin_score += 8  # 拼音首字母缩写 bonus (比 v2 更高)
            reason = '拼音首字母缩写'
        elif has_consonants and has_vowels and len(word) >= 3:
            # 混合元辅音且长度>=3 → 可能是英文词/专名
            reason = '纯英文词'
        else:
            # 全元音或极短混合 → 大概率噪音
            reason = '纯英文词'

        # v3: 评分阈值过滤 (LATIN_MIN_SCORE=25)
        if latin_score < LATIN_MIN_SCORE:
            continue

        # 过滤低熵
        if left_ent < LATIN_WORD_ENTROPY_MIN and right_ent < LATIN_WORD_ENTROPY_MIN:
            if count < 10:
                continue

        latin_filtered[word] = {
            'count': count,
            'left_entropy': round(left_ent, 4),
            'right_entropy': round(right_ent, 4),
            'has_latin': True,
            'latin_reason': reason,
            '_latin_score': latin_score,
        }

    return latin_filtered


# ══════════════════════════════════════════════════════════════
# 第四步: 合并排序
# ══════════════════════════════════════════════════════════════

def score_candidate(ngram, info):
    """
    v3 综合评分 (去中英混用 bonus, 提高非词典词权重):
      base = log2(count) * 5                   频次分
      + PMI * 3                                 凝聚度分
      + (left_ent + right_ent) * 2              边界自由度分
      + homophone bonus (0~15)                  谐音梗加分
      + not-in-dict bonus (0~{NOT_IN_DICT_BONUS})              词典外加分 (大幅提权)
      (v2 的 mixed-script bonus 已删除 —— 19个中英混用词8个是人名碎片)
    """
    score = 0.0

    count = info.get('count', 1)
    score += math.log2(count + 1) * 5

    pmi = info.get('pmi', 0)
    score += pmi * 3

    left_ent = info.get('left_entropy', 0)
    right_ent = info.get('right_entropy', 0)
    score += (left_ent + right_ent) * 2

    # 谐音梗加分 (同 v2)
    homophones = info.get('homophones', [])
    if homophones:
        best_sim = homophones[0]['similarity']
        if best_sim >= 0.95:
            score += 15
        elif best_sim >= 0.85:
            score += 10
        elif best_sim >= PINYIN_SIMILARITY_MIN:
            score += 5

    # 不在 jieba 词典中 → 大幅加分 (v3: NOT_IN_DICT_BONUS=15, v2 是 8)
    if not is_in_jieba_dict(ngram):
        score += NOT_IN_DICT_BONUS

    # v3: 删除混合脚本 bonus (v2 中 19 个中英混用词 8 个是 "zxq说" 类人名碎片)

    return score


def merge_and_rank_v3(jieba_counter, ngram_candidates, latin_candidates, stopwords, max_candidates=MAX_CANDIDATES):
    """
    v3 配额制合并:
      - 40% jieba 高频词 (评分 × JIEBA_SCORE_MULTIPLIER 降权)
      - 30% ngram 纯中文新词 (按综合评分, 非词典词提权)
      - 15% ngram 谐音梗词
      - 5%  ngram 混合脚本 (降配, 无 bonus)
      - 8%  纯拉丁词 (通道D, 如 zxq/yyds)
      - 2%  弹性补充
    """
    jieba_quota = int(max_candidates * 0.40)
    ngram_cn_quota = int(max_candidates * 0.30)
    ngram_pun_quota = int(max_candidates * 0.15)
    ngram_mixed_quota = int(max_candidates * 0.05)
    latin_quota = int(max_candidates * 0.08)

    seen = set()
    all_candidates = []
    total_jieba_count = sum(jieba_counter.values())

    # ── 1. Jieba 高频 (v3: 评分降权) ──────────────────────
    jieba_high = []
    for word, count in jieba_counter.most_common(jieba_quota):
        seen.add(word)
        freq = count / total_jieba_count if total_jieba_count > 0 else 0
        # v3: jieba 词 _score 乘以降权系数
        jieba_high.append({
            'word': word, 'count': count,
            'frequency': round(freq, 6),
            'source': 'jieba',
            'reason': '高频词',
            '_score': count * JIEBA_SCORE_MULTIPLIER
        })

    # ── 2. Ngram 分类 ────────────────────────────────────
    pure_cn = {}       # 纯中文
    homophone_cn = {}  # 谐音梗
    mixed_sc = {}       # 混合脚本

    for ngram, info in ngram_candidates.items():
        if ngram in seen:
            continue
        if ngram in stopwords:
            continue

        pun_type = info.get('pun_type', '')
        if pun_type == 'homophone':
            homophone_cn[ngram] = info
        elif info.get('has_latin') or DIGIT_PATTERN.search(ngram):
            mixed_sc[ngram] = info
        else:
            pure_cn[ngram] = info

    # ── 3. 纯中文新词 ────────────────────────────────────
    cn_scored = [(ngram, info, score_candidate(ngram, info))
                 for ngram, info in pure_cn.items()]
    cn_scored.sort(key=lambda x: x[2], reverse=True)

    for ngram, info, score in cn_scored[:ngram_cn_quota]:
        if ngram in seen:
            continue
        seen.add(ngram)
        all_candidates.append(build_candidate_entry(ngram, info, 'ngram', score))

    # ── 4. 谐音梗词 ──────────────────────────────────────
    pun_scored = [(ngram, info, score_candidate(ngram, info))
                  for ngram, info in homophone_cn.items()]
    pun_scored.sort(key=lambda x: x[2], reverse=True)

    for ngram, info, score in pun_scored[:ngram_pun_quota]:
        if ngram in seen:
            continue
        seen.add(ngram)
        all_candidates.append(build_candidate_entry(ngram, info, 'ngram', score))

    # ── 5. 混合脚本 (v3: 降配, 无 mixed-script bonus) ────
    mixed_scored = [(ngram, info, score_candidate(ngram, info))
                    for ngram, info in mixed_sc.items()]
    mixed_scored.sort(key=lambda x: x[2], reverse=True)

    for ngram, info, score in mixed_scored[:ngram_mixed_quota]:
        if ngram in seen:
            continue
        seen.add(ngram)
        all_candidates.append(build_candidate_entry(ngram, info, 'ngram', score))

    # ── 6. 纯拉丁词 (通道D) ──────────────────────────────
    latin_scored = [(ngram, info, info.get('_latin_score', 0))
                    for ngram, info in latin_candidates.items()
                    if ngram not in seen and ngram not in stopwords]
    latin_scored.sort(key=lambda x: x[2], reverse=True)

    for ngram, info, score in latin_scored[:latin_quota]:
        if ngram in seen:
            continue
        seen.add(ngram)
        # 构建条目时使用 build_candidate_entry，但 source 标记为 'latin'
        entry = build_candidate_entry(ngram, info, 'latin', score)
        entry['reason'] = info.get('latin_reason', '首字母缩写')
        all_candidates.append(entry)

    # ── 7. 合并 jieba 高频 ──────────────────────────────
    all_candidates.extend(jieba_high)

    # ── 8. 不足补充 (优先从 ngram > latin > jieba) ────────
    remaining = max_candidates - len(all_candidates)
    if remaining > 0:
        extra = 0
        # 先从 ngram 纯中文补充
        for ngram, info, score in cn_scored[ngram_cn_quota:]:
            if extra >= remaining:
                break
            if ngram not in seen:
                seen.add(ngram)
                all_candidates.append(build_candidate_entry(ngram, info, 'ngram', score))
                extra += 1
        # 再从纯拉丁补充
        if extra < remaining:
            for ngram, info, score in latin_scored[latin_quota:]:
                if extra >= remaining:
                    break
                if ngram not in seen:
                    seen.add(ngram)
                    entry = build_candidate_entry(ngram, info, 'latin', score)
                    entry['reason'] = info.get('latin_reason', '首字母缩写')
                    all_candidates.append(entry)
                    extra += 1
        # 最后从 jieba 补充
        if extra < remaining:
            for word, count in jieba_counter.most_common():
                if extra >= remaining:
                    break
                if word not in seen:
                    seen.add(word)
                    freq = count / total_jieba_count if total_jieba_count > 0 else 0
                    all_candidates.append({
                        'word': word, 'count': count,
                        'frequency': round(freq, 6),
                        'source': 'jieba',
                        'reason': '补充词',
                        '_score': count * JIEBA_SCORE_MULTIPLIER
                    })
                    extra += 1

    # 统计
    jieba_in = sum(1 for c in all_candidates if c['source'] == 'jieba')
    ngram_in = sum(1 for c in all_candidates if c['source'] == 'ngram')
    latin_in = sum(1 for c in all_candidates if c['source'] == 'latin')
    pun_in = sum(1 for c in all_candidates
                 if c.get('pun_type') in ('homophone', 'possible_homophone'))
    print(f"  入选: jieba={jieba_in}, ngram={ngram_in}, latin={latin_in} (谐音梗={pun_in})")

    # 频次分布
    low = sum(1 for c in all_candidates if c['count'] <= 3)
    mid = sum(1 for c in all_candidates if 4 <= c['count'] <= 20)
    high = sum(1 for c in all_candidates if c['count'] > 20)
    print(f"  频次: 低频(<=3)={low}, 中频(4-20)={mid}, 高频(>20)={high}")

    # PMI/熵 分布
    with_pmi = [c for c in all_candidates if 'pmi' in c]
    if with_pmi:
        avg_pmi = sum(c.get('pmi', 0) for c in with_pmi) / len(with_pmi)
        avg_le = sum(c.get('left_entropy', 0) for c in with_pmi) / len(with_pmi)
        avg_re = sum(c.get('right_entropy', 0) for c in with_pmi) / len(with_pmi)
        print(f"  ngram 平均 PMI={avg_pmi:.1f}, 左熵={avg_le:.1f}, 右熵={avg_re:.1f}")

    # 最终排序
    all_candidates.sort(key=lambda x: x['_score'], reverse=True)

    # 重新计算 frequency + 清理 _score
    total_count = sum(c['count'] for c in all_candidates)
    for c in all_candidates:
        c['frequency'] = round(c['count'] / total_count, 6) if total_count > 0 else 0
        del c['_score']

    return all_candidates


def build_candidate_entry(ngram, info, source, score):
    """构建候选词条目"""
    entry = {
        'word': ngram,
        'count': info['count'],
        'frequency': 0,
        'source': source,
        '_score': score,
    }

    # PMI + 熵 元数据
    if 'pmi' in info:
        entry['pmi'] = info['pmi']
        entry['left_entropy'] = info['left_entropy']
        entry['right_entropy'] = info['right_entropy']

    # 谐音梗元数据
    homophones = info.get('homophones', [])
    if homophones:
        entry['homophones'] = homophones
        entry['pun_type'] = info.get('pun_type', 'possible_homophone')
        # 生成人类可读的解释
        best = homophones[0]
        if best['similarity'] >= 0.85:
            entry['reason'] = f'谐音梗 → "{best["word"]}" (拼音相似度 {best["similarity"]:.0%})'
        else:
            entry['reason'] = f'疑似谐音 → "{best["word"]}" (拼音相似度 {best["similarity"]:.0%})'
    elif info.get('filter_level') == 'strict':
        entry['reason'] = f'新词发现 (PMI={info.get("pmi",0):.1f}, 凝聚度强)'
    elif info.get('filter_level') == 'freq_relaxed':
        entry['reason'] = f'高频新词 (频次={info["count"]})'
    else:
        entry['reason'] = '潜在新词'

    # 混合脚本标记
    if info.get('has_latin') and info.get('has_cjk'):
        entry['reason'] += ' [混合脚本]'

    # 纯拉丁词说明
    if info.get('latin_reason'):
        entry['reason'] = info.get('latin_reason')

    return entry


# ══════════════════════════════════════════════════════════════
# 输出
# ══════════════════════════════════════════════════════════════

def output_all_jieba(jieba_counter, output_dir):
    """输出完整 jieba 词表"""
    os.makedirs(output_dir, exist_ok=True)
    all_words_path = os.path.join(output_dir, 'all_jieba_words.txt')
    with open(all_words_path, 'w', encoding='utf-8') as f:
        f.write("# 完整 jieba 词表 (按频次降序)\n")
        f.write(f"# 共 {len(jieba_counter)} 个词\n\n")
        for i, (word, count) in enumerate(jieba_counter.most_common(), 1):
            f.write(f"{i:6d}. {word:　<10s} {count:>8d}\n")
    return all_words_path


def output_candidates_v3(candidates, output_dir, ngram_count_before_filter=0, latin_count=0):
    """输出候选词文件（增强版）"""
    os.makedirs(output_dir, exist_ok=True)

    # ── JSON 输出 ──────────────────────────────────────────
    json_path = os.path.join(output_dir, 'candidate_words.json')
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(candidates, f, ensure_ascii=False, indent=2)

    # ── 文本预览输出 ──────────────────────────────────────
    txt_path = os.path.join(output_dir, 'candidate_words.txt')
    with open(txt_path, 'w', encoding='utf-8') as f:
        f.write("# 候选词汇列表 (v3 增强版 · 四通道)\n")
        f.write(f"# 共 {len(candidates)} 个候选词\n")
        ngram_cnt = sum(1 for c in candidates if c['source']=='ngram')
        latin_cnt = sum(1 for c in candidates if c['source']=='latin')
        f.write(f"# ngram 过滤前: {ngram_count_before_filter} → 过滤后: {ngram_cnt} 个\n")
        if latin_count:
            f.write(f"# 通道D (纯拉丁词): {latin_count} 个 → 入选: {latin_cnt} 个\n")
        f.write("#\n")
        f.write("# 格式: 排名 | 词 | 频次 | 来源 | PMI | 左熵 | 右熵 | 说明\n")
        f.write("# 标记说明:\n")
        f.write("#   [jieba]   = 标准分词结果 (v3 降权)\n")
        f.write("#   [ngram]   = PMI+熵过滤后的新词\n")
        f.write("#   [latin]   = 纯拉丁词 (拼音首字母缩写/人名/梗)\n")
        f.write("#   🎯 = 谐音梗（已匹配到原词）\n")
        f.write("#   🔍 = 潜在新词（非谐音）\n")
        f.write("#\n")

        for i, c in enumerate(candidates, 1):
            word = c['word']
            count = c['count']
            source = c['source']

            pmi_str = f"{c.get('pmi', '-'):>5}" if 'pmi' in c else "    -"
            le_str = f"{c.get('left_entropy', '-'):>4}" if 'left_entropy' in c else "   -"
            re_str = f"{c.get('right_entropy', '-'):>4}" if 'right_entropy' in c else "   -"

            # 标记
            flags = ''
            pun_type = c.get('pun_type', '')
            if pun_type == 'homophone':
                flags = '  🎯 谐音梗'
            elif pun_type == 'possible_homophone':
                flags = '  🎯? 疑似谐音'
            elif source == 'latin':
                flags = '  🔤 首字母'
            elif source == 'ngram':
                flags = '  🔍 新词'

            # 混合脚本标记
            has_cn = bool(CJK_PATTERN.search(word))
            has_en = bool(LATIN_PATTERN.search(word))
            if has_cn and has_en:
                flags += ' ⚡'

            reason = c.get('reason', '')
            homophones = c.get('homophones', [])
            if homophones:
                best = homophones[0]
                reason_extra = f' → "{best["word"]}" (相似度{best["similarity"]:.0%})'
                reason += reason_extra

            f.write(f"{i:4d}. {word:　<8s} {count:>6d}  [{source}] "
                    f"PMI={pmi_str}  LE={le_str}  RE={re_str}{flags}\n")
            if reason and reason != c.get('word', ''):
                f.write(f"     └─ {reason}\n")

    return json_path, txt_path


def print_stats(messages, jieba_counter, ngram_raw, ngram_filtered, latin_candidates, merged):
    """打印统计信息"""
    print("\n" + "=" * 60)
    print("[统计] 提取统计")
    print("=" * 60)
    print(f"  总消息数:          {len(messages):>8,}")
    print(f"  通道A (jieba):     {len(jieba_counter):>8,} 个独特词")
    print(f"  通道B (ngram原始): {len(ngram_raw):>8,} 个")
    print(f"  通道B (PMI+熵过滤):{len(ngram_filtered):>8,} 个 (过滤掉 {len(ngram_raw)-len(ngram_filtered):,})")
    print(f"  通道C (谐音梗):    {sum(1 for v in ngram_filtered.values() if v.get('homophones')):>8,} 个")
    print(f"  通道D (纯拉丁词):  {len(latin_candidates):>8,} 个")
    print(f"  合并最终:          {len(merged):>8,} 个候选词")
    print(f"")

    if len(merged) == 0:
        print("  (无候选词)")
        print("=" * 60)
        return

    # 来源统计
    jieba_src = sum(1 for c in merged if c['source'] == 'jieba')
    ngram_src = sum(1 for c in merged if c['source'] == 'ngram')
    latin_src = sum(1 for c in merged if c['source'] == 'latin')
    pun_src = sum(1 for c in merged if c.get('pun_type') in ('homophone', 'possible_homophone'))
    print(f"  来源 - jieba: {jieba_src:>8,} ({jieba_src/len(merged)*100:.1f}%)")
    print(f"  来源 - ngram: {ngram_src:>8,} ({ngram_src/len(merged)*100:.1f}%)")
    print(f"  来源 - latin: {latin_src:>8,} ({latin_src/len(merged)*100:.1f}%)")
    print(f"  其中谐音梗:   {pun_src:>8,}")
    print(f"")

    # Top 20
    print("  Top 20 候选词:")
    print(f"  {'排名':<5} {'词':<14} {'频次':<8} {'来源':<8} {'说明'}")
    print(f"  {'-'*65}")
    for i, c in enumerate(merged[:20], 1):
        reason = c.get('reason', '')[:30]
        print(f"  {i:<5} {c['word']:<14} {c['count']:<8} [{c['source']}]  {reason}")

    print("=" * 60)


# ══════════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description='四通道聊天词频提取工具 v3（深度清洗：去时间戳/ID/URL/系统消息 + PMI+熵+拼音谐音梗+纯拉丁词检测）',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python extract_words_v3.py 聊天.md
  python extract_words_v3.py 聊天.md --output-dir ./output_v3
  python extract_words_v3.py 聊天.md --sender wxid_xxxxxxxxxxxxxx
  python extract_words_v3.py 聊天.md --max-candidates 2000
  python extract_words_v3.py 聊天.md --pmi-min 4.0 --entropy-min 1.2

v3 改进:
  - 深度清洗: 系统消息/撤回/纯图片链接丢弃
  - jieba 降权 (×0.6), 非词典词提权 (bonus 8→15)
  - 删除了中英混用 bonus
  - 新增通道D: 纯拉丁词检测 (拼音首字母缩写/人名)
        """
    )
    parser.add_argument('chat_file', help='聊天记录 Markdown 文件路径')
    parser.add_argument('--output-dir', '-o', default='./output_v3',
                        help='输出目录 (默认: ./output_v3)')
    parser.add_argument('--sender', '-s', default=None,
                        help='只提取指定发送者的消息')
    parser.add_argument('--max-candidates', '-n', type=int, default=MAX_CANDIDATES,
                        help=f'候选池最大数量 (默认: {MAX_CANDIDATES})')
    parser.add_argument('--stopwords', default='stopwords.txt',
                        help='停用词文件路径')
    parser.add_argument('--pmi-min', type=float, default=PMI_MIN,
                        help=f'最低 PMI 阈值 (默认: {PMI_MIN})')
    parser.add_argument('--entropy-min', type=float, default=ENTROPY_MIN,
                        help=f'最低邻接熵阈值 (默认: {ENTROPY_MIN})')
    parser.add_argument('--no-pinyin', action='store_true',
                        help='禁用拼音谐音梗检测')
    args = parser.parse_args()

    # 命令行覆盖阈值
    cli_pmi_min = args.pmi_min
    cli_entropy_min = args.entropy_min
    local_max = args.max_candidates

    chat_file = args.chat_file
    if not os.path.exists(chat_file):
        print(f"错误: 文件不存在: {chat_file}")
        sys.exit(1)

    # ── 加载停用词 ──────────────────────────────────────────
    print("📖 加载停用词...")
    stopwords = load_stopwords(args.stopwords)
    print(f"   共 {len(stopwords)} 个停用词")

    # ── 解析聊天记录 ──────────────────────────────────────
    print(f"📄 解析聊天记录: {chat_file}")
    messages = parse_chat_log(chat_file)
    print(f"   解析到 {len(messages)} 条消息")

    if args.sender:
        messages = [m for m in messages if m['sender'] == args.sender]
        print(f"   筛选发送者 '{args.sender}': 剩余 {len(messages)} 条")

    # ── 清洗消息 (v3 深度清洗: 去系统消息/撤回/URL/纯数字) ──
    print("🧹 清洗消息（v3 深度清洗：系统消息/撤回/URL/纯数字/纯系统token）...")
    cleaned_texts = []
    discarded_sysmsg = 0
    for msg in messages:
        cleaned = clean_content(msg['content'])
        if cleaned:
            cleaned_texts.append(cleaned)
        else:
            discarded_sysmsg += 1
    print(f"   有效消息: {len(cleaned_texts)} 条 "
          f"({len(cleaned_texts)/max(len(messages),1)*100:.1f}%)")
    if discarded_sysmsg:
        print(f"   丢弃系统/空消息: {discarded_sysmsg} 条")

    # ── 通道A: jieba 分词 ─────────────────────────────────
    print("🔪 通道A: jieba 分词...")
    jieba_counter = extract_jieba_words(messages, stopwords)
    jieba_words_set = set(jieba_counter.keys())
    print(f"   获得 {len(jieba_counter)} 个独特词 (count >= {MIN_COUNT_JIEBA})")

    # ── 语料库统计（用于 PMI） ────────────────────────────
    print("📊 计算语料库统计 (单字/二字共现频率)...")
    char_counter, bigram_counter, total_chars, total_bigrams = compute_corpus_stats(cleaned_texts)
    print(f"   总字符数: {total_chars:,}, 总bigram数: {total_bigrams:,}")

    # ── 通道B: ngram 扫描 + PMI/熵过滤 ──────────────────
    print("🔍 通道B: n-gram 扫描 (含上下文)...")
    raw_ngrams = scan_ngrams_with_context(cleaned_texts, NGRAM_MIN, NGRAM_MAX)
    print(f"   原始 ngram: {len(raw_ngrams):,} 个")

    print(f"🔬 PMI+邻接熵过滤 (PMI>={cli_pmi_min}, 熵>={cli_entropy_min})...")
    ngram_filtered = filter_ngrams_by_pmi_entropy(
        raw_ngrams, char_counter, bigram_counter, total_chars, total_bigrams,
        jieba_words_set, stopwords, MIN_COUNT_NGRAM,
        pmi_min=cli_pmi_min, entropy_min=cli_entropy_min
    )
    filtered_out = len(raw_ngrams) - len(ngram_filtered)
    print(f"   过滤后: {len(ngram_filtered):,} 个 (过滤掉 {filtered_out:,} 个, "
          f"{filtered_out/max(len(raw_ngrams),1)*100:.1f}%)")

    # ── 通道C: 拼音谐音梗检测 ────────────────────────────
    if not args.no_pinyin and HAS_PYPINYIN:
        print("🎵 通道C: 拼音空间谐音梗检测...")
        extra_ref_words = [
            ng for ng, info in raw_ngrams.items()
            if info['count'] >= 2 and CJK_PATTERN.search(ng)
        ]
        pinyin_index = build_pinyin_index(
            jieba_counter.keys(),
            extra_words=extra_ref_words
        )
        print(f"   拼音索引: {len(pinyin_index):,} 个拼音条目 "
              f"(jieba={len(jieba_counter):,} + ngram参照={len(extra_ref_words):,})")
        ngram_enriched = detect_homophones(ngram_filtered, pinyin_index)
        pun_count = sum(1 for v in ngram_enriched.values() if v.get('homophones'))
        print(f"   检测到谐音梗候选: {pun_count} 个")
    elif args.no_pinyin:
        print("⏭️  通道C: 已禁用 (--no-pinyin)")
        ngram_enriched = ngram_filtered
    else:
        print("⚠️  通道C: 降级 (安装 pypinyin 获得完整功能: pip install pypinyin)")
        ngram_enriched = ngram_filtered

    # ── 通道D: 纯拉丁词检测 (v3 新增) ──────────────────
    print("🔤 通道D: 纯拉丁词检测 (拼音首字母缩写/人名/梗)...")
    latin_candidates = extract_latin_candidates(
        cleaned_texts, char_counter, bigram_counter,
        total_chars, total_bigrams, jieba_words_set
    )
    print(f"   检测到纯拉丁词: {len(latin_candidates)} 个")

    # ── 合并排序 (v3: jieba 降权) ────────────────────────
    print("🔀 合并排序 (v3 配额制: jieba降权×0.6, 非词典提权+15)...")
    merged = merge_and_rank_v3(jieba_counter, ngram_enriched, latin_candidates, stopwords, local_max)

    # ── 输出 ─────────────────────────────────────────────
    print(f"💾 输出到: {args.output_dir}")
    json_path, txt_path = output_candidates_v3(
        merged, args.output_dir,
        ngram_count_before_filter=len(raw_ngrams),
        latin_count=len(latin_candidates)
    )
    all_words_path = output_all_jieba(jieba_counter, args.output_dir)

    # ── 打印统计 ─────────────────────────────────────────
    print_stats(messages, jieba_counter, raw_ngrams, ngram_enriched, latin_candidates, merged)

    print(f"\nDone!")
    print(f"   候选词 JSON: {json_path}")
    print(f"   候选词文本: {txt_path}")
    print(f"   完整 jieba 词表: {all_words_path}")
    print(f"\n  下一步:")
    print(f"   1. 浏览 candidate_words.txt 查看结果（含梗解释）")
    print(f"   2. 运行 python scripts/visual_filter.py 进行人工筛选")
    print(f"   3. 或创建 keep.txt + 运行 filter_words.py")


if __name__ == '__main__':
    main()
