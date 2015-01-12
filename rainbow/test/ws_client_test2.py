# encoding=utf-8

import sys
import os
parentdir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, parentdir)
import random
import logging as log
import traceback
import thread
import time

from config import g_CONFIG
from test.ws_client_test import websocket_test, init_config


def run_client():
    o = websocket_test()
    try:
        o.create_connection(random.randint(0, g_CONFIG['channelcnt']))
        o.run2()
    except Exception, e:
        log.warning(e)
        log.warning(traceback.format_exc())


def main():
    if not init_config():
        log.error('init_config fail, close')
        return

    for i in range(0, g_CONFIG['linkcnt']):
        time.sleep(0.01)
        thread.start_new_thread(run_client, ())

    while True:
        time.sleep(random.randint(10, 20))
        for i in range(0, g_CONFIG['linkcnt']):
            time.sleep(0.01)
            thread.start_new_thread(run_client, ())


if __name__ == '__main__':
    main()
