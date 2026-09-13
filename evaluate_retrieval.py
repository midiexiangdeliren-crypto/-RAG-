# -*- coding: utf-8 -*-
"""检索评估: recall@5 / MRR, 评估集见 CASES
命中判定(宽松): top-k 中存在块满足 expect 任一字段
  dir  = 目录前缀匹配   code = 关卡号包含   title = 标题包含
"""
import os, sys, json, sqlite3, argparse, warnings
warnings.filterwarnings('ignore')
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
from embed_store import DB, BM25_PKL, FAISS_F, _client, tokenize

CASES = [  # (query, expect)  注: expect 键间 OR; 2026-09-13 经人工校准(诊断 top5 相关性后修正标注)
    ('博士在切尔诺伯格苏醒',            {'dir': '主线/黑暗时代·上'}),
    ('整合运动袭击切尔诺伯格 城内混乱',  {'dir': '主线/黑暗时代·上'}),
    ('0-11 突围 剧情经过',              {'code': '0-11'}),
    ('Ace 的牺牲',                     {'dir': '活动/巴别塔', 'code': '1-8'}),
    ('临光保护博士突围',                {'dir': '主线/黑暗时代·上', 'code': '0-11'}),
    ('浮士德和梅菲斯特撤退',            {'dir': '主线/局部坏死', 'code': '0-11'}),
    ('塔露拉对整合运动的控制',          {'dir': '主线/风暴瞭望'}),
    ('弑君者对乌萨斯的仇恨',            {'title': '弑君者'}),
    ('弑君者·欲摘其冠',                {'title': '弑君者·欲摘其冠'}),
    ('老鲤 三餐一日',                   {'title': '三餐一日'}),
    ('凯尔希与W对峙 11:15:38',         {'title': '11:15:38'}),
    ('可露希尔 找凯尔希',               {'title': '和光同尘'}),
    ('凯尔希 可以聊些别的',             {'title': '我无惧前路何往'}),
    ('瑭雅被神秘人拦住',                {'code': '17-8', 'title': '裂响'}),
    ('风波暗涌 剧情',                   {'title': '风波暗涌'}),
    ('从未怀疑，从未远离',              {'title': '从未怀疑'}),
    ('离解复合 结局',                   {'dir': '主线/离解复合'}),
    ('岁相 IW-9',                      {'code': 'IW-9'}),
    ('死结两端 PA-7',                   {'code': 'PA-7'}),
    ('坍塌 0-1 开局',                   {'code': '0-1', 'title': '坍塌'}),
    ('阿米娅 罗德岛的成立',             {'title': '被踩住的影子', 'dir': '活动/去咧嘴谷'}),
    ('博士 失忆的真相',                 {'code': '7-19', 'title': '我不曾怀揣希望'}),
    ('杜宾教官 救援平民',               {'title': '大流行', 'dir': '活动/午间逸话'}),
    ('米莎 城市里的孩子',               {'code': '2-3', 'dir': '主线/异卵同生'}),
    ('长夜临光 耀骑士',                 {'dir': '活动/长夜临光'}),
    ('孤星 克丽斯腾',                   {'dir': '活动/孤星'}),
    ('叙拉古 家族',                     {'dir': '活动/叙拉古人'}),
    ('崔林特尔梅之金 剧情',             {'dir': '活动/崔林特尔梅之金'}),
    ('慈悲灯塔 博士的选择',             {'dir': '主线/慈悲灯塔'}),
    ('怒号光明 萨米',                   {'dir': '主线/怒号光明'}),
]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--source', choices=['hybrid', 'vec', 'bm25'], default='hybrid')
    ap.add_argument('--k', type=int, default=5)
    args = ap.parse_args()

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    ids = json.load(open(os.path.join(HERE, 'faiss_ids.json')))
    vec_hits_all = {}
    if args.source in ('hybrid', 'vec'):
        import faiss
        cfg, cli = _client()
        idx = faiss.read_index(FAISS_F)
        for q, _ in CASES:
            qv = np.asarray(cli.embeddings.create(model=cfg['model'], input=[q]).data[0].embedding,
                            dtype=np.float32)
            qv /= (np.linalg.norm(qv) + 1e-12)
            D, I = idx.search(qv[None, :], 50)
            vec_hits_all[q] = [(ids[i], float(s)) for s, i in zip(D[0], I[0]) if i >= 0]
    bm_hits_all = {}
    if args.source in ('hybrid', 'bm25'):
        import jieba, pickle
        for w in open(os.path.join(HERE, 'dict_operators.txt'), encoding='utf-8').read().splitlines():
            jieba.add_word(w)
        bm25 = pickle.load(open(BM25_PKL, 'rb'))
        for q, _ in CASES:
            sc = bm25.get_scores(tokenize(q))
            top = sorted(range(len(sc)), key=lambda i: -sc[i])[:50]
            bm_hits_all[q] = [(i + 1, float(sc[i])) for i in top if sc[i] > 0]

    from retrieve import rrf_merge
    recall, rr, fails = 0, 0.0, []
    for q, exp in CASES:
        hits = (rrf_merge(vec_hits_all[q], bm_hits_all[q]) if args.source == 'hybrid'
                else vec_hits_all[q] if args.source == 'vec' else bm_hits_all[q])
        rank = None
        for r, (cid, _) in enumerate(hits, 1):
            if r > args.k:
                break
            row = con.execute('SELECT dir, code, title FROM chunks WHERE chunk_id=?', (cid,)).fetchone()
            ok = (('dir' in exp and row['dir'].startswith(exp['dir']))
                  or ('code' in exp and exp['code'] in (row['code'] or ''))
                  or ('title' in exp and exp['title'] in (row['title'] or '')))
            if ok:
                rank = r
                break
        if rank:
            recall += 1
            rr += 1.0 / rank
        else:
            fails.append(q)
    n = len(CASES)
    print('评估: %d 条 | 路线: %s' % (n, args.source))
    print('recall@%d = %.2f | MRR = %.3f' % (args.k, recall / n, rr / n))
    if fails:
        print('未命中(%d):' % len(fails))
        for q in fails:
            print('  -', q)

if __name__ == '__main__':
    main()
