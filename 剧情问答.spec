# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 打包配置: onedir + 无控制台, 数据文件进 _internal (与脚本 HERE 相对路径一致)
# 构建: uv run pyinstaller 剧情问答.spec --noconfirm
from PyInstaller.utils.hooks import collect_all

faiss_datas, faiss_bins, faiss_hidden = collect_all('faiss')
jieba_datas, jieba_bins, jieba_hidden = collect_all('jieba')

a = Analysis(
    ['app_main.py'],
    pathex=[],
    binaries=faiss_bins + jieba_bins,
    datas=[
        ('static', 'static'),
        ('dict_operators.txt', '.'),
        ('embed_config.json', '.'),
        ('chunks.sqlite', '.'),
        ('vectors.faiss', '.'),
        ('faiss_ids.json', '.'),
        ('bm25.pkl', '.'),
    ] + faiss_datas + jieba_datas,
    hiddenimports=[
        # uvicorn 运行时动态导入的子模块
        'uvicorn.logging',
        'uvicorn.loops', 'uvicorn.loops.auto',
        'uvicorn.protocols', 'uvicorn.protocols.http', 'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets', 'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan', 'uvicorn.lifespan.on',
    ] + faiss_hidden + jieba_hidden,
    excludes=['tkinter', 'matplotlib', 'pandas'],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name='剧情问答',
    console=False,                # 无控制台窗口
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name='明日方舟剧情问答',
)
