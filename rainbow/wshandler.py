# encoding=utf-8
import time
import json
import struct

from tornado.websocket import WebSocketHandler as Handler
from tornado.concurrent import TracebackFuture
from tornado.ioloop import IOLoop
import redis

import logging as log
log.basicConfig(level='INFO')

import settings
redis_client = redis.Redis(
    settings.REDIS_HOST,
    settings.REDIS_PORT,
    settings.REDIS_DATABASE,
    settings.REDIS_PASSWORD)
USER_ID_HASH = 'websocket_connected_users'

# 每个uid 当前 migid, g_uid_msgid[uid] = message_id
g_uid_msgid = {}
# 每个uid 的 message_id 的 future hdl,
# g_uid_msgid_hdl[uid]={[message_id]=hdl, [message_id2]=hdl2}
g_uid_msgid_hdl = {}


def clear_uid_msg_data(uid):
    if g_uid_msgid.get(uid):
        del g_uid_msgid[uid]
    if g_uid_msgid_hdl.get(uid):
        del g_uid_msgid_hdl[uid]


def get_next_msgid(uid):
    # 单线程？
    message_id = g_uid_msgid.get(uid, 0)
    message_id = message_id + 1
    g_uid_msgid[uid] = message_id
    return message_id


def del_msgid_memory(uid):
    if g_uid_msgid.get(uid):
        del g_uid_msgid[uid]


def set_msg_hdl(uid, message_id, web_handle_response, client_count):
    if not web_handle_response:
        return

    message_id_list = g_uid_msgid_hdl.get(uid, {})
    if not message_id_list:
        g_uid_msgid_hdl[uid] = message_id_list
    message_id_list[message_id] = {}
    message_id_list[message_id]['web_handle_response'] = web_handle_response
    # ack 或 rec 完成了的记录
    message_id_list[message_id]['finish_list'] = {}
    message_id_list[message_id]['error_list'] = {}
    message_id_list[message_id]['client_count'] = client_count


def clear_msg_hdl(uid, message_id):
    if not g_uid_msgid_hdl.get(uid):
        return
    if not g_uid_msgid_hdl[uid].get(message_id):
        return

    del g_uid_msgid_hdl[uid][message_id]


# 对 webhandler的回应
def send_msg_response(uid, message_id, ws_handler, error=''):
    if not g_uid_msgid_hdl.get(uid):
        return
    if not g_uid_msgid_hdl[uid].get(message_id):
        return

    hdl_info = g_uid_msgid_hdl[uid][message_id]
    # 用字典防止多次确认
    if not error:
        hdl_info['finish_list'][ws_handler] = 1
        if hdl_info['error_list'].get(ws_handler):
            # 先timeout 再来 on_message
            del hdl_info['error_list'][ws_handler]
    else:
        hdl_info['error_list'][ws_handler] = 1

    finish_count = len(hdl_info['finish_list'])
    rsp_count = finish_count + len(hdl_info['error_list'])
    if rsp_count == hdl_info['client_count']:
        # 所有客户端都有返回或者超时
        if hdl_info['web_handle_response']:
            hdl_info['web_handle_response'](finish_count)
        clear_msg_hdl(uid, message_id)


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
    QOS_LEVEL1 = 0
    QOS_LEVEL2 = 1
    QOS_LEVEL3 = 2

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
        self.handler_init()

    def handler_init(self):
        # 每个 ws 连接也要维护 消息的 future
        self.future_rsp_hl = {}
        self.rsp_timeout_hl = {}

    def handler_close(self):
        for toh in self.rsp_timeout_hl:
            IOLoop.current().remove_timeout(self.toh)
        for handle_response in self.future_rsp_hl:
            handle_response(exception='on_close')
        self.future_rsp_hl = None
        self.rsp_timeout_hl = None

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

        self.handler_close()

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

        handlers = self.socket_handlers.get(self.uid, None)
        if not handlers:
            clear_uid_msg_data(self.uid)

        log.debug('handlers after close %d' % len(self.socket_handlers))
        try:
            if redis_client.hexists(USER_ID_HASH, self.uid):
                redis_client.hdel(USER_ID_HASH, self.uid)
            redis_client.delete(self.get_packet_status_key(self.uid))
        except:
            pass

        if self.uid in self.client_versions:
            del self.client_versions[self.uid]

    @classmethod
    def send_message(
            cls, uid, message_type, data,
            qos=0, timeout=0, web_handle_response=None):
        if uid in WebSocketHandler.socket_handlers:
            handlers = WebSocketHandler.socket_handlers[uid]
            if not isinstance(handlers, list):
                handlers = [handlers]
            packet = Packet(command=Packet.PACKET_SEND,
                            msg_type=message_type,
                            data=data, qos=qos)

            # 记录 消息 的回掉情况
            client_count = len(handlers)
            message_id = get_next_msgid(uid)
            set_msg_hdl(uid, message_id, web_handle_response, client_count)

            packet.message_id = message_id

            if timeout > 1:
                timeout = timeout - 1
            for handler in handlers:
                handler.send_packet(packet, timeout)
        else:
            # 没有客户端在线
            if web_handle_response:
                web_handle_response(0)
            pass

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
            result = None
            return
        elif packet.qos == 1:
            # 需要返回ack
            result = None
            rp = Packet(command=Packet.PACKET_ACK, data=result,
                        message_id=packet.message_id)
            self.write_message(rp.raw)
        elif packet.qos == 2:
            # 需要返回rec
            if packet.dup == 1 or \
                    packet.message_id in self.received_message_ids:
                # 重复消息，不处理。
                return
            result = None
            self.received_message_ids.add(packet.message_id)
            rp = Packet(command=Packet.PACKET_REC, data=result,
                        message_id=packet.message_id)
            self.write_message(rp.raw)

    def on_packet_ack(self, packet):
        """收到PACKET_ACK消息, 结果要通知给http的回调者。这里要怎么搞？
        """
        handle_response = self.future_rsp_hl.get(packet.message_id)
        if handle_response:
            handle_response(packet)

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

    def send_packet_cb(self, message_id, packet, exception):
        toh = self.rsp_timeout_hl.get(message_id)
        if toh:
            IOLoop.current().remove_timeout(self.toh)
            del self.rsp_timeout_hl[message_id]
        send_msg_response(self.uid, message_id, self, error=exception)

    def send_packet_cb_timeout(self, message_id):
        send_msg_response(self.uid, message_id, self, error='timeout')

    def send_packet(self, packet, timeout):
        """此方法需要返回Future
        """
        if packet.qos == self.QOS_LEVEL1:
            self.write_message(packet.raw)
        else:
            # qos = 1 或 2
            # 生成消息id，将消息发送至客户端，然后记录消息id。等待ack
            message_id = packet.message_id

            future = TracebackFuture()

            def handle_future(future):
                print 'handle_future'
                packet = None
                exception = ''
                try:
                    packet = future.result()
                except:
                    # 有可能是没发确认就on_close了
                    exception = future.exception()
                    # todo
                    # 客户端不发 ack 直接 close 会怎么样, handler_close 函数会有效果吗
                    # add_callback的数据，如果timeout了，内存是如何清空
                IOLoop.current().add_callback(
                    self.send_packet_cb, message_id, packet, exception)
            future.add_done_callback(handle_future)

            def handle_response(packet=None, exception=None):
                print 'handle_response'
                if packet:
                    future.set_result(packet)
                else:
                    future.set_exception(exception)
            self.future_rsp_hl[message_id] = handle_response

            toh = IOLoop.current().add_timeout(
                time.time() + timeout,
                self.send_packet_cb_timeout,
                message_id)
            self.rsp_timeout_hl[message_id] = toh

            packet.message_id = message_id
            self.write_message(packet.raw)

            return future

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
