# -*- coding: utf-8 -*-
import asyncio, multiprocessing, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
print('父进程策略:', type(asyncio.get_event_loop_policy()).__name__)
def child():
    import asyncio
    print('spawn 子进程策略:', type(asyncio.get_event_loop_policy()).__name__)
    print('spawn 子进程 new_loop:', type(asyncio.new_event_loop()).__name__)
if __name__ == '__main__':
    p = multiprocessing.Process(target=child)
    p.start(); p.join()
