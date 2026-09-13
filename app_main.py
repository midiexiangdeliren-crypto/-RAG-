# -*- coding: utf-8 -*-
"""打包入口: 等价于 `python server.py` (默认端口 8600, 自动开浏览器)
PyInstaller --noconsole 下 sys.stdout 可能为 None, 先兜底再导入业务模块。
"""
import os, sys

if sys.stdout is None:
    sys.stdout = open(os.devnull, 'w', encoding='utf-8')
if sys.stderr is None:
    sys.stderr = open(os.devnull, 'w', encoding='utf-8')

sys.argv = ['server.py']                 # 走 server.main() 默认参数
import server

if __name__ == '__main__':
    server.main()
