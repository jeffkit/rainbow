#encoding=utf-8

from tornado.websocket import WebSocketHandler as Handler
from tornado.concurrent import return_future

import json
import redis
import sys
import traceback
import struct

import logging as log
log.basicConfig(level='INFO')


redis_client = redis.Redis()
USER_ID_HASH = 'websocket_connected_users'


class Packet(object):

    PACKET_RESERVED = 0
    PACKET_SEND = 1
    PACKET_ACK = 2  # for QOS1
    PACKET_REC = 3  # for QOS2
    PACKET_REL = 4  # for QOS2
    PACKET_COM = 5  # for QOS2

    def __init__(self, raw=None, command=None, msg_type=None, data=None,
                 qos=0, dup=0, message_id=None):
        """data有一至两个byte的header
        - 第1个byte是命令及选项，命令占4位，选项占4位。
        选项只有PACKET_SEND命令才用到，DUP1位, QOS2位，预留1位
        - 消息类型，2个byte是可选，只有PACKET_SEND命令才有用，代表消息的类型
        - 消息id，1个byte是可选的，message_id。当QOS大于0时才有。
        - data，消息内容，SEND时必有，ACK，REC可能有。
        """
        self._raw = raw
        self._valid = True
        if self._raw:
            meta = struct.unpack('!B', raw[0])
            if not meta[0]:
                self._valid = False
                return

            command = (meta[0] & 0xF0) / 16
            if command == self.PACKET_RESERVED:
                self._valid = False
                return

            qos = (meta[0] & 0x06) / 2
            dup = 1 if meta[0] & 0x08 == 8 else 0
            if command in (self.PACKET_SEND,
                           self.PACKET_ACK,
                           self.PACKET_REC):
                msg_type = struct.unpack('!H', raw[1:3])[0]
                data_idx = 3
                if qos > 0:
                    message_id = struct.unpack('!H', raw[3:5])[0]
                    data_idx = 5
                if len(data) > data_idx:
                    data = json.loads(data[data_idx:])
            else:
                message_id = struct.unpack('!H', raw[1:3])[0]

        self.command = command  # 命令
        self.msg_type = msg_type
        self.qos = qos  # 发送的质量
        self.dup = dup  # 是否重复发送
        self.message_id = message_id  # 消息ID
        self.data = data  # 实际数据

    @property
    def raw(self):
        if not self._raw:
            _raw = ''
            _raw += chr(self.command * 16 + self.dup * 8 + self.qos * 2)
            if self.command == self.PACKET_SEND:
                _raw += struct.pack('!H', self.msg_type)
                if self.qos > 0:
                    _raw += struct.pack('!H', self.message_id)
            else:
                _raw += struct.pack('!H', self.message_id)
            if self.data:
                _raw += json.dumps(self.data, ensure_ascii=False)
            self._raw = _raw
        return self._raw

    @property
    def valid(self):
        return self._valid


class WebSocketHandler(Handler):

    socket_handlers = {}

    def open(self):
        """
        成功创建websocket连接，保存或更新用户信息。
        """
        log.info('Open connection for %s' % self.uid)
        self.update_handler()
        try:
            redis_client.hsetnx(USER_ID_HASH, self.uid, 1)
        except:
            pass

    def update_handler(self):
        """把当前handler增加到handler_map中。
        """
        handlers = self.socket_handlers.get(self.uid, None)
        if handlers:
            if isinstance(handlers, list):
                if self not in handlers:
                    self.socket_handlers[self.uid].append(self)
            else:
                self.socket_handlers[self.uid] = [handlers, self]
        else:
            self.socket_handlers[self.uid] = self

    def on_close(self):
        """从handler_map移除掉handler
        """
        log.info('will close handler for user %s' % self.uid)
        log.info('handlers defore close %d' % len(self.socket_handlers))
        handlers = self.socket_handlers.get(self.uid, None)
        if not handlers:
            return
        if isinstance(handlers, list):
            if self in handlers:
                handlers.remove(self)
                if len(handlers) == 0:
                    del self.socket_handlers[self.uid]
                elif len(handlers) == 1:
                    self.socket_handlers[self.uid] = handlers[0]
                else:
                    self.socket_handlers[self.uid] = handlers
        else:
            del self.socket_handlers[self.uid]
        log.debug('handlers after close %d' % len(self.socket_handlers))
        try:
            if redis_client.hexists(USER_ID_HASH, self.uid):
                redis_client.hdel(USER_ID_HASH, self.uid)
        except:
            pass

        if self.uid in self.client_versions:
            del self.client_versions[self.uid]

    @classmethod
    def send_message(cls, uid, message_type, data, qos=0, timeout=0):
        """本方法返回一个Future给调用者。
        """
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

    def on_message(self, message):
        """message的前两个以上的字节为header，表示这是什么样的数据包及数据包长度。
        - send
        - send_ack  //for QOS1。
        - send_receive  //for QOS2, 下同。
        - send_release
        - send_complete
        """
        try:
            if not message:
                return
            pck = Packet(raw=message)
            if not pck.valid:
                return
            {Packet.PACKET_SEND: self.on_packet_send,
             Packet.PACKET_ACK: self.on_packet_ack,
             Packet.PACKET_REC: self.on_packet_rec,
             Packet.PACKET_REL: self.on_packet_rel,
             Packet.PACKET_COM: self.on_packet_com}[pck.command](pck)
        except Exception:
            log.error('handle message error %s' % message, exc_info=True)

    def on_packet_send(self, packet):
        """收到PACKET_SEND消息
        """
        if packet.qos == 0:
            result = None #  转发消息获取结果。
            return
        elif packet.qos == 1:
            # 需要返回ack
            result = None #  转发消息获取结果。
            rp = Packet(command=Packet.PACKET_ACK, data=result,
                        message_id=packet.message_id)
            self.write_message(rp.raw)
        elif packet.qos == 2:
            # 需要返回rec
            if packet.dup == 1 or packet.message_id in self.received_message_ids:
                # 重复消息，不处理。
                return
            result = None # 转发消息获取结果
            self.received_message_ids.add(packet.message_id)
            rp = Packet(command=Packet.PACKET_REC, data=result,
                        message_id=packet.message_id)
            self.write_message(rp.raw)

    def on_packet_ack(self, packet):
        """收到PACKET_ACK消息, 结果要通知给http的回调者。这里要怎么搞？
        """
        pass

    def on_packet_rec(self, packet):
        """收到PACKET_REC消息, 要通知给http调用者。
        """
        pass

    def on_packet_rel(self, packet):
        """收到PACKET_REL消息，删除消息ID，返回COM消息。
        """
        if packet.message_id not in self.received_message_ids:
            return
        self.received_message_ids.remove(packet.message_id)
        rp = Packet(command=Packet.PACKET_COM, message_id=packet.message_id)
        self.write_message(rp.raw)

    def on_packet_com(self, packet):
        """收到PACKET_COM消息。删除消息ID即可。
        """
        if packet.message_id in self.pendding_message_ids:
            self.pendding_message_ids.remove(packet.message_id)

    def send_packet(self, packet):
        """此方法需要返回Future
        """
        if packet.qos == 0:
            self.write_message(packet.raw)
        else:
            # qos = 1 或 2
            # 生成消息id，将消息发送至客户端，然后记录消息id。等待ack
            message_id = self.next_message_id()  #生成并保存id到内存
            packet.message_id = message_id
            self.write_message(packet.raw)

    @classmethod
    def shutdown(cls):
        """把当前线程所有的用户状态标记为离线。
        再关闭用户连接。
        """
        for uid, handler in cls.socket_handlers.iteritems():
            if isinstance(handler, list):
                for h in handler:
                    log.info('close here 1')
                    h.close()
            else:
                handler.close()
                log.info('close here 2')

    def get(self, *args, **kwds):
        """在打开websocket前先进行权限校验，如果没权限直接断开连接。
        """
        log.info('在打开websocket前先进行权限校验，如果没权限直接断开连接')
        # 保存uid或关闭连接
        # result = self.valid_request()
        uid = self.valid_request()
        if not uid:
            log.warn('invalid request, close!')
            self.set_status(401)
            self.finish('HTTP/1.1 401 Unauthorized\r\n\r\nNot authenticated.')
            return
        else:
            self.uid = uid
        super(WebSocketHandler, self).get(*args, **kwds)

    def valid_request(self):
        """检查该请求是否合法，如果合法则返回uid，否则为空。
        """
        log.info(self.request.headers)
        # 这里需要把请求头的一些信息转到某个鉴权的地址上去。
        return 'uid'
