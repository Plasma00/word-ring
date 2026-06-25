#!/usr/bin/env python3
"""
词云环 Web 后端 — 三阶段管道服务器
====================================
启动后浏览器打开 http://localhost:5000 即可使用三阶段管道：
  1. 上传聊天记录 → 提取候选词
  2. 人工筛选 → 自定义词语
  3. 构建词环 → 可视化 + 下载

用法:
    pip install flask
    python app.py
    # → http://localhost:5000
"""

import sys
import os
import json
import uuid
import subprocess
import math
import threading
from pathlib import Path

# Windows UTF-8 fix
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from flask import Flask, request, jsonify, Response, stream_with_context, send_from_directory

# ── 路径配置 ──────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent / 'scripts'
PROJECT_DIR = Path(__file__).parent
FILES_DIR = PROJECT_DIR / 'Files'
UPLOAD_DIR = FILES_DIR / 'uploads'
FRONTEND_DIR = PROJECT_DIR / 'frontend'

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# ── Flask 应用 ───────────────────────────────────────
app = Flask(__name__, static_folder=None)

# Session storage (in-memory — sessions survive until server restart)
sessions = {}
sessions_lock = threading.Lock()


@app.route('/')
def index():
    """返回三阶段前端页面"""
    return send_from_directory(str(FRONTEND_DIR), 'app.html', mimetype='text/html')


@app.route('/api/health')
def health():
    """健康检查"""
    return jsonify({'status': 'ok', 'version': '4.0'})


# ═══════════════════════════════════════════════════════
#  POST /api/extract  — 上传聊天文件 + 参数 → SSE 流式提取
# ═══════════════════════════════════════════════════════
@app.route('/api/extract', methods=['POST'])
def extract():
    """
    multipart/form-data:
        file:  聊天记录 .md 文件
        params: JSON 字符串，提取参数（全部可选，有默认值）
    """
    if 'file' not in request.files:
        return jsonify({'error': '未提供文件'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': '文件名为空'}), 400

    # 解析参数
    params = {}
    if 'params' in request.form:
        try:
            params = json.loads(request.form['params'])
        except json.JSONDecodeError as e:
            return jsonify({'error': f'参数 JSON 解析失败: {e}'}), 400

    # 生成 session
    session_id = uuid.uuid4().hex[:12]
    session_dir = UPLOAD_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    # 保存上传文件
    chat_path = session_dir / 'chat_history.md'
    file.save(str(chat_path))

    # 构建命令行参数
    extract_script = SCRIPT_DIR / 'extract_words.py'
    if not extract_script.exists():
        return jsonify({'error': 'extract_words.py 未找到'}), 500

    cmd = [
        sys.executable, '-u', str(extract_script),
        str(chat_path),
        '--output-dir', str(session_dir),
        '--max-candidates', str(params.get('max_candidates', 1500)),
        '--pmi-min', str(params.get('pmi_min', 3.0)),
        '--entropy-min', str(params.get('entropy_min', 1.0)),
        '--ngram-min', str(params.get('ngram_min', 2)),
        '--ngram-max', str(params.get('ngram_max', 5)),
        '--jieba-multiplier', str(params.get('jieba_multiplier', 0.6)),
        '--not-in-dict-bonus', str(params.get('not_in_dict_bonus', 15)),
        '--latin-quota', str(params.get('latin_quota', 50)),
    ]

    if params.get('no_pinyin'):
        cmd.append('--no-pinyin')
    if params.get('sender'):
        cmd.extend(['--sender', params['sender']])

    # 存储 session 信息
    with sessions_lock:
        sessions[session_id] = {
            'status': 'running',
            'dir': str(session_dir),
            'chat_file': str(chat_path),
        }

    def generate():
        """SSE 事件流生成器"""
        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
                errors='replace',
                env={
                    **os.environ,
                    'PYTHONUNBUFFERED': '1',
                    'PYTHONIOENCODING': 'utf-8',
                },
                cwd=str(PROJECT_DIR),
            )

            # 逐行读取 stdout，推送给前端
            for line in iter(process.stdout.readline, ''):
                if line.strip():
                    yield f"data: {json.dumps({'type': 'progress', 'text': line.strip()}, ensure_ascii=False)}\n\n"

            process.wait()

            # 读取输出
            candidates_path = session_dir / 'candidate_words.json'
            if process.returncode == 0 and candidates_path.exists():
                with open(candidates_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                with sessions_lock:
                    if session_id in sessions:
                        sessions[session_id]['status'] = 'done'
                        sessions[session_id]['count'] = len(data)
                        sessions[session_id]['candidates'] = data

                yield f"data: {json.dumps({'type': 'complete', 'count': len(data), 'session_id': session_id}, ensure_ascii=False)}\n\n"

            elif process.returncode != 0:
                with sessions_lock:
                    if session_id in sessions:
                        sessions[session_id]['status'] = 'error'
                yield f"data: {json.dumps({'type': 'error', 'message': f'提取脚本异常退出 (code={process.returncode})'}, ensure_ascii=False)}\n\n"

            else:
                with sessions_lock:
                    if session_id in sessions:
                        sessions[session_id]['status'] = 'error'
                yield f"data: {json.dumps({'type': 'error', 'message': '提取完成但未生成输出文件'}, ensure_ascii=False)}\n\n"

        except Exception as e:
            with sessions_lock:
                if session_id in sessions:
                    sessions[session_id]['status'] = 'error'
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no',
        }
    )


# ═══════════════════════════════════════════════════════
#  GET /api/session/<session_id>/candidates
# ═══════════════════════════════════════════════════════
@app.route('/api/session/<session_id>/candidates')
def get_candidates(session_id):
    """获取提取完成的候选词数据（不通过 SSE 传输大 JSON）"""
    with sessions_lock:
        session = sessions.get(session_id)

    print(f"[DEBUG] get_candidates: {session_id=} found={session is not None} status={session.get('status') if session else 'N/A'} has_candidates={'candidates' in session if session else False}")

    if not session:
        return jsonify({'error': 'Session 不存在或已过期'}), 404

    if session['status'] == 'running':
        return jsonify({'error': '提取仍在进行中'}), 202

    if session['status'] == 'error':
        return jsonify({'error': '提取失败'}), 500

    if 'candidates' in session:
        return jsonify({
            'session_id': session_id,
            'count': session['count'],
            'candidates': session['candidates'],
        })

    # 尝试从文件读取
    candidates_path = Path(session['dir']) / 'candidate_words.json'
    if candidates_path.exists():
        with open(candidates_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return jsonify({
            'session_id': session_id,
            'count': len(data),
            'candidates': data,
        })

    return jsonify({'error': '找不到候选词数据'}), 500


# ═══════════════════════════════════════════════════════
#  POST /api/build-ring  — 构建词环数据
# ═══════════════════════════════════════════════════════
@app.route('/api/build-ring', methods=['POST'])
def build_ring():
    """
    JSON body:
        { "keep_words": [...] }
    返回:
        { "ring_words": [...], "stats": {...} }
    """
    body = request.get_json(silent=True)
    if not body or 'keep_words' not in body:
        return jsonify({'error': '缺少 keep_words 字段'}), 400

    keep_words = body['keep_words']
    if not isinstance(keep_words, list) or len(keep_words) == 0:
        return jsonify({'error': 'keep_words 为空'}), 400

    # ── 内联权重计算（直接复用 build_ring.py 的算法） ──
    FONT_MIN = 14
    FONT_MAX = 72
    POWER_CURVE = 0.55

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
        ratio = ratio ** POWER_CURVE
        return round(FONT_MIN + ratio * (FONT_MAX - FONT_MIN), 1)

    # 计算权重
    results = []
    for entry in keep_words:
        w = calc_weight(entry)
        results.append({
            'word': entry['word'],
            'tag': 'keep',
            'count': entry.get('count', 0),
            'weight': w,
            'pmi': entry.get('pmi'),
            'left_entropy': entry.get('left_entropy'),
            'right_entropy': entry.get('right_entropy'),
            'source': entry.get('source', ''),
            'reason': entry.get('reason', ''),
            'homophone': entry['homophones'][0]['word'] if entry.get('homophones') else None,
        })

    # 计算字号
    weights = [r['weight'] for r in results]
    w_min, w_max = min(weights), max(weights)
    for r in results:
        r['fontSize'] = font_size(r['weight'], w_min, w_max)

    # 统计
    fs_vals = [r['fontSize'] for r in results]
    src_dist = {}
    for r in results:
        src_dist[r['source']] = src_dist.get(r['source'], 0) + 1

    return jsonify({
        'ring_words': results,
        'stats': {
            'total': len(results),
            'weight_min': round(w_min, 1),
            'weight_max': round(w_max, 1),
            'weight_avg': round(sum(weights) / len(weights), 1),
            'font_min': round(min(fs_vals), 1),
            'font_max': round(max(fs_vals), 1),
            'source_dist': src_dist,
        }
    })


# ── 启动 ─────────────────────────────────────────────
if __name__ == '__main__':
    print("""
╔══════════════════════════════════════════════════════╗
║         词云环 Web 后端  v4.0                         ║
╠══════════════════════════════════════════════════════╣
║                                                      ║
║  浏览器打开:  http://localhost:5000                   ║
║                                                      ║
║  三阶段管道:                                          ║
║    ① 上传聊天记录 → 提取候选词                        ║
║    ② 人工筛选 → 自定义词语                            ║
║    ③ 构建词环 → 可视化 + 下载                         ║
║                                                      ║
║  按 Ctrl+C 停止服务器                                 ║
╚══════════════════════════════════════════════════════╝
""")
    app.run(host='0.0.0.0', port=5000, debug=True, threaded=True)
