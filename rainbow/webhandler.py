#encoding=utf-8

import tornado.web
from tornado.ioloop import IOLoop
from wshandler import WebSocketHandler

import time


class SendMessageHandler(tornado.web.RequestHandler):

    def get(self):
        self.write('hello world')

    @tornado.web.asynchronous
    def post(self):
        """接受来自业务服务器的消息，发送给客户端。
        - uid: 用户唯一ID
        - message_type: 消息类型
        - data: 消息参数，JSON
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

        uid = self.get_query_argument('uid')
        msg_type = self.get_query_argument('msg_type')
        data = self.get_query_argument('data')
        qos = int(self.get_query_argument('qos', 2))
        timeout = int(self.get_query_argument('timeout', 10))

        # 如果是集群模式，则直接调用其他服务器的接口。
        # 发送消息前，先看看uid分布在哪些机器上，然后去调用它们的发送接口。

        future = WebSocketHandler.send_message(uid, msg_type,
                                               data, qos, timeout,
                                               self.send_finish)

        self.toh = IOLoop.current().add_timeout(time.time() + timeout or 10,
                                                self.handle_timeout)

    def send_finish(self, result):
        """发送完成了，返回数据给客户端
        """
        IOLoop.current().remove_timeout(self.toh)
        self.finish(json.dumps({'status': 0, 'success': result}))

    def handle_timeout(self):
        # 虽然超时，但是是否能够知道有部份成功发送？
        self.finish(json.dumps({'status': 1, 'msg': 'timeout'}))
