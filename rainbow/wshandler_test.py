
import json

from tornado.web import Application

# from tornado.concurrent import Future
from tornado.test.util import unittest
from tornado.testing import AsyncHTTPTestCase
from tornado.testing import gen_test
from tornado.websocket import websocket_connect
from tornado.web import RequestHandler

from server import log
from wshandler import WebSocketHandler
from config import g_CONFIG


class ConnectHandler(RequestHandler):

    def get(self):
        data = {'status': 'success'}
        data = json.dumps(data)
        self.finish(data)


class BaseTest(AsyncHTTPTestCase):

    def setUp(self):
        g_CONFIG['connect_url'] = (
            'http://localhost:%d/'
            'connecturl/' % self.get_http_port())
        g_CONFIG['security_token'] = 'tokentoken'
        super(BaseTest, self).setUp()


class WebSocketTest(BaseTest):

    def get_app(self):
        return Application([
            ('/connect/', WebSocketHandler),
            ('/connecturl/', ConnectHandler)

        ])

    @gen_test
    def test_websocket_gen(self):
        url = 'ws://localhost:%d/connect/?X-CLIENT-OS=jj&X-DEVICEID=8'
        log.debug(self.get_http_port())
        ws = yield websocket_connect(
            url % self.get_http_port(),
            io_loop=self.io_loop)
        ws.write_message('hello')
        response = yield ws.read_message()
        log.debug(response)

        # response = ws.read_message()
        # self.assertEqual(response, 'hello')
        ws.close()


if __name__ == '__main__':
    unittest.main()
