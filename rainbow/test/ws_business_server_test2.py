# encoding=utf-8

import sys
import os
parentdir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, parentdir)
import thread
import random
import logging as log
import time

from config import g_CONFIG
from test.ws_business_server_test import init_config, _run_request


def run_request():
    for i in range(0, g_CONFIG['requestcnt']):
        _run_request(random.randint(0, g_CONFIG['channelcnt']))


def main():
    if not init_config():
        log.error('init_config fail, close')
        return

    thread.start_new_thread(run_request, ())

    while True:
        time.sleep(random.randint(2, 10))
        thread.start_new_thread(run_request, ())


if __name__ == '__main__':
    main()
