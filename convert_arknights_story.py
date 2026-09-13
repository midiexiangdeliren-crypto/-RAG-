# -*- coding: utf-8 -*-
"""明日方舟剧情脚本(.txt) -> Markdown 转换器
数据源: ArknightsGameData-master/zh_CN/gamedata/story
输出:   ArknightsGameData-master/story/<分类目录>/<标题>.md + index.md
"""
import os, re, json, sys, collections, argparse

sys.stdout.reconfigure(encoding='utf-8')
ap = argparse.ArgumentParser(description='明日方舟剧情脚本 -> Markdown 转换器')
ap.add_argument('--full', action='store_true', help='忽略用户删除记录，强制全量重新生成')
ap.add_argument('--reindex-only', action='store_true', help='只按现存文件重建 index.md，不转换')
ap.add_argument('--root', default=None,
                help='ArknightsGameData 根目录 (默认: 本脚本所在目录的上一级, 即本仓库需放在 ArknightsGameData 内)')
ARGS = ap.parse_args()

# 数据源根目录: 可用 --root 显式指定, 默认取脚本上一级
ROOT  = os.path.abspath(ARGS.root) if ARGS.root else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC   = os.path.join(ROOT, 'zh_CN', 'gamedata', 'story')
OUT   = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'story')
EXCEL = os.path.join(ROOT, 'zh_CN', 'gamedata', 'excel')

# ---------------- 元数据表 ----------------
review = json.load(open(os.path.join(EXCEL, 'story_review_table.json'), encoding='utf-8'))
title_map, act_info = {}, {}
for gid, g in review.items():
    act_info[gid] = {'name': g.get('name') or gid, 'actType': g.get('actType') or '',
                     'startTime': g.get('startTime') or 0}
    for it in g.get('infoUnlockDatas', []):
        txt = it.get('storyTxt')
        if txt:
            title_map[txt.lower()] = {'group': gid, 'actName': g.get('name') or gid,
                                      'actType': g.get('actType') or '',
                                      'startTime': g.get('startTime') or 0,
                                      'code': it.get('storyCode') or '',
                                      'storyName': it.get('storyName') or ''}

zone = json.load(open(os.path.join(EXCEL, 'zone_table.json'), encoding='utf-8'))
zone_name = {zid: (z.get('zoneNameSecond') or z.get('zoneNameFirst') or '')
             for zid, z in zone.get('zones', {}).items()}
stages = json.load(open(os.path.join(EXCEL, 'stage_table.json'), encoding='utf-8')).get('stages', {})

# actid -> 审阅组反查（a001 -> 1stact 等）
group_by_actid = {}
for key, h in title_map.items():
    if key.startswith('activities/'):
        aid = key.split('/')[1]
        group_by_actid.setdefault(aid, h['group'])

char_by_suffix = {}
for cid, c in json.load(open(os.path.join(EXCEL, 'character_table.json'), encoding='utf-8')).items():
    char_by_suffix.setdefault(cid.split('_')[-1], c.get('name') or '')

summaries = {}
uc_root = os.path.join(SRC, '[uc]info')
for dirpath, _, files in os.walk(uc_root):
    rel = os.path.relpath(dirpath, SRC).replace('\\', '/').lower()
    for fn in files:
        if fn.endswith('.txt'):
            key = (rel + '/' + fn[:-4].lower()).replace('[uc]info/', '', 1)
            summaries[key] = open(os.path.join(dirpath, fn), encoding='utf-8').read().strip()

# ---------------- 排除规则 ----------------
def exclude(rel_l, fn_l):
    segs = rel_l.split('/')
    if rel_l.startswith('obt/guide/') or 'guide' in segs or 'tutorial' in segs:
        return '新手引导'
    if 'training' in segs:               return '训练关'
    if 'ref' in segs:                    return '素材参考'
    if fn_l.startswith('tutorial_'):     return '教程'
    if fn_l.startswith('training_'):     return '训练'
    if fn_l.startswith('guide_'):        return '模式引导'
    if fn_l == 'avg_segment_report.txt': return '分析报告'
    if fn_l == 'tmp_test_visibility.txt':return '测试文件'
    return None

# ---------------- 行解析 ----------------
TAG_RE   = re.compile(r'^\[([A-Za-z_][A-Za-z_0-9]*)')
P_NAME  = re.compile(r'name="([^"]*)"')
P_TEXT  = re.compile(r'text="([^"]*)"')
P_OPTS  = re.compile(r'options="([^"]*)"')
P_REFS  = re.compile(r'references="([^"]*)"')
INLINE  = re.compile(r'<@[^>]*>|</>|<i>|</i>|<b>|</b>|<color=[^>]*>|</color>|<p=[^>]*>', re.I)

def split_tag(line):
    m = TAG_RE.match(line)
    if not m:
        return None
    tag = m.group(1); depth = 0; in_q = False
    for i in range(m.end() - 1, len(line)):
        ch = line[i]
        if ch == '"':
            in_q = not in_q
        elif not in_q and ch == '[':
            depth += 1
        elif not in_q and ch == ']':
            if depth == 0:
                return tag, line[m.end():i], line[i + 1:]
            depth -= 1
    return tag, line[m.end():], ''

def clean_text(t):
    t = INLINE.sub('', t)
    t = re.sub(r'(?:\{@nickname\}|@nickname)博士', '博士', t)   # 源文"{@nickname}博士"避免替换成"博士博士"
    t = t.replace('{@nickname}', '博士').replace('@nickname', '博士').replace('@nbs', ' ')
    return t.strip()

def parse_line(line, st):
    taginfo = split_tag(line)
    if taginfo is None:                        # 裸文本：旁白/场景/分支正文
        t = clean_text(line)
        if t:
            st['kept'] += 1
            return [t]
        return []
    tag, params, rest = taginfo
    tl = tag.lower()
    if tl == 'header':
        t = clean_text(rest)
        if t:
            st['header_title'] = t
        return []
    if tl in ('name', 'multiline'):            # 对白
        text = clean_text(rest)
        if not text:
            return []
        st['kept'] += 1
        m = P_NAME.search(params) or re.search(r'"([^"]*)"', params)
        return [f'**{m.group(1)}**：{text}'] if m else [text]
    if tl == 'subtitle':                       # 字幕旁白
        m = P_TEXT.search(params)
        text = clean_text(m.group(1) if m else rest)
        if text:
            st['kept'] += 1
            return ['> ' + text]
        return []
    if tl in ('narration', 'dialog'):          # 旁白/问答体
        text = clean_text(rest)
        if text:
            st['kept'] += 1
            return [text]
        return []
    if tl == 'decision':                       # 玩家选择
        m = P_OPTS.search(params)
        if m:
            opts = [clean_text(o) for o in m.group(1).split(';') if o.strip()]
            st['kept'] += 1
            return ['**【选择】** ' + ' ／ '.join(opts)]
        return []
    if tl == 'predicate':                      # 分支门槛
        m = P_REFS.search(params)
        if m:
            refs = [r for r in re.split('[;,]', m.group(1)) if r]
            st['kept'] += 1
            return ['**【分支%s】**' % refs[0]] if len(refs) == 1 else ['**【共同】**']
        return []
    if tl == 'div':
        return ['---']
    if tl == 'title':
        mt = P_TEXT.search(params)
        text = clean_text(rest) or (mt.group(1) if mt else '')
        if text:
            st['kept'] += 1
            return ['## ' + text]
        return []
    t = clean_text(rest)                       # 其余指令：有文字则保留
    if t:
        st['kept'] += 1
        return [t]
    st['dropped'][tag] += 1
    return []

# ---------------- 命名 ----------------
SAFE = re.compile(r'[\\/:*?"<>|]')
def sanitize(s):
    s = SAFE.sub('_', s).strip().rstrip('.').strip()
    if s.upper() in ('CON', 'PRN', 'AUX', 'NUL', *('COM%d' % i for i in range(1, 10)),
                     *('LPT%d' % i for i in range(1, 10))):
        s = '_' + s
    return s or 'untitled'

def natkey(s):
    return [int(t) if t.isdigit() else t for t in re.split(r'(\d+)', s)]

ACT_TYPE = {'ACTIVITY_STORY': '活动剧情', 'MINI_STORY': '小型活动',
            'MAIN_STORY': '主线', 'NONE': '活动剧情'}

def plan_for(rel_f, stem):
    """rel_f: 正斜杠相对路径(不含文件名)。
    返回 (目录, title, type, activity, code, group_key, sortkey, 章节序)"""
    hit = title_map.get((rel_f + '/' + stem).lower())
    segs = rel_f.split('/')
    top = segs[0] if segs else ''
    sub = segs[1] if len(segs) > 1 else ''
    NO_CODE = ['\uffff\uffff']                      # 无关卡码排最后
    flag = 0 if stem.endswith('_beg') else (1 if stem.endswith('_end') else 2)
    if top == 'obt' and sub == 'main':
        if hit:
            d, code, name, ty = '主线/' + hit['actName'], hit['code'], hit['storyName'], '主线'
            tnum = int(hit['group'].split('_')[1]) if hit['group'].startswith('main_') else 99
        else:
            m = re.match(r'level_main_(\d+)(?:-(\d+))?', stem)
            theme = 'main_' + m.group(1) if m else ''
            gk = theme if theme in act_info else 'obt:main'
            sg = (stages.get('main_%s-%s' % (m.group(1), m.group(2)), {})
                  if m and m.group(2) else {})
            tname = (act_info.get(gk, {}).get('name')
                     or zone_name.get(theme) or theme or '未知主题')
            d = '主线/' + tname
            code, name, ty = sg.get('code') or '', sg.get('name') or '', '主线'
            tnum = int(m.group(1)) if m else 99
        gk = hit['group'] if hit else gk
        sortk = (natkey(code) if code else NO_CODE, flag, natkey(stem))
        return d, (name or stem), ty, d.split('/')[-1], code, gk, sortk, tnum
    if top == 'obt' and sub == 'memory':
        m = re.match(r'story_([a-z0-9]+)_', stem)
        op = char_by_suffix.get(m.group(1), '') if m else ''
        sname = hit['storyName'] if hit and hit['storyName'] else stem
        title = (op + '·' + sname) if op else sname
        return '悖论模拟', title, '悖论模拟', '悖论模拟', '', 'obt/memory', (op, 0, natkey(stem)), None
    if top == 'obt' and sub == 'record':
        return '主线/主线情报记录', stem, '主线情报记录', '主线情报记录', '', 'obt/record', (natkey(stem), 0, ''), None
    if top == 'obt' and sub == 'rune':
        return '危机合约', stem, '危机合约', '危机合约', '', 'obt/rune', (natkey(stem), 0, ''), None
    if top == 'obt' and sub in ('rogue', 'roguelike'):
        return '集成战略', stem, '集成战略', '集成战略', '', 'obt:rogue', (natkey(stem), 0, ''), None
    if top == 'obt' and sub == 'sandboxperm':
        return '生息演算', stem, '生息演算', '生息演算', '', 'obt:' + sub, (natkey(stem), 0, ''), None
    if top == 'obt' and sub == 'legion':
        return '其他剧情', stem, '其他剧情', '其他', '', 'obt/legion', (natkey(stem), 0, ''), None
    if top == 'activities':
        actid = sub
        info = act_info.get(actid, {})
        if hit:
            d = '活动/' + hit['actName']
            code, name = hit['code'], hit['storyName']
            ty = ACT_TYPE.get(hit['actType'], '活动剧情')
            gk = hit['group']
        else:
            gk = group_by_actid.get(actid) or 'act:' + (actid or rel_f)
            d = '活动/' + (act_info.get(gk, {}).get('name') or info.get('name')
                           or actid or '未分类活动')
            code, name, ty = '', '', '活动剧情'
        NO_CODE = ['￿￿']
        sortk = (natkey(code) if code else NO_CODE, flag, natkey(stem))
        return d, (name or stem), ty, d.split('/')[-1], code, gk, sortk, None
    return rel_f or '未分类', stem, '其他', rel_f, '', 'src:' + rel_f, (natkey(stem), 0, ''), None

# ---------------- 收集文件 ----------------
stats_ex = collections.Counter()
jobs = []
for dirpath, _, files in os.walk(SRC):
    rel = os.path.relpath(dirpath, SRC).replace('\\', '/')
    if rel == '[uc]info' or rel.startswith('[uc]info/'):
        continue
    for fn in files:
        if not fn.endswith('.txt'):
            continue
        why = exclude(rel.lower(), fn.lower())
        if why:
            stats_ex[why] += 1
            continue
        src = os.path.join(dirpath, fn)
        if os.path.getsize(src) == 0:
            stats_ex['空文件'] += 1
            continue
        jobs.append((rel, fn, src))
jobs.sort(key=lambda j: (j[0], j[1]))          # 稳定顺序: beg 恒在 end 前

# ---------------- 主流程 ----------------
if ARGS.reindex_only:
    jobs = []                                # 跳过转换，仅重建索引

def load_keep_ids():
    """扫描 OUT 现存 md 的 frontmatter id（sync 白名单：用户删过的不再生成）"""
    ids = set()
    if not os.path.isdir(OUT):
        return ids
    for dirpath, _, files in os.walk(OUT):
        for fn in files:
            if not fn.endswith('.md') or fn == 'index.md':
                continue
            try:
                with open(os.path.join(dirpath, fn), encoding='utf-8') as f:
                    if f.readline().strip() != '---':
                        continue
                    buf = []
                    for ln in f:
                        if ln.strip() == '---':
                            break
                        buf.append(ln)
                    fm = json.loads('{' + ''.join(buf) + '}')
                    if fm.get('id'):
                        ids.add(fm['id'].lower())
            except (OSError, ValueError):
                continue
    return ids

KEEP_IDS = None if (ARGS.full or ARGS.reindex_only) else load_keep_ids()
if KEEP_IDS is not None and not KEEP_IDS:    # 无现存文件 = 首次全量
    KEEP_IDS = None

DIR_SEQ = {}          # 主线主题目录 -> 章节序号
used_dirs = {}        # 输出目录名 -> 组键
group_dir = {}        # 组键 -> 输出目录名
used_files = {}       # 输出目录 -> set(小写文件名)
rows = []
stats = collections.Counter()
dropped_tags = collections.Counter()
empty_out, no_title = [], []

for rel, fn, src in jobs:
    stem = fn[:-4]
    rel_id = rel + '/' + stem
    st = {'total': 0, 'kept': 0, 'dropped': dropped_tags, 'header_title': ''}
    blocks = []
    head = ''
    with open(src, encoding='utf-8') as f:
        for line in f:
            line = line.rstrip('\r\n')
            if not head and line.startswith('[HEADER'):
                head = line[:200]
            if not line.strip():
                continue
            blocks += parse_line(line, st)
    if 'is_tutorial=true' in head:          # 头部标记的引导文件
        stats_ex['教程标记'] += 1
        continue
    stats['total'] += 1
    stats['kept_lines'] += st['kept']
    if not blocks:                          # 纯演出无文本（entry 展示页等）不输出
        stats_ex['无文本内容'] += 1
        empty_out.append(rel_id)
        continue
    if KEEP_IDS is not None and rel_id.lower() not in KEEP_IDS:
        stats_ex['用户已删除'] += 1           # sync 防护：不复活用户删过的文件
        continue

    d, title, ty, act, code, gk, sortk, catnum = plan_for(rel, stem)
    hit = title_map.get(rel_id.lower())
    if not hit and st['header_title']:
        title = st['header_title']
    if not hit:
        no_title.append(rel_id)

    # 目录分配：同组同目录；不同组撞中文名时末段附组 ID 后缀
    d = '/'.join(sanitize(p) for p in d.split('/'))
    if gk in group_dir:
        d = group_dir[gk]
    else:
        if d in used_dirs and used_dirs[d] != gk:
            parent, _, last = d.rpartition('/')
            suffix = gk.split(':')[-1].replace('/', '_')
            cand = sanitize(last + '_' + suffix)
            cand = parent + '/' + cand if parent else cand
            while cand in used_dirs and used_dirs[cand] != gk:
                cand += '_x'
            d = cand
        used_dirs[d] = gk
        group_dir[gk] = d
    if catnum is not None and d not in DIR_SEQ:
        DIR_SEQ[d] = catnum

    # 文件名：有 code 用 code_标题，否则用标题
    base = sanitize(code + '_' + title if code and code not in title else title)
    taken = used_files.setdefault(d, set())
    if base.lower() in taken:
        suf = {'_beg': '前', '_end': '后'}.get(stem[-4:])
        cand = (base + '_' + suf) if suf and (base + '_' + suf).lower() not in taken else base + '_' + stem
        base = cand
    taken.add(base.lower())

    out_dir = os.path.join(OUT, d)
    summary = summaries.get(rel_id.lower(), '')
    os.makedirs(out_dir, exist_ok=True)
    fm = {'id': rel_id, 'title': title, 'type': ty, 'activity': act,
          'code': code or None, 'summary': summary or None}
    fm_txt = json.dumps(fm, ensure_ascii=False, indent=1)[1:-1]
    with open(os.path.join(out_dir, base + '.md'), 'w', encoding='utf-8', newline='\n') as f:
        f.write('---\n' + fm_txt + '\n---\n\n# ' + title + '\n\n' + '\n\n'.join(blocks) + '\n')
    rows.append({'dir': d, 'file': base, 'title': title, 'summary': summary,
                 'code': code, 'sort': sortk})

# ---------------- index.md ----------------
def cat_key(d):
    cat = d.split('/')[0]
    if cat == '主线':
        return (0, DIR_SEQ.get(d, 99), d)
    if cat == '活动':
        return (1, 0, d)
    return (2, 0, d)

def scan_rows():
    """--reindex-only: 扫描 OUT 现存 md 的 frontmatter 重建 rows，排序规则与转换一致"""
    theme_seq = {}                           # 主题名 -> main_N 序号（review 组为准）
    for gid, g in review.items():
        m = re.match(r'main_(\d+)$', gid)
        if m and g.get('name'):
            theme_seq.setdefault(sanitize(g['name']), int(m.group(1)))
    rows = []
    for dirpath, _, files in os.walk(OUT):
        d = os.path.relpath(dirpath, OUT).replace('\\', '/')
        if d == '.':
            continue
        for fn in files:
            if not fn.endswith('.md') or fn == 'index.md':
                continue
            try:
                with open(os.path.join(dirpath, fn), encoding='utf-8') as f:
                    if f.readline().strip() != '---':
                        continue
                    buf = []
                    for ln in f:
                        if ln.strip() == '---':
                            break
                        buf.append(ln)
                    fm = json.loads('{' + ''.join(buf) + '}')
            except (OSError, ValueError):
                print('frontmatter 解析失败:', os.path.join(dirpath, fn))
                continue
            stem = (fm.get('id') or fn[:-3]).rsplit('/', 1)[-1]
            code = fm.get('code') or ''
            flag = 0 if stem.endswith('_beg') else (1 if stem.endswith('_end') else 2)
            rows.append({'dir': d, 'file': fn[:-3], 'title': fm.get('title') or fn[:-3],
                         'summary': fm.get('summary') or '', 'code': code,
                         'sort': (natkey(code) if code else ['\uffff\uffff'],
                                  flag, natkey(stem))})
            if d.startswith('主线/'):
                seq = theme_seq.get(d.split('/')[-1])
                if seq is not None:
                    DIR_SEQ.setdefault(d, seq)
    return rows

if ARGS.reindex_only:
    rows = scan_rows()

order = sorted({r['dir'] for r in rows}, key=cat_key)
rows.sort(key=lambda r: (cat_key(r['dir']), r['sort'][0], r['sort'][1],
                         r['sort'][2], r['file']))
by_dir = collections.OrderedDict((d, []) for d in order)
for r in rows:
    by_dir.setdefault(r['dir'], []).append(r)

def md_link(d, f):
    return '%s/%s' % (d.replace(' ', '%20').replace('(', '%28').replace(')', '%29'),
                      f.replace(' ', '%20').replace('(', '%28').replace(')', '%29'))

with open(os.path.join(OUT, 'index.md'), 'w', encoding='utf-8', newline='\n') as f:
    f.write('# 明日方舟剧情索引\n\n共 %d 篇剧情、%d 个分类。每篇正文为 Markdown，元数据见文件头 frontmatter。\n'
            % (len(rows), len(by_dir)))
    for d, items in by_dir.items():
        f.write('\n## %s\n\n' % d)
        for r in items:
            label = (r['code'] + ' ' + r['title']) if r['code'] and r['code'] not in r['title'] else r['title']
            if r['file'].endswith('_前'):
                label += '（前）'
            elif r['file'].endswith('_后'):
                label += '（后）'
            f.write('- [%s](%s.md)' % (label, md_link(d, r['file'])))
            if r['summary']:
                f.write('：' + r['summary'].replace('|', '，').replace('\n', ' '))
            f.write('\n')

# ---------------- 报告 ----------------
if ARGS.reindex_only:
    print('索引已按现存文件重建:', len(rows), '篇 | 目录:', len(by_dir), '个')
else:
    print('转换成功:', len(rows), '篇 | 输出目录:', len(by_dir), '个')
    if KEEP_IDS is not None:
        print('sync 防护: 白名单 %d 篇' % len(KEEP_IDS))
    print('排除:', dict(stats_ex))
    print('无官方标题(文件名兜底):', len(no_title))
    print('空输出:', len(empty_out), empty_out[:8])
    print('丢弃指令 top12:', dropped_tags.most_common(12))
    print('内容块保留:', stats['kept_lines'])
