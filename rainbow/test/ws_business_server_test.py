# encoding=utf-8

import sys
import os
parentdir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, parentdir)
import thread
import random
import urllib
import logging as log
import time

from tornado.httpclient import HTTPClient
from tornado.httpclient import HTTPRequest

from api import param_signature, init_config


def _run_request():
    c = random.randint(1, 20)
    url = 'http://127.0.0.1:1984/send/'
    params = param_signature()
    params['channel'] = 'aaa%d' % c
    params['qos'] = 2
    params['timeout'] = 13
    param_str = urllib.urlencode(params)
    url = 'http://127.0.0.1:1984/send/?%s' % param_str
    log.info('url = %s' % url)
    body = 'message from business server'
    req = HTTPRequest(
        url=url, method='POST', body=body,
        connect_timeout=10, request_timeout=10)
    rsp = HTTPClient().fetch(req)
    log.info(rsp.body)


def run_request():
    while True:
        _run_request()
        time.sleep(random.randint(2, 10))


def main():
    init_config()
    for i in range(1, 100):
        thread.start_new_thread(run_request, ())

    while True:
        pass


if __name__ == '__main__':
    main()
