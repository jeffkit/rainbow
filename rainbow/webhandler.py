# encoding=utf-8

import json
import time
import logging as log
from hashlib import sha1

import tornado.web
from tornado.ioloop import IOLoop
from wshandler import WebSocketHandler
from wshandler import sub, unsub
# from tornado.httpclient import AsyncHTTPClient
# from tornado.httpclient import HTTPClient
from tornado import stack_context
from tornado.concurrent import TracebackFuture

import settings
from config import g_CONFIG


def fetch_msg(uid, msg_type, data, qos, timeout, callback=None):
    future = TracebackFuture()
    if callback is not None:
        callback = stack_context.wrap(callback)

        def handle_future(future):
            response = future.result()
            IOLoop.current().add_callback(callback, response)
        future.add_done_callback(handle_future)

    def web_handle_response(response=''):
        future.set_result(response)

    WebSocketHandler.send_message(
        uid, msg_type, data,
        qos, timeout, web_handle_response)

    return future


def is_signature(request):
    nonce = request.get_query_argument('nonce')
    timestamp = request.get_query_argument('timestamp')
    signature = request.get_query_argument('signature')
    if not nonce or not timestamp or not signature:
        log.warning('not nonce or not timestamp or not signature')
        return False
    if not settings.DEBUG:
        timenow = time.time()
        if not (timenow - 10 < int(timestamp) < timenow + 10):
            return False
    security_token = g_CONFIG['security_token']
    sign_ele = [security_token, timestamp, nonce]
    sign_ele.sort()
    sign = sha1(''.join(sign_ele)).hexdigest()
    if sign == signature:
        return True
    else:
        log.warning('sign != signature')
        return False


def error_rsp(request_handler, status, msg):
    data = json.dumps({'status': status, 'msg': msg})
    request_handler.finish(data)


class WebHandler(tornado.web.RequestHandler):

    @tornado.web.asynchronous
    def prepare(self):
        if not is_signature(self):
            return error_rsp(self, 1, 'signature error')
        return super(WebHandler, self).prepare()


class SendMessageHandler(WebHandler):

    @tornado.web.asynchronous
    def get(self):
        self.finish('ok')

    @tornado.web.asynchronous
    def post(self):
        """接受来自业务服务器的消息，发送给客户端。
        - uid: 用户唯一ID
        - message_type: 消息类型
        - data: 消息参数，JSON格式
        - qos: 要求本次发送消息的质量
        - timeout: 等待超时是间，单位为秒，0为默认超时时间。
        - callback: 回调函数。

        返回：
        成功：
        {'status': 0, 'connections': 1}
        connections：成功发送消息的连接。如果为0则表示用户没有在线。

        失败:
        {'status': -123, 'msg': 'timeout'}
        """
        log.info('post ' * 5)
        channel = self.get_query_argument('channel', '')
        log.info('channel = %s' % channel)
        if not channel:
            channel = 'uuiidd'
        msgtype = int(self.get_query_argument('msgtype', '1'))
        qos = int(self.get_query_argument('qos', 2))
        self.qos = qos
        timeout = int(self.get_query_argument('timeout', '10'))
        if timeout <= 0:
            timeout = 10
        data = self.request.body

        # 如果是集群模式，则直接调用其他服务器的接口。
        # 发送消息前，先看看uid分布在哪些机器上，然后去调用它们的发送接口。

        log.info('SendMessageHandler request.body = %s' % self.request.body)
        log.info('SendMessageHandler channel = %s' % channel)
        fetch_msg(channel, msgtype, data, qos, timeout, self.send_finish)

        self.toh = IOLoop.current().add_timeout(time.time() + timeout or 10,
                                                self.handle_timeout)

    def send_finish(self, response):
        """发送完成了，返回数据给客户端
        """
        if getattr(self, 'timeout', None):
            return

        IOLoop.current().remove_timeout(self.toh)

        data = {'status': 0}
        if self.qos > 0:
            data['connections'] = response
        data = json.dumps(data)
        log.debug('SendMessageHandler send_finish data = %s' % data)
        self.finish(data)

    def handle_timeout(self):
        # 虽然超时，但是是否能够知道有部份成功发送？
        self.timeout = True
        log.debug('SendMessageHandler timeout')
        self.finish(json.dumps({'status': 1, 'msg': 'timeout'}))


class SubChannelHandler(WebHandler):

    @tornado.web.asynchronous
    def post(self):
        data = json.loads(self.request.body)
        identity = data.get('identity')
        channel = data.get('channel')
        occupy = data.get('occupy', False)
        if occupy:
            occupy = True
        if not identity or not channel:
            return error_rsp(self, 1, 'params error')

        ret, errmsg = sub(identity, channel, occupy)
        data = {}
        if ret:
            data['status'] = 0
        else:
            data['status'] = 1
            data['msg'] = errmsg

        log.debug('SubChannelHandler rsp data = %s' % data)
        self.finish(json.dumps(data))


class UnSubChannelHandler(WebHandler):

    @tornado.web.asynchronous
    def post(self):
        data = json.loads(self.request.body)
        identity = data.get('identity')
        channel = data.get('channel')
        if not identity or not channel:
            return error_rsp(self, 1, 'params error')

        ret, errmsg = unsub(identity, channel)
        data = {}
        if ret:
            data['status'] = 0
        else:
            data['status'] = 1
            data['msg'] = errmsg

        log.debug('SubChannelHandler rsp data = %s' % data)
        self.finish(json.dumps(data))
