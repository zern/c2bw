# -*- coding: utf-8 -*-
"""
c2bw - 智能图像预处理与重构工具 (兼容入口)
重构为模块化架构：
c2bw/
├── core.py      (纯算力核心：PDF提取、图片裁切、OTSU、二值化、PDF 1.5封装)
├── service.py   (任务调度与状态机：start/pause/cancel/events)
├── desktop.py   (桌面端宿主：Pywebview、COM拖拽解析、Tkinter容灾回退)
├── android.py   (安卓端宿主：Flask 本地 API、Android SAF 适配)
└── webui/       (Vue 2 + Element UI 前端)

本文件作为桌面版打包与向下兼容顶层入口，直接委托调用 c2bw.desktop.main()。
"""

import multiprocessing

multiprocessing.freeze_support()

from c2bw.core import *
from c2bw.service import *
from c2bw.desktop import *


if __name__ == '__main__':
    main()
