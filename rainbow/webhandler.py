# encoding=utf-8

import json
import time
import logging as log
from hashlib import sha1
import traceback
import urllib

import tornado.web
from tornado.ioloop import IOLoop
from tornado import stack_context
from tornado.concurrent import TracebackFuture
from tornado.httpclient import AsyncHTTPClient
from tornado.httpclient import HTTPRequest

import settings
from config import g_CONFIG
from wshandler import WebSocketHandler
from wshandler import sub, unsub
# from config import g_Online_Server
# from config import g_Online_Server_List
from config import g_Online_Server_deque
from api import param_signature


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

    def exception_finish(self, e, tb):
        log.error(e)
        log.error(tb)
        self.set_status(500)
        self.finish()


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
        try:
            channel = self.get_query_argument('channel', '')
            if not channel:
                return error_rsp(self, 1, 'channel miss')
            msgtype = int(self.get_query_argument('msgtype', '1'))
            qos = int(self.get_query_argument('qos', 2))
            if qos < 0 or qos > 2:
                return error_rsp(self, 1, 'qos level error')
            self.qos = qos
            timeout = int(self.get_query_argument('timeout', '10'))
            if timeout <= 0:
                timeout = 10
            data = self.request.body
            self.server_cnt = 1
            self.server_rsp_cnt = 0

            # 如果是集群模式，则直接调用其他服务器的接口。
            if not self.get_query_argument('cluster', ''):
                self.cluster_send_message(channel, msgtype, qos, timeout, data)

            log.info('SendMessageHandler body = %s' % self.request.body)
            log.debug('SendMessageHandler channel = %s' % channel)
            fetch_msg(channel, msgtype, data, qos, timeout, self.fetch_msg_cb)

            self.toh = IOLoop.current().add_timeout(
                time.time() + timeout or 10,
                self.handle_timeout)
        except Exception, e:
            self.exception_finish(e, traceback.format_exc())

    def fetch_msg_cb(self, response):
        log.info(response)
        self.server_rsp_cnt = self.server_rsp_cnt + 1
        if self.qos > 0:
            self.rb_connections = getattr(
                self, 'rb_connections', 0) + response['connections']
            self.rb_data = response['data'] or getattr(self, 'rb_data', '')

        if self.server_rsp_cnt == self.server_cnt:
            self.send_finish()

    def send_finish(self):
        log.debug('')
        if getattr(self, 'timeout', None):
            log.info('already timeout return')
            return
        IOLoop.current().remove_timeout(self.toh)
        if getattr(self, 'rb_finish', None):
            log.info('already finish return')
            return
        self.rb_finish = True
        data = {'status': 0}
        if self.qos > 0:
            data['connections'] = getattr(self, 'rb_connections', 0)
            data['data'] = getattr(self, 'rb_data', '')
        data = json.dumps(data)
        log.info('SendMessageHandler body = %s' % self.request.body)
        log.info('SendMessageHandler send_finish data = %s' % data)
        try:
            self.finish(data)
        except Exception, e:
            self.exception_finish(e, traceback.format_exc())

    def handle_timeout(self):
        # 虽然超时，但是是否能够知道有部份成功发送？
        self.timeout = True
        log.info('SendMessageHandler timeout')
        log.info('SendMessageHandler body = %s' % self.request.body)
        try:
            self.finish(json.dumps({'status': 1, 'msg': 'timeout'}))
        except Exception, e:
            self.exception_finish(e, traceback.format_exc())

    def cluster_send_message(self, channel, msgtype, qos, timeout, body):
        try:
            params = param_signature()
            params['channel'] = channel
            params['msgtype'] = msgtype
            params['qos'] = qos
            if timeout >= 4:
                timeout = timeout - 1
            params['timeout'] = timeout
            params['cluster'] = '1'
            params_str = urllib.urlencode(params)

            # for server in g_Online_Server_List:
            for server in g_Online_Server_deque:
                self.server_cnt = self.server_cnt + 1
                log.debug('self.server_cnt = %d' % self.server_cnt)
                url = '%s/send/?%s' % (server, params_str)
                req = HTTPRequest(
                    url=url, method='POST', body=body,
                    connect_timeout=20, request_timeout=20)
                http_client = AsyncHTTPClient()
                http_client.fetch(req, self.cluster_send_msg_cb)
        except Exception, e:
            log.warning(e)
            log.warning(traceback.format_exc())

    def cluster_send_msg_cb(self, rsp):
        log.debug('')
        self.server_rsp_cnt = self.server_rsp_cnt + 1
        # 集群的其它 server 返回
        if rsp.error:
            log.warning('rsp.error = %s' % rsp.error)
        else:
            log.debug('rsp.body = %s' % rsp.body)
            if self.qos > 0:
                body = json.loads(rsp.body)
                if body['status'] == 0:
                    self.rb_connections = getattr(
                        self, 'rb_connections', 0) + body['connections']
                    self.rb_data = getattr(self, 'rb_data', '') or body['data']

        if self.server_rsp_cnt == self.server_cnt:
            self.send_finish()


class SubWebHandler(WebHandler):

    def cluster_init(self):
        self.server_cnt = 1
        self.server_rsp_cnt = 0
        self.status = 1

    def cluster_send_cb(self, rsp):
        log.debug('')
        # 集群的其它 server 返回
        if rsp.error:
            log.warning('rsp.error = %s' % rsp.error)
            data = {'status': 1, 'msg': rsp.error}
        else:
            log.debug('rsp.body = %s' % rsp.body)
            data = json.loads(rsp.body)

        self.sub_return(data)

    def sub_return(self, data):
        log.debug('SubWebHandler data = %s' % data)
        self.server_rsp_cnt = self.server_rsp_cnt + 1
        if data['status'] == 0:
            self.status = 0
        else:
            self.msg = data['msg']

        if self.server_rsp_cnt == self.server_cnt:
            self.send_finish()

    def send_finish(self):
        data = {'status': self.status}
        if self.status == 1:
            data['msg'] = self.msg
        data = json.dumps(data)
        log.info('data = %s' % data)
        try:
            self.finish(data)
        except Exception, e:
            self.exception_finish(e, traceback.format_exc())

    def cluster_send(self, body, uri):
        self.status = 1
        self.msg = ''
        try:
            params = param_signature()
            params['cluster'] = '1'
            params_str = urllib.urlencode(params)

            for server in g_Online_Server_deque:
                self.server_cnt = self.server_cnt + 1
                log.debug('self.server_cnt = %d' % self.server_cnt)
                url = '%s/%s/?%s' % (server, uri, params_str)
                req = HTTPRequest(
                    url=url, method='POST', body=body,
                    connect_timeout=5, request_timeout=5)
                http_client = AsyncHTTPClient()
                http_client.fetch(req, self.cluster_send_cb)
        except Exception, e:
            log.warning(e)
            log.warning(traceback.format_exc())


class SubChannelHandler(SubWebHandler):

    @tornado.web.asynchronous
    def post(self):
        try:
            log.debug(self.request.body)
            data = json.loads(self.request.body)
            identity = data.get('identity')
            channel = data.get('channel')
            occupy = data.get('occupy', False)
            if occupy:
                occupy = True
            if not identity or not channel:
                return error_rsp(self, 1, 'params error')

            self.cluster_init()

            if not self.get_query_argument('cluster', ''):
                self.cluster_send(self.request.body, 'sub')

            ret, errmsg = sub(identity, channel, occupy)
            data = {}
            if ret:
                data['status'] = 0
            else:
                data['status'] = 1
                data['msg'] = errmsg

            self.sub_return(data)
        except Exception, e:
            self.exception_finish(e, traceback.format_exc())


class UnSubChannelHandler(SubWebHandler):

    @tornado.web.asynchronous
    def post(self):
        try:
            data = json.loads(self.request.body)
            identity = data.get('identity')
            channel = data.get('channel')
            if not identity or not channel:
                return error_rsp(self, 1, 'params error')

            self.cluster_init()

            if not self.get_query_argument('cluster', ''):
                self.cluster_send(self.request.body, 'unsub')

            ret, errmsg = unsub(identity, channel)
            data = {}
            if ret:
                data['status'] = 0
            else:
                data['status'] = 1
                data['msg'] = errmsg

            self.sub_return(data)
        except Exception, e:
            self.exception_finish(e, traceback.format_exc())


class HelloHandler(tornado.web.RequestHandler):

    @tornado.web.asynchronous
    def get(self):
        self.finish('ok')
