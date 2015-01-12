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
from optparse import OptionParser
import traceback
import ConfigParser

from tornado.httpclient import HTTPClient
from tornado.httpclient import HTTPRequest

from api import param_signature
from config import g_CONFIG


def init_config():
    parser = OptionParser()
    try:
        parser.add_option("-f", dest="config_filename", type="string")

        parser.add_option("--host", dest="httphost", type="string")
        parser.add_option(
            "-r", "--requestcnt", dest="requestcnt", type="int")
        parser.add_option(
            "-c", "--channelcnt", dest="channelcnt", type="int")
        options, args = parser.parse_args()
    except Exception, e:
        log.error(e)
        log.error(traceback.format_exc())
        return False

    if not getattr(options, 'config_filename', None):
        log.error('miss -f')
        return False
    if not getattr(options, 'requestcnt', None):
        log.error('Options miss -c')
        return False
    if not getattr(options, 'httphost', None):
        log.error('Options miss --host')
        return False
    if not getattr(options, 'channelcnt', None):
        log.error('Options miss -c')
        return False

    try:
        config = ConfigParser.SafeConfigParser()
        config.read(options.config_filename)
        g_CONFIG['security_token'] = config.get("main", "security_token")
    except Exception, e:
        log.error(e)
        log.error(traceback.format_exc())
        return False

    g_CONFIG['httphost'] = options.httphost
    g_CONFIG['requestcnt'] = options.requestcnt
    g_CONFIG['channelcnt'] = options.channelcnt

    log.info(g_CONFIG)
    return True


def _run_request(channel):
    params = param_signature()
    params['channel'] = '%d' % channel
    params['qos'] = 2
    params['timeout'] = 10
    param_str = urllib.urlencode(params)
    url = 'http://%s/send/?%s' % (g_CONFIG['httphost'], param_str)
    body = 'message from business server'
    req = HTTPRequest(
        url=url, method='POST', body=body,
        connect_timeout=10, request_timeout=10)
    try:
        timebegin = time.time()
        HTTPClient().fetch(req)
        timelast = ((time.time() - timebegin) * 1000)
        log.info('%s %dms' % (url, timelast))
    except Exception, e:
        log.error(e)
        log.error(traceback.format_exc())


def run_request(channel):
    for i in range(0, g_CONFIG['requestcnt']):
        _run_request(channel)


def main():
    if not init_config():
        log.error('init_config fail, close')
        return

    for channel in range(0, g_CONFIG['channelcnt']):
        thread.start_new_thread(run_request, (channel,))

    while True:
        time.sleep(random.randint(2, 10))


if __name__ == '__main__':
    main()
