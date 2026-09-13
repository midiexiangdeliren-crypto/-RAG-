# -*- coding: utf-8 -*-
"""明日方舟剧情 RAG 检索
用法:
  uv run python retrieve.py "查询词"                     # 混合检索(向量+BM25 RRF)
  uv run python retrieve.py "查询" --source bm25         # 只走 BM25(无需 key)
  uv run python retrieve.py "查询" --top 8 --type 主线    # 过滤
  uv run python retrieve.py "查询" --speaker 凯尔希       # 只搜某干员台词
  uv run python retrieve.py "查询" --full                # 输出整块正文(喂 LLM 用)
"""
import os, sys, json, sqlite3, pickle, argparse, warnings
warnings.filterwarnings('ignore')
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
from embed_store import DB, BM25_PKL, FAISS_F, _client, tokenize

def rrf_merge(*hit_lists, k=60):
    s = {}
    for hits in hit_lists:
        for r, (cid, _) in enumerate(hits):
            s[cid] = s.get(cid, 0.0) + 1.0 / (k + r + 1)
    return sorted(s.items(), key=lambda x: -x[1])

def search(query, source='hybrid', top=6, activity='', type_f='', speaker=''):
    """混合检索: 返回 [(sqlite.Row, 融合分)] 供 retrieve 展示与 ask 复用"""
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    n_rows = con.execute('SELECT COUNT(*) c FROM chunks').fetchone()['c']

    vec_hits, bm_hits = [], []
    if source in ('hybrid', 'vec'):
        import faiss
        cfg, cli = _client()
        qv = np.asarray(cli.embeddings.create(
            model=cfg['model'], input=[query]).data[0].embedding, dtype=np.float32)
        qv /= (np.linalg.norm(qv) + 1e-12)
        idx = faiss.read_index(FAISS_F)
        ids = json.load(open(os.path.join(HERE, 'faiss_ids.json')))
        D, I = idx.search(qv[None, :], 50)
        vec_hits = [(ids[i], float(s)) for s, i in zip(D[0], I[0]) if i >= 0]
    if source in ('hybrid', 'bm25'):
        import jieba
        for w in open(os.path.join(HERE, 'dict_operators.txt'), encoding='utf-8').read().splitlines():
            jieba.add_word(w)
        bm25 = pickle.load(open(BM25_PKL, 'rb'))
        assert n_rows == bm25.corpus_size, 'BM25 语料与 SQLite 不一致, 请重跑 build'
        sc = bm25.get_scores(tokenize(query))
        top50 = sorted(range(len(sc)), key=lambda i: -sc[i])[:50]
        bm_hits = [(i + 1, float(sc[i])) for i in top50 if sc[i] > 0]

    if source == 'vec':
        merged = vec_hits
    elif source == 'bm25':
        merged = bm_hits
    else:
        merged = rrf_merge(vec_hits, bm_hits)

    where, params = [], []
    if activity:
        where.append('dir LIKE ?'); params.append(activity + '%')
    if type_f:
        where.append('type = ?'); params.append(type_f)
    if speaker:
        where.append('speakers LIKE ?'); params.append('%"' + speaker + '"%')
    sql = ('SELECT chunk_id, story_id, dir, activity, type, code, title, text, n_chars, overlap '
           'FROM chunks' + (' WHERE ' + ' AND '.join(where) if where else ''))

    out, seen = [], set()
    for cid, score in merged:
        if len(out) >= top:
            break
        if cid in seen:
            continue
        r = con.execute(sql + (' AND chunk_id=?' if where else ' WHERE chunk_id=?'),
                        params + [cid]).fetchone()
        if r:
            out.append((r, score))
            seen.add(cid)
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('query')
    ap.add_argument('--top', type=int, default=6)
    ap.add_argument('--source', choices=['hybrid', 'vec', 'bm25'], default='hybrid')
    ap.add_argument('--activity', default='', help='按活动/主题目录过滤')
    ap.add_argument('--type', dest='type_f', default='', help='按类型过滤: 主线/活动剧情/小型活动/悖论模拟')
    ap.add_argument('--speaker', default='', help='只搜某说话人出现的块')
    ap.add_argument('--full', action='store_true', help='输出整块正文')
    args = ap.parse_args()

    shown = 0
    for r, score in search(args.query, args.source, args.top, args.activity, args.type_f, args.speaker):
        shown += 1
        code = '（%s）' % r['code'] if r['code'] else ''
        print('[#%d | %s | %s%s%s] hash分=%.4f' % (
            r['chunk_id'], r['dir'], r['title'], code,
            ' |+%d重叠' % r['overlap'] if r['overlap'] else '', score))
        if args.full:
            print(r['text'])
        else:
            t = r['text'].replace('\n', ' ')
            print('  ' + (t[:200] + '…' if len(t) > 200 else t))
        print()
    if not shown:
        print('无命中(或被过滤条件排除)')

if __name__ == '__main__':
    main()
