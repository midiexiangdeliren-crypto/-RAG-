# -*- coding: utf-8 -*-
"""诊断未命中案例: 打印 top5 实际检索结果供人工判断"""
import os, sys, json, sqlite3, warnings
warnings.filterwarnings('ignore')
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np
import pickle, jieba

HERE = os.path.dirname(os.path.abspath(__file__))
from embed_store import DB, BM25_PKL, FAISS_F, _client, tokenize
from retrieve import rrf_merge

QUERIES = ['Ace 的牺牲', '浮士德和梅菲斯特撤退', '塔露拉对整合运动的控制', '瑭雅被神秘人拦住',
           '阿米娅 罗德岛的成立', '博士 失忆的真相', '杜宾教官 救援平民', '米莎 城市里的孩子']

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
cfg, cli = _client()
idx = __import__('faiss').read_index(FAISS_F)
ids = json.load(open(os.path.join(HERE, 'faiss_ids.json')))
for w in open(os.path.join(HERE, 'dict_operators.txt'), encoding='utf-8').read().splitlines():
    jieba.add_word(w)
bm25 = pickle.load(open(BM25_PKL, 'rb'))

for q in QUERIES:
    qv = np.asarray(cli.embeddings.create(model=cfg['model'], input=[q]).data[0].embedding, dtype=np.float32)
    qv /= (np.linalg.norm(qv) + 1e-12)
    D, I = idx.search(qv[None, :], 50)
    vh = [(ids[i], float(s)) for s, i in zip(D[0], I[0]) if i >= 0]
    sc = bm25.get_scores(tokenize(q))
    top = sorted(range(len(sc)), key=lambda i: -sc[i])[:50]
    bh = [(i + 1, float(sc[i])) for i in top if sc[i] > 0]
    print('Q: %s' % q)
    for r, (cid, s) in enumerate(rrf_merge(vh, bh)[:5], 1):
        row = con.execute('SELECT dir, code, title, text FROM chunks WHERE chunk_id=?', (cid,)).fetchone()
        t = row['text'].replace('\n', ' ')[:70]
        print('  %d. [%s|%s(%s)] %s' % (r, row['dir'], row['title'], row['code'], t))
    print()
