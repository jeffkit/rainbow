# encoding=utf-8

import sys
import os
parentdir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, parentdir)
import json
import urllib


from tornado.test.util import unittest
from tornado.testing import AsyncHTTPTestCase
from tornado.web import Application

from server import log
from webhandler import SubChannelHandler
from webhandler import UnSubChannelHandler
from webhandler import SendMessageHandler
from wshandler import param_signature
from config import g_CONFIG


class BaseTest(AsyncHTTPTestCase):
    def setUp(self):
        g_CONFIG['security_token'] = 'tokentoken'
        super(BaseTest, self).setUp()


class WebHandlerTest(BaseTest):

    def get_app(self):
        return Application([
            ('/sub/', SubChannelHandler),
            ('/unsub/', UnSubChannelHandler),
            ('/send/', SendMessageHandler)
        ])

    def test_sub(self):
        body = {"channel": "uuiidd",
                "identity": "ed6bee835a19d6d15002f5d"}
        body = json.dumps(body)
        params = param_signature()
        params_str = urllib.urlencode(params)
        url = '/sub/?%s' % params_str

        response = self.fetch(url, method='POST', body=body)
        log.debug(response.code)
        log.debug(response.body)
        self.assertEqual(response.code, 200)

    def test_unsub(self):
        body = {"channel": "uuiidd",
                "identity": "ed6bee835a19d6d15002f5d"}
        body = json.dumps(body)
        params = param_signature()
        params_str = urllib.urlencode(params)
        url = '/unsub/?%s' % params_str

        response = self.fetch(url, method='POST', body=body)
        log.debug(response.code)
        log.debug(response.body)
        self.assertEqual(response.code, 200)

    def test_send(self):
        params = param_signature()
        params['channel'] = 'channel1'
        params['msgtype'] = '0'
        params['qos'] = '1'
        params['timeout'] = '5'

        params_str = urllib.urlencode(params)
        url = '/send/?%s' % params_str

        response = self.fetch(url, method='POST', body='haha')
        log.debug(response.code)
        log.debug(response.body)
        self.assertEqual(response.code, 200)


if __name__ == '__main__':
    unittest.main()
