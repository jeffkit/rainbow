#encoding=utf-8

import tornado.web
from tornado import gen
from tornado.concurrent import return_future
from wshandler import WebSocketHandler


class SendMessageHandler(tornado.web.RequestHandler):

    def get(self):
        self.write('hello world')

    @gen.coroutine
    def post(self):
        """接受来自业务服务器的消息，发送给客户端。
        - uid: 用户唯一ID
        - message_type: 消息类型
        - data: 消息参数，JSON
        - qos: 要求本次发送消息的质量
        - timeout: 等待超时是间，单位为秒，0为默认超时时间。
        - callback: 回调函数。
        """

        uid = ''
        msg_type = ''
        data = {}
        qos = 0

        # 如果是集群模式，则直接调用其他服务器的接口。
        # 发送消息前，先看看uid分布在哪些机器上，然后去调用它们的发送接口。

        if uid in WebSocketHandler.socket_handlers:
            handlers = WebSocketHandler.socket_handlers[uid]
            if not isinstance(handlers, list):
                handlers = [handlers]
            packet = Packet(command=Packet.PACKET_SEND,
                            msg_type=msg_type,
                            data=data, qos=qos)

            results = yield [handler.send_packet(packet)
                             for handler in handlers]
        self.finish({'success': sum(results)})

    @gen.engine
    def send_message(self, uid, message_type, data, qos=0, timeout=0,
                     callback=None):
        """向客户端发送消息, 直至客户端有返回或超时。参数说明：
        
        返回：Feature,异步执行发送动作，而不block当前线程。
        """
        pass
