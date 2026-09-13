# -*- coding: utf-8 -*-
"""明日方舟剧情 RAG 入库
build:  chunks.jsonl -> SQLite(元数据+正文) + jieba自定义词表 + BM25 索引   (无需 key)
embed:  缺向量的块调 API embedding -> 写回 SQLite -> 重建 faiss IndexFlatIP  (需要 key)

配置 rag/embed_config.json: {"base_url": "...", "model": "...", "dims": 1024}
key 走环境变量 ARK_EMBED_API_KEY (不落盘)
"""
import os, json, sys, sqlite3, pickle, warnings, time

warnings.filterwarnings('ignore')
sys.stdout.reconfigure(encoding='utf-8')
import jieba
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
STORY_XLSX = os.path.join(os.path.dirname(HERE), 'zh_CN', 'gamedata', 'excel')
DB = os.path.join(HERE, 'chunks.sqlite')
BM25_PKL = os.path.join(HERE, 'bm25.pkl')
FAISS_F = os.path.join(HERE, 'vectors.faiss')
CONFIG = os.path.join(HERE, 'embed_config.json')
CACHE_BATCH = 64

DDL = '''CREATE TABLE IF NOT EXISTS chunks(
  chunk_id INTEGER PRIMARY KEY, hash TEXT, story_id TEXT, dir TEXT,
  activity TEXT, type TEXT, code TEXT, title TEXT, prefix TEXT, text TEXT,
  speakers TEXT, n_chars INTEGER, overlap INTEGER, ord INTEGER, vector BLOB)'''

def build_operator_dict():
    """干员名 -> jieba 自定义词表(提升 BM25 专名切分)。
    优先加载仓库自带的 dict_operators.txt; 缺失时才从 gamedata 的 character_table.json 重新生成。"""
    p = os.path.join(HERE, 'dict_operators.txt')
    if os.path.exists(p):
        names = [w for w in open(p, encoding='utf-8').read().splitlines() if w.strip()]
    else:
        ct = json.load(open(os.path.join(STORY_XLSX, 'character_table.json'), encoding='utf-8'))
        names = sorted({c.get('name', '') for c in ct.values() if c.get('name')})
        with open(p, 'w', encoding='utf-8') as f:
            f.write('\n'.join(names))
    for w in names:
        jieba.add_word(w)
    return len(names)

def tokenize(s):
    return [w for w in jieba.cut(s) if w.strip()]

def cmd_build():
    n = build_operator_dict()
    print('jieba 自定义词表: %d 个干员名' % n)
    rows = [json.loads(l) for l in open(os.path.join(HERE, 'chunks.jsonl'), encoding='utf-8')]
    if os.path.exists(DB):
        os.remove(DB)
    con = sqlite3.connect(DB)
    con.execute(DDL)
    con.executemany('INSERT INTO chunks(hash,story_id,dir,activity,type,code,title,'
                    'prefix,text,speakers,n_chars,overlap,ord) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    [(r['hash'], r['story_id'], r['dir'], r['activity'], r['type'], r['code'],
                      r['title'], r['prefix'], r['text'], json.dumps(r['speakers'], ensure_ascii=False),
                      r['n_chars'], r['overlap'], r['ord']) for r in rows])
    con.commit()
    print('SQLite: %d 块 -> %s' % (len(rows), DB))
    corpus = tokenize('\x00'.join([r['prefix'], r['text']])) if False else None  # noqa
    corpus = [tokenize(r['prefix'] + '\n' + r['text']) for r in rows]
    from rank_bm25 import BM25Okapi
    bm25 = BM25Okapi(corpus)
    with open(BM25_PKL, 'wb') as f:
        pickle.dump(bm25, f)
    print('BM25 索引: %d 文档 -> %s' % (len(corpus), BM25_PKL))

def _client():
    import openai
    cfg = json.load(open(CONFIG, encoding='utf-8'))
    key = os.environ.get('ARK_EMBED_API_KEY')
    if not key:
        sys.exit('缺少环境变量 ARK_EMBED_API_KEY')
    return cfg, openai.OpenAI(base_url=cfg['base_url'], api_key=key)

def embed_texts(cli, model, texts):
    for attempt in range(6):
        try:
            resp = cli.embeddings.create(model=model, input=texts)
            return [d.embedding for d in resp.data]
        except Exception as e:
            wait = min(2 ** attempt * 2, 60)
            print('  重试 %d (%s), %ds 后再试' % (attempt + 1, str(e)[:80], wait))
            time.sleep(wait)
    raise RuntimeError('embedding 连续失败')

def cmd_embed(reindex=False):
    con = sqlite3.connect(DB)
    cfg, cli = _client()
    dims = cfg['dims']
    batch_n = cfg.get('batch', 64)                             # 百炼 v4 上限 10 条/批
    if reindex:
        rows = con.execute('SELECT chunk_id, vector FROM chunks WHERE vector IS NOT NULL').fetchall()
    else:
        todo = con.execute('SELECT COUNT(*) FROM chunks WHERE vector IS NULL').fetchone()[0]
        if not todo:
            print('全部 %d 块已有向量, 仅重建 faiss' %
                  con.execute('SELECT COUNT(*) FROM chunks WHERE vector IS NOT NULL').fetchone()[0])
            rows = con.execute('SELECT chunk_id, vector FROM chunks WHERE vector IS NOT NULL').fetchall()
        else:
            print('待 embedding: %d 块 | 模型: %s @ %s' % (todo, cfg['model'], cfg['base_url']))
            known = {h: v for h, v in con.execute(
                'SELECT hash, vector FROM chunks WHERE vector IS NOT NULL').fetchall()}
            ids_texts = con.execute(
                'SELECT chunk_id, prefix, text, hash FROM chunks WHERE vector IS NULL ORDER BY chunk_id'
            ).fetchall()
            reuse = [(cid, known[h]) for cid, _, _, h in ids_texts if h in known]
            if reuse:
                con.executemany('UPDATE chunks SET vector=? WHERE chunk_id=?', reuse)
                con.commit()
                print('  按内容 hash 复用已有向量: %d 块(免调 API)' % len(reuse))
            ids_texts = [(cid, p, t) for cid, p, t, h in ids_texts if h not in known]
            done = 0
            for i in range(0, len(ids_texts), batch_n):
                batch = ids_texts[i:i + batch_n]
                texts = [p + '\n' + t for _, p, t in batch]
                vecs = embed_texts(cli, cfg['model'], texts)
                con.executemany('UPDATE chunks SET vector=? WHERE chunk_id=?',
                                [(np.asarray(v, dtype=np.float32).tobytes(), cid) for (cid, _, _), v in zip(batch, vecs)])
                con.commit()
                done += len(batch)
                print('\r  %d/%d' % (done, len(ids_texts)), end='', flush=True)
            print()
            rows = con.execute('SELECT chunk_id, vector FROM chunks WHERE vector IS NOT NULL').fetchall()
    import faiss
    mat = np.zeros((len(rows), dims), dtype=np.float32)
    order = []
    for i, (cid, blob) in enumerate(rows):
        v = np.frombuffer(blob, dtype=np.float32)
        mat[i] = v
        order.append(cid)
    faiss.normalize_L2(mat)
    idx = faiss.IndexFlatIP(dims)
    idx.add(mat)
    faiss.write_index(idx, FAISS_F)
    with open(os.path.join(HERE, 'faiss_ids.json'), 'w') as f:
        json.dump(order, f)
    print('faiss: %d x %d -> %s' % (idx.ntotal, dims, FAISS_F))

if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else ''
    if cmd == 'build':
        cmd_build()
    elif cmd == 'embed':
        cmd_embed(reindex='--reindex' in sys.argv)
    else:
        print('用法: python embed_store.py build | embed [--reindex]')
