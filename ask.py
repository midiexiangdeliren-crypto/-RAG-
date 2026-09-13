# -*- coding: utf-8 -*-
"""明日方舟剧情 RAG 问答 (检索 + LLM 生成, 带来源引用)
用法:
  ARK_EMBED_API_KEY=<百炼key> uv run python ask.py "博士为什么会失忆"
  uv run python ask.py "弑君者是谁" --chat          # 多轮对话(检索每轮独立)
  uv run python ask.py "0-11 突围" --show-src       # 显示引用片段原文
  uv run python ask.py "..." --model qwen-max --top 8
生成模型默认 qwen-plus (与 embedding 同一 key/端点, 可 --model 换 qwen-max/qwen-flash)
"""
import os, sys, json, argparse, warnings
warnings.filterwarnings('ignore')
sys.stdout.reconfigure(encoding='utf-8')
if not sys.stdin.isatty():
    sys.stdin.reconfigure(encoding='utf-8', errors='replace')  # 管道输入按 UTF-8 读

HERE = os.path.dirname(os.path.abspath(__file__))
from embed_store import _client
from retrieve import search

SYS = ('你是《明日方舟》剧情问答助手。严格依据给定的剧情片段回答, 规则:\n'
       '1. 只使用片段中的信息, 不编造、不补充游戏外知识; 片段不足以回答时直接说明\n'
       '2. 回答中用 [n] 标注所依据的片段编号; 引用台词时可写「角色名：台词」原句\n'
       '3. 剧情有先后因果时按顺序讲清楚; 涉及多个片段时综合归纳\n'
       '4. 中文回答, 简洁分点')

def build_context(hits):
    parts, srcs = [], []
    for i, (r, score) in enumerate(hits, 1):
        code = '（%s）' % r['code'] if r['code'] else ''
        parts.append('[%d] %s｜%s%s\n%s' % (i, r['dir'], r['title'], code, r['text']))
        srcs.append('[%d] %s｜%s%s' % (i, r['dir'], r['title'], code))
    return '\n\n'.join(parts), srcs

def chat_client():
    import openai
    cfg = json.load(open(os.path.join(HERE, 'embed_config.json'), encoding='utf-8'))
    key = os.environ.get('ARK_EMBED_API_KEY')
    if not key:
        sys.exit('缺少环境变量 ARK_EMBED_API_KEY (百炼 key 同时用于 chat)')
    return openai.OpenAI(base_url=cfg['base_url'], api_key=key)

def ask(cli, model, question, hits, history):
    ctx, srcs = build_context(hits)
    user = '【剧情片段】\n%s\n\n【问题】%s' % (ctx, question)
    msgs = [{'role': 'system', 'content': SYS}] + history + [{'role': 'user', 'content': user}]
    stream = cli.chat.completions.create(model=model, messages=msgs, temperature=0.2, stream=True)
    print()
    buf = []
    for ev in stream:
        delta = ev.choices[0].delta.content if ev.choices else ''
        if delta:
            buf.append(delta)
            print(delta, end='', flush=True)
    print('\n')
    for s in srcs:
        print('  ' + s)
    print()
    return ''.join(buf)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('question', nargs='?')
    ap.add_argument('--chat', action='store_true', help='多轮对话模式 (exit 退出)')
    ap.add_argument('--model', default='qwen-plus')
    ap.add_argument('--top', type=int, default=6)
    ap.add_argument('--source', choices=['hybrid', 'vec', 'bm25'], default='hybrid')
    ap.add_argument('--activity', default='')
    ap.add_argument('--type', dest='type_f', default='')
    ap.add_argument('--speaker', default='')
    ap.add_argument('--show-src', action='store_true', help='回答前先打印引用片段')
    args = ap.parse_args()

    cli = chat_client()
    history = []
    while True:
        q = args.question if args.question else input('\n问> ').strip()
        if not q:
            continue
        if q.lower() in ('exit', 'quit', 'q') and not args.question:
            break
        hits = search(q, args.source, args.top, args.activity, args.type_f, args.speaker)
        if not hits:
            print('无相关剧情片段, 换个问法试试')
            if not args.chat:
                break
            continue
        if args.show_src:
            ctx, _ = build_context(hits)
            print(ctx + '\n' + '-' * 40)
        ans = ask(cli, args.model, q, hits, history)
        history += [{'role': 'user', 'content': q},
                    {'role': 'assistant', 'content': ans}]
        args.question = None          # 防止 --chat 下重复问同一问题
        if not args.chat:
            break

if __name__ == '__main__':
    main()
