#!/usr/bin/env python3
"""
合并 keep.txt + skip_words.txt，按语义归类，不删除任何一个词。
输出: output_v3/merged_keep.md (分类汇总)
"""
import re
import sys
import json
import os
from collections import defaultdict

# ── 读取文件 ──────────────────────────────────────────
def read_simple(path):
    """读纯文本，每行一个词"""
    words = []
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                w = line.strip()
                if w and not w.startswith('#'):
                    words.append(w)
    return words

def read_skip(path):
    """读取跳过词 - 支持两种格式:
    1. 纯文本: 每行一个词 (visual_filter.html 导出)
    2. 格式化: "   1. word  count  [source]" (extract_skip.py 旧格式)
    """
    words = []
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                # 尝试格式化匹配: "   1. word  ..."
                m = re.match(r'\s*\d+\.\s+(\S+)', line)
                if m:
                    words.append(m.group(1))
                else:
                    # 纯文本: 整行就是词
                    words.append(line)
    # 同时尝试 skip_words.json (visual_filter.html v3.4+ 导出的完整元数据)
    json_path = path.replace('.txt', '.json')
    if os.path.exists(json_path):
        with open(json_path, 'r', encoding='utf-8') as f:
            for c in json.load(f):
                w = c.get('word', '')
                if w and w not in words:
                    words.append(w)
    return words

# ── 读取 JSON 获取频次等元数据 ──────────────────────────
def load_meta(json_path):
    meta = {}
    if os.path.exists(json_path):
        with open(json_path, 'r', encoding='utf-8') as f:
            for c in json.load(f):
                meta[c['word']] = c
    return meta

# ── 分类规则 ──────────────────────────────────────────
# 格式: {类别名: [关键词列表]}
# 每个词命中多个类别时取第一个

CATEGORIES = {
    "🏫 学校/教育": [],
    "📍 地点/城市": [],
    "🍜 食物/饮品": [],
    "👤 人名/称呼": [],
    "🎮 游戏/娱乐": [],
    "💻 网络用语/梗": [],
    "🎵 谐音梗": [],
    "💕 情感/关系": [],
    "🏠 日常生活": [],
    "👗 服饰/物品": [],
    "🗣️ 日常用语/虚词": [],
    "🔤 英文缩写/拉丁词": [],
    "📋 其他中文词": [],
    "❓ 未分类": [],
}
# ═══════════════════════════════════════════════════════
# 加载外部分类数据 (如果存在)
# 将你的词汇表保存为 output_v3/categories.json，格式:
# { "类别名": ["词1", "词2", ...] }
# 运行 extract_words_v3.py 时会自动生成
# ═══════════════════════════════════════════════════════
import json as _json
_CAT_FILE = os.path.join(os.path.dirname(__file__) if '__file__' in dir() else '.', '..', 'output_v3', 'categories.json')
if os.path.exists(_CAT_FILE):
    with open(_CAT_FILE, 'r', encoding='utf-8') as _f:
        _loaded = _json.load(_f)
        for _cat, _words in _loaded.items():
            if _cat in CATEGORIES:
                CATEGORIES[_cat] = _words


# ── 主流程 ────────────────────────────────────────────
def main():
    base_dir = sys.argv[1] if len(sys.argv) > 1 else 'output_v3'

    keep_path = os.path.join(base_dir, 'keep.txt')
    skip_path = os.path.join(base_dir, 'skip_words.txt')
    json_path = os.path.join(base_dir, 'candidate_words.json')

    keep_words = read_simple(keep_path)
    skip_words = read_skip(skip_path)
    meta = load_meta(json_path)

    # 合并去重
    all_words = {}
    for w in keep_words:
        all_words[w] = all_words.get(w, 0) + 1
    for w in skip_words:
        all_words[w] = all_words.get(w, 0) + 1

    # 如果有重复（同时在keep和skip），标记
    duplicates = {w: c for w, c in all_words.items() if c > 1}

    all_unique = list(all_words.keys())
    print(f"keep.txt:   {len(keep_words)} 词")
    print(f"skip.txt:   {len(skip_words)} 词")
    print(f"合并去重后: {len(all_unique)} 词")
    if duplicates:
        print(f"重复词:     {len(duplicates)} 个 ({', '.join(duplicates.keys())})")

    # ── 归类 ───────────────────────────────────────────
    categorized = defaultdict(list)
    uncategorized = []

    for word in all_unique:
        found = False
        for cat, keywords in CATEGORIES.items():
            if word in keywords:
                categorized[cat].append(word)
                found = True
                break
        if not found:
            uncategorized.append(word)

    # ── 未归类二次判断（模糊匹配）───────────────────────
    still_uncategorized = []
    for word in uncategorized:
        # 纯英文/数字 → 英文缩写
        if re.match(r'^[a-zA-Z0-9]+$', word):
            categorized["🔤 英文缩写/拉丁词"].append(word)
        # 包含"谐音"标记的 skip 词
        elif any(c['word'] == word and 'homophones' in c for c in [meta.get(word, {})]):
            categorized["🎵 谐音梗"].append(word)
        elif re.search(r'[一-鿿]', word):
            categorized["📋 其他中文词"].append(word)
        else:
            still_uncategorized.append(word)

    if still_uncategorized:
        categorized["❓ 未分类"] = still_uncategorized

    # ── 输出 Markdown ───────────────────────────────────
    out_path = os.path.join(base_dir, 'merged_keep.md')
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write("# 合并保留词表 (keep + skip)\n\n")
        f.write(f"- keep.txt: {len(keep_words)} 词\n")
        f.write(f"- skip.txt: {len(skip_words)} 词\n")
        f.write(f"- 合并去重: {len(all_unique)} 词\n")
        if duplicates:
            f.write(f"- ⚠️ 重复词 ({len(duplicates)} 个): {', '.join(duplicates.keys())}\n")
        f.write(f"- 归类数: {len(categorized)} 个类别\n\n")
        f.write("---\n\n")

        # 按类别输出，每类内按拼音排序
        for cat in sorted(categorized.keys()):
            words_in_cat = sorted(set(categorized[cat]), key=lambda w: w.lower())
            f.write(f"## {cat} ({len(words_in_cat)}词)\n\n")
            for w in words_in_cat:
                m = meta.get(w, {})
                count = m.get('count', '')
                src = m.get('source', '')
                reason = m.get('reason', '')
                # 来源标签
                src_tag = ''
                if src == 'ngram':
                    src_tag = '🔍'
                elif src == 'latin':
                    src_tag = '🔤'
                elif src == 'jieba':
                    src_tag = '📖'

                pun_info = ''
                if m.get('homophones'):
                    best = m['homophones'][0]
                    pun_info = f' → {best["word"]}'

                count_str = f'({count})' if count else ''
                f.write(f"- {w} {count_str} {src_tag}{pun_info}\n")
            f.write("\n")

    print(f"\n输出: {out_path}")

    # ── 同时输出纯文本合并版 ────────────────────────────
    merged_txt = os.path.join(base_dir, 'keep_merged.txt')
    with open(merged_txt, 'w', encoding='utf-8') as f:
        f.write("# keep + skip 合并，按类别排序\n")
        f.write(f"# 共 {len(all_unique)} 词（keep={len(keep_words)} + skip={len(skip_words)}）\n")
        for cat in sorted(categorized.keys()):
            f.write(f"\n# ==== {cat} ====\n")
            for w in sorted(set(categorized[cat]), key=lambda w: w.lower()):
                f.write(f"{w}\n")
    print(f"纯文本: {merged_txt}")

if __name__ == '__main__':
    main()
