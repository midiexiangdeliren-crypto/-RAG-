# -*- coding: utf-8 -*-
"""明日方舟剧情 RAG 问答服务 (FastAPI + 单页前端)
用法:
  uv run python server.py                 # 默认 http://127.0.0.1:8600 并自动开浏览器
  uv run python server.py --port 9000     # 换端口
  uv run python server.py --no-open       # 不自动开浏览器
说明:
  API key 由网页弹窗输入, 校验通过后只注入本服务进程内存(os.environ), 不落盘, 重启需重输。
  检索索引(faiss/bm25/sqlite)在首次提问时懒加载; 会话历史存进程内存, 每会话上限 4 轮。
"""
import os, sys, json, argparse, warnings, time, threading, webbrowser
warnings.filterwarnings('ignore')
sys.stdout.reconfigure(encoding='utf-8')

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
import sqlite3
from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from retrieve import search
from ask import SYS                      # 复用问答系统提示词
from embed_store import DB, _client      # _client 读取 os.environ 的 key

HIST_MAX = 8                             # 会话历史最多保留 8 条(4 轮)
STATE = {'key': None, 'filters': None, 'sessions': {}}

app = FastAPI(title='明日方舟剧情问答')


# ---------------- 数据结构 ----------------
class KeyBody(BaseModel):
    key: str

class AskBody(BaseModel):
    question: str
    session_id: str = ''
    model: str = 'qwen-plus'
    top: int = 6
    source: str = 'hybrid'               # hybrid / vec / bm25
    type_f: str = ''                     # 类型过滤
    speaker: str = ''                    # 说话人过滤
    activity: str = ''                   # 目录过滤(前缀)


# ---------------- 工具 ----------------
def require_key():
    if not STATE['key']:
        raise PermissionError('尚未设置 API key')

def build_sources(hits):
    srcs = []
    for i, (r, score) in enumerate(hits, 1):
        srcs.append({'i': i, 'dir': r['dir'], 'title': r['title'],
                     'code': r['code'] or '', 'text': r['text']})
    return srcs

def sse(obj):
    return 'data: ' + json.dumps(obj, ensure_ascii=False) + '\n\n'


# ---------------- 接口 ----------------
@app.get('/')
def index():
    return FileResponse(os.path.join(HERE, 'static', 'index.html'))

@app.get('/api/health')
def health():
    return {'key_set': bool(STATE['key'])}

@app.post('/api/key')
def set_key(b: KeyBody):
    key = b.key.strip()
    if not key:
        return {'ok': False, 'msg': 'key 为空'}
    cfg = json.load(open('embed_config.json', encoding='utf-8'))
    try:
        import openai
        cli = openai.OpenAI(base_url=cfg['base_url'], api_key=key, timeout=30, max_retries=1)
        r = cli.embeddings.create(model=cfg['model'], input=['连通性测试', '罗德岛'])
        dims = len(r.data[0].embedding)
        if dims != cfg.get('dims', dims):
            return {'ok': False, 'msg': '向量维度 %d 与索引 %d 不一致' % (dims, cfg.get('dims'))}
    except Exception as e:
        return {'ok': False, 'msg': 'key 校验失败: %s' % str(e)[:200]}
    os.environ['ARK_EMBED_API_KEY'] = key      # 仅本进程内存
    STATE['key'] = key
    print('[key] 校验通过, 已注入进程内存')
    return {'ok': True, 'model': cfg['model'], 'dims': dims}

@app.get('/api/filters')
def filters():
    require_key()                              # 索引数据仅对本机, key 只挡外部误用
    if STATE['filters']:
        return STATE['filters']
    con = sqlite3.connect('file:%s?mode=ro' % DB.replace('\\', '/'), uri=True)
    types = [r[0] for r in con.execute('SELECT DISTINCT type FROM chunks ORDER BY type')]
    dirs = [r[0] for r in con.execute('SELECT DISTINCT dir FROM chunks ORDER BY dir')]
    cnt = {}
    for (s,) in con.execute("SELECT speakers FROM chunks WHERE speakers != ''"):
        for name in json.loads(s):
            cnt[name] = cnt.get(name, 0) + 1
    con.close()
    speakers = [n for n, _ in sorted(cnt.items(), key=lambda x: -x[1])]
    STATE['filters'] = {'types': types, 'dirs': dirs, 'speakers': speakers}
    return STATE['filters']

@app.post('/api/ask')
def ask(b: AskBody):
    require_key()
    sid = b.session_id or 'default'
    hist = STATE['sessions'].get(sid, [])

    def gen():
        nonlocal hist               # gen 内 += 需显式引用外层会话历史
        try:
            hits = search(b.question, b.source, b.top, b.activity, b.type_f, b.speaker)
        except AssertionError as e:
            yield sse({'type': 'err', 'msg': '索引不一致: %s' % e}); return
        except Exception as e:
            yield sse({'type': 'err', 'msg': '检索失败: %s' % str(e)[:200]}); return
        if not hits:
            yield sse({'type': 'empty', 'msg': '无相关剧情片段, 换个问法试试'}); return

        srcs = build_sources(hits)
        yield sse({'type': 'sources', 'sources': srcs})
        ctx = '\n\n'.join('[%d] %s｜%s（%s）\n%s' % (s['i'], s['dir'], s['title'], s['code'], s['text'])
                          for s in srcs)
        msgs = [{'role': 'system', 'content': SYS}] + hist + [
            {'role': 'user', 'content': '【剧情片段】\n%s\n\n【问题】%s' % (ctx, b.question)}]
        try:
            cli = _client()[1]      # _client() 返回 (cfg, cli)
            stream = cli.chat.completions.create(
                model=b.model, messages=msgs, temperature=0.2, stream=True)
        except Exception as e:
            yield sse({'type': 'err', 'msg': '调用模型失败: %s' % str(e)[:200]}); return
        buf = []
        try:
            for ev in stream:
                if not ev.choices:
                    continue
                delta = ev.choices[0].delta.content or ''
                if delta:
                    buf.append(delta)
                    yield sse({'type': 't', 'v': delta})
        except Exception as e:
            yield sse({'type': 'err', 'msg': '生成中断: %s' % str(e)[:200]})
        answer = ''.join(buf)
        hist += [{'role': 'user', 'content': b.question},
                 {'role': 'assistant', 'content': answer}]
        del hist[:-HIST_MAX]                   # 控制上下文长度
        STATE['sessions'][sid] = hist
        yield sse({'type': 'done'})

    return StreamingResponse(gen(), media_type='text/event-stream',
                             headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


# ---------------- 启动 ----------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=8600)
    ap.add_argument('--no-open', dest='no_open', action='store_true')
    args = ap.parse_args()
    url = 'http://127.0.0.1:%d' % args.port
    if not args.no_open:
        t = threading.Timer(1.5, lambda: webbrowser.open(url))
        t.daemon = True
        t.start()
    print('剧情问答服务: %s  (Ctrl+C 停止)' % url)
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=args.port, log_level='warning')

if __name__ == '__main__':
    main()
