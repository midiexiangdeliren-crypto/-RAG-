# -*- coding: utf-8 -*-
"""明日方舟剧情 md -> RAG 块切分（对白感知滑窗 + 上下文前缀）
方案依据: outputs/明日方舟RAG构筑方案.md 第三节/第四节
输入:  ../story/**/*.md (排除 index.md)
输出:  chunks.jsonl  每行一个 chunk dict
规则:  条目原子 = 对白行/字幕/段落/选择组(选择+连续分支标记)/分隔线/小标题
       目标 800 字, 硬上限 1100; 重叠 2-3 条普通条目(<=150 字); <1200 字整篇一块
"""
import os, re, json, sys, hashlib, collections, warnings

warnings.filterwarnings('ignore')
sys.stdout.reconfigure(encoding='utf-8')

HERE = os.path.dirname(os.path.abspath(__file__))
# 剧情目录: 优先用仓库自带的 story/; 若仓库放在 ArknightsGameData 内且 story 在上一级, 也能识别
_STORY_HERE = os.path.join(HERE, 'story')
STORY = _STORY_HERE if os.path.isdir(_STORY_HERE) else os.path.join(os.path.dirname(HERE), 'story')
OUT = os.path.join(HERE, 'chunks.jsonl')

TARGET, HARD, WHOLE = 800, 1100, 1200
OV_ITEMS, OV_CHARS = 3, 150

RE_DIALOG = re.compile(r'^\*\*(.+?)\*\*：(.*)$')            # 对白: **说话人**：文本
RE_CHOICE = re.compile(r'^\*\*【选择】\*\*')
RE_BRANCH = re.compile(r'^\*\*【(分支\d+|共同)】\*\*\s*$')

def parse_fm(txt):
    """返回 (fm_dict, 正文)"""
    if not txt.startswith('---'):
        return {}, txt
    end = txt.find('\n---', 3)
    if end < 0:
        return {}, txt
    body = txt[end + 4:]
    try:
        fm = json.loads('{' + txt[3:end].strip().rstrip(',') + '}')
    except ValueError:
        fm = {}
    return fm, body

def items_from(body):
    """正文 -> 条目流。选择组 = 选择行 + 紧随的分支/共同标记行"""
    items, choice_open = [], False
    for ln in body.splitlines():
        ln = ln.rstrip()
        if not ln.strip() or ln.startswith('# '):
            continue
        if ln == '---':
            items.append({'kind': 'sep', 'text': ''}); choice_open = False; continue
        if ln.startswith('## '):
            items.append({'kind': 'head', 'text': ln}); choice_open = False; continue
        if RE_CHOICE.match(ln):
            items.append({'kind': 'choice', 'text': ln}); choice_open = True; continue
        if choice_open and RE_BRANCH.match(ln):
            items[-1]['text'] += '\n' + ln; continue           # 并入选择组
        choice_open = False
        m = RE_DIALOG.match(ln)
        if m:
            items.append({'kind': 'dialog', 'speaker': m.group(1), 'text': ln}); continue
        if ln.startswith('> '):
            items.append({'kind': 'sub', 'text': ln}); continue
        items.append({'kind': 'para', 'text': ln})
    return items

PLAIN = ('dialog', 'sub', 'para')                              # 可做重叠的普通条目

def make_prefix(fm):
    code = '（%s）' % fm['code'] if fm.get('code') else ''
    parts = ['【明日方舟·%s】%s%s' % (fm.get('activity') or '未分类', fm.get('title') or '', code)]
    if fm.get('summary'):
        parts.append(fm['summary'][:150])
    parts.append('———以下为剧情片段———')
    return '\n'.join(parts)

def chunk_story(story_id, fm, body, whole_only=False):
    items = items_from(body)
    total = sum(len(it['text']) for it in items)
    prefix = make_prefix(fm)
    base = {'story_id': story_id, 'dir': fm.get('_dir', ''), 'type': fm.get('type') or '',
            'activity': fm.get('activity') or '', 'code': fm.get('code') or '',
            'title': fm.get('title') or '', 'prefix': prefix}
    if not items:
        return []
    if whole_only or total <= WHOLE:                           # 短篇整篇一块
        text = '\n'.join(it['text'] for it in items)
        return [dict(base, text=text, speakers=sorted({it.get('speaker', '') for it in items} - {''}),
                     n_chars=len(text), overlap=0, ord=0)]
    chunks, cur, cur_len, ordi = [], [], 0, 0
    head_ov = [0]                                              # 本块开头从上一块复制的条目数

    def flush(ov_items):
        nonlocal cur, cur_len, ordi
        if not cur:
            return
        text = '\n'.join(it['text'] for it in cur)
        chunks.append(dict(base, text=text,
                           speakers=sorted({it.get('speaker', '') for it in cur} - {''}),
                           n_chars=len(text), overlap=head_ov[0], ord=ordi))
        ordi += 1
        head_ov[0] = len(ov_items)                             # 传给下一块的开头重叠数
        cur, cur_len = list(ov_items), sum(len(it['text']) for it in ov_items)

    i = 0
    while i < len(items):
        it = items[i]
        is_cutpoint = it['kind'] in ('sep', 'head')            # 天然场景边界
        cur_len_it = len(it['text'])
        # 条目边界判定: 已达目标 / 即将超硬上限 / 分隔线场景断点
        if cur and (cur_len >= TARGET
                    or (cur_len + cur_len_it > HARD)
                    or (is_cutpoint and it['kind'] == 'sep' and cur_len >= 400)):
            ov, took, j = [], 0, len(cur) - 1                  # 从尾部取普通条目做重叠
            while j >= 0 and len(ov) < OV_ITEMS and took <= OV_CHARS:
                if cur[j]['kind'] not in PLAIN:
                    break
                ov.insert(0, cur[j]); took += len(cur[j]['text']); j -= 1
            flush(ov)
            if it['kind'] == 'sep':                            # 分隔线本身不入块
                i += 1; continue
        cur.append(it); cur_len += cur_len_it
        i += 1
    flush([])
    return chunks

def main():
    n_files, chunks = 0, []
    for dp, _, fs in os.walk(STORY):
        d = os.path.relpath(dp, STORY).replace(os.sep, '/')
        for fn in sorted(fs):
            if not fn.endswith('.md') or fn == 'index.md':
                continue
            fm, body = parse_fm(open(os.path.join(dp, fn), encoding='utf-8').read())
            fm['_dir'] = d
            story_id = fm.get('id') or (d + '/' + fn[:-3])
            chunks += chunk_story(story_id, fm, body)
            n_files += 1
    with open(OUT, 'w', encoding='utf-8', newline='\n') as f:
        for c in chunks:
            c['hash'] = hashlib.md5((c['prefix'] + '\x00' + c['text']).encode()).hexdigest()[:16]
            f.write(json.dumps(c, ensure_ascii=False) + '\n')

    lens = sorted(c['n_chars'] for c in chunks)
    n = len(lens)
    stat = collections.Counter()
    for c in chunks:
        stat['含选择组'] += '**【选择】**' in c['text']
        stat['超硬上限'] += c['n_chars'] > HARD + 50
        stat['含重叠'] += c['overlap'] > 0
    print('篇数: %d -> 块数: %d (%s)' % (n_files, n, OUT))
    print('块字数: p50=%d p90=%d p99=%d max=%d' % (lens[n // 2], lens[int(n * .9)],
                                                    lens[int(n * .99)], lens[-1]))
    print('含选择组块: %d | 含重叠块: %d | 超硬上限块: %d' % (stat['含选择组'], stat['含重叠'], stat['超硬上限']))

if __name__ == '__main__':
    main()
