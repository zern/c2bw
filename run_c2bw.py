# -*- coding: utf-8 -*-
"""
c2bw 桌面应用启动入口
"""
import multiprocessing

if __name__ == '__main__':
    multiprocessing.freeze_support()
    from c2bw.desktop import main
    main()
