#!/usr/bin/env python3
"""
词云环构建器 — 从 merged_keep.md + candidate_words.json 生成 ring_words.json
=====================================================================
可重复使用。当你修改 merged_keep.md 后，运行此脚本即可重新生成环数据。

用法:
    python scripts/build_ring.py

输入:
    output_v3/merged_keep.md        ← 你编辑的词表（可手动增删词）
    output_v3/candidate_words.json  ← v3 提取的权重源数据

输出:
    output_v3/ring_words.json       ← 网页环加载的数据

权重规则:
    - 在 candidate_words.json 中找到的词 → 使用实际频次/PMI/熵计算权重
    - 手动新增的词（不在 candidate 里） → 使用平均权重

字号映射:
    - 权重通过 power-curve 映射到 14~72px
    - 极大值被压缩，避免少数高频词垄断视觉

网页:
    用浏览器打开 scripts/word_ring.html → 加载 output_v3/ring_words.json
    或直接: python -m http.server 8000 → http://localhost:8000/scripts/word_ring.html
"""

import json
import re
import math
import os
import sys
from pathlib import Path

# Windows UTF-8 fix
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# ── 配置 ──────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
MERGED_MD = PROJECT_DIR / 'output_v3' / 'merged_keep.md'
CANDIDATE_JSON = PROJECT_DIR / 'output_v3' / 'candidate_words.json'
OUTPUT_JSON = PROJECT_DIR / 'output_v3' / 'ring_words.json'

FONT_MIN = 14
FONT_MAX = 72
POWER_CURVE = 0.55  # <1 压缩极端值，让分布更均匀


# ── 解析 merged_keep.md ───────────────────────────────
def parse_merged_md(path):
    r"""
    merged_keep.md 格式:
        * 词 \[keep]
        * 词 \[skip]
        * 词              (默认 keep)
        ## 类别标题        (跳过)
    返回: [(word, tag), ...]
    """
    words = []
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line.startswith('* '):
                continue
            content = line[2:]  # 去掉 '* '
            tag = 'keep'
            # md 里反斜杠是字面量
            if content.endswith(r' \[keep]'):
                content = content[:-8]
            elif content.endswith(r' \[skip]'):
                content = content[:-8]
                tag = 'skip'
            word = content.strip()
            if word:
                words.append((word, tag))
    return words


# ── 加载权重元数据 ─────────────────────────────────────
def load_meta(path):
    with open(path, 'r', encoding='utf-8') as f:
        candidates = json.load(f)
    return {c['word']: c for c in candidates}


def calc_weight(info):
    """综合权重: 频次(log) + PMI + 双端熵"""
    count = info.get('count', 1)
    pmi = info.get('pmi', 0) or 0
    le = info.get('left_entropy', 0) or 0
    re = info.get('right_entropy', 0) or 0
    return round(math.log2(count + 1) * 5 + pmi * 3 + (le + re) * 2, 1)


def font_size(weight, w_min, w_max):
    """权重 → 字号 (power curve)"""
    if w_max == w_min:
        return (FONT_MIN + FONT_MAX) / 2
    ratio = (weight - w_min) / (w_max - w_min)
    ratio = ratio ** POWER_CURVE
    return round(FONT_MIN + ratio * (FONT_MAX - FONT_MIN), 1)


# ── 主流程 ────────────────────────────────────────────
def main():
    if not MERGED_MD.exists():
        print(f"错误: 找不到 {MERGED_MD}")
        print("  请先运行 extract_words_v3.py 获取候选词，然后编辑 merged_keep.md")
        return

    if not CANDIDATE_JSON.exists():
        print(f"错误: 找不到 {CANDIDATE_JSON}")
        return

    # 1. 解析词表
    word_list = parse_merged_md(MERGED_MD)
    print(f"📋 解析 merged_keep.md → {len(word_list)} 个词")

    # 2. 加载权重
    meta = load_meta(CANDIDATE_JSON)
    print(f"📊 加载权重数据 → {len(meta)} 条记录")

    # 3. 匹配 & 计算
    results = []
    found_weights = []

    for word, tag in word_list:
        info = meta.get(word)
        if info:
            w = calc_weight(info)
            found_weights.append(w)
            results.append({
                'word': word, 'tag': tag,
                'count': info.get('count', 0),
                'weight': w,
                'pmi': info.get('pmi'),
                'left_entropy': info.get('left_entropy'),
                'right_entropy': info.get('right_entropy'),
                'source': info.get('source', ''),
                'reason': info.get('reason', ''),
                'homophone': info['homophones'][0]['word'] if info.get('homophones') else None,
            })
        else:
            results.append({
                'word': word, 'tag': tag,
                'count': 1,
                'weight': None,  # 稍后填
                'source': 'manual',
                'reason': '手动新增',
            })

    # 4. 新增词用平均权重
    avg_weight = sum(found_weights) / len(found_weights) if found_weights else 30.0
    new_count = 0
    for r in results:
        if r['weight'] is None:
            r['weight'] = round(avg_weight, 1)
            new_count += 1

    # 5. 计算字号
    weights = [r['weight'] for r in results]
    w_min, w_max = min(weights), max(weights)
    for r in results:
        r['fontSize'] = font_size(r['weight'], w_min, w_max)

    # 6. 输出
    os.makedirs(OUTPUT_JSON.parent, exist_ok=True)
    with open(OUTPUT_JSON, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    # ── 统计 ──────────────────────────────────────────
    fs_vals = [r['fontSize'] for r in results]
    keep_n = sum(1 for r in results if r['tag'] == 'keep')
    skip_n = sum(1 for r in results if r['tag'] == 'skip')
    src_dist = {}
    for r in results:
        src_dist[r['source']] = src_dist.get(r['source'], 0) + 1

    print()
    print(f"✅ 生成 ring_words.json → {len(results)} 个词")
    print(f"   保留 [keep]: {keep_n}  |  跳过 [skip]: {skip_n}")
    print(f"   权重范围: {w_min:.1f} ~ {w_max:.1f} (均值 {sum(weights)/len(weights):.1f})")
    print(f"   字号范围: {min(fs_vals):.0f}px ~ {max(fs_vals):.0f}px")
    print(f"   来源分布: {src_dist}")
    print(f"   手动新增: {new_count} 个 (使用平均权重 {avg_weight:.1f})")
    print()
    print("Top 10 (权重最高):")
    top10 = sorted(results, key=lambda x: x['weight'], reverse=True)[:10]
    for r in top10:
        print(f"  {r['word']:<14s}  count={r['count']:<7d}  weight={r['weight']:<7.1f}  font={r['fontSize']}px")
    print()
    print("→ 下一步:")
    print("   python -m http.server 8000")
    print("   浏览器打开 http://localhost:8000/scripts/word_ring.html")
    print(f"  或将 {OUTPUT_JSON} 拖入 word_ring.html")


if __name__ == '__main__':
    main()
