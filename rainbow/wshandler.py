# encoding=utf-8
import time
import json
import struct
import traceback
from hashlib import sha256
import urllib
import logging as log

import tornado
from tornado.websocket import WebSocketHandler as Handler
from tornado.concurrent import TracebackFuture
from tornado.ioloop import IOLoop
from tornado.httpclient import AsyncHTTPClient
# from tornado.httpclient import HTTPClient
from tornado.httpclient import HTTPRequest
from tornado.websocket import WebSocketClosedError

from config import g_CONFIG
import settings
from api import param_signature

USER_ID_HASH = 'websocket_connected_users'


# 每个channel 当前 migid, g_channel_msgid[channel] = message_id_channel
g_channel_msgid = {}

# webhandler那边过来的, 每个 channel 的 每一条 message_id_channel 的 future hdl,
# g_channel_msgid_hdl[channel]={
# [message_id_channel]=hdl,
# [message_id_channel2]=hdl2
# }
g_channel_msgid_hdl = {}


def clear_channel_msg_data(channel):
    log.debug('channel = %s' % channel)
    if g_channel_msgid.get(channel) is not None:
        del g_channel_msgid[channel]
    if g_channel_msgid_hdl.get(channel) is not None:
        del g_channel_msgid_hdl[channel]
    log.debug('g_channel_msgid = %s' % g_channel_msgid)
    log.debug('g_channel_msgid_hdl = %s' % g_channel_msgid_hdl)


def get_next_msgid(channel):
    # 单线程？
    message_id_channel = g_channel_msgid.get(channel, 0)
    if message_id_channel >= 65534:
        message_id_channel = 0
    message_id_channel = message_id_channel + 1
    g_channel_msgid[channel] = message_id_channel
    return message_id_channel


def del_msgid_memory(channel):
    if g_channel_msgid.get(channel) is not None:
        del g_channel_msgid[channel]


def set_msg_hdl(
        channel, message_id_channel, web_handle_response, client_count):
    if not web_handle_response:
        return

    message_id_list = g_channel_msgid_hdl.get(channel, {})
    if not message_id_list:
        g_channel_msgid_hdl[channel] = message_id_list
    message_id_list[message_id_channel] = {}
    message_id_list[message_id_channel][
        'web_handle_response'] = web_handle_response
    # 客户端回复 ack 或 rec 表示收到
    message_id_list[message_id_channel]['finish_list'] = {}
    message_id_list[message_id_channel]['error_list'] = {}
    message_id_list[message_id_channel]['client_count'] = client_count
    message_id_list[message_id_channel]['rsp_data'] = ''


def clear_msg_hdl(channel, message_id_channel):
    if not g_channel_msgid_hdl.get(channel) is not None:
        return
    if not g_channel_msgid_hdl[channel].get(message_id_channel) is not None:
        return

    del g_channel_msgid_hdl[channel][message_id_channel]


# 对 webhandler的回应
def send_msg_response(channel, packet_msg_id, ws_handler, data=None, error=''):
    if ws_handler.future_rsp_hl.get(packet_msg_id) is not None:
        del ws_handler.future_rsp_hl[packet_msg_id]
    log.debug('send_msg_response data = %s' % data)

    log.debug('send_msg_response message_id')
    log.debug(packet_msg_id)
    log.debug('send_msg_response error')
    log.debug(error)

    message_id_channel = ws_handler.packet_msg_id_to_channel.get(packet_msg_id)
    if not message_id_channel:
        return
    del ws_handler.packet_msg_id_to_channel[packet_msg_id]

    if not g_channel_msgid_hdl.get(channel):
        log.info('send_msg_response return 1')
        return
    if not g_channel_msgid_hdl[channel].get(message_id_channel):
        log.info('send_msg_response return 2')
        return

    hdl_info = g_channel_msgid_hdl[channel][message_id_channel]
    if data:
        hdl_info['rsp_data'] = data
    log.debug('send_msg_response hdl_info =')
    log.debug(hdl_info)
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

    log.debug('send_msg_response error_list %d' % len(hdl_info['error_list']))
    log.debug('send_msg_response finish_count %d' % finish_count)
    log.debug('send_msg_response rsp_count %d' % rsp_count)
    log.debug('send_msg_response client_count = %d' % hdl_info['client_count'])
    if rsp_count == hdl_info['client_count']:
        # 所有客户端都有返回或者超时
        if hdl_info.get('web_handle_response'):
            response = {
                'connections': finish_count,
                'data': hdl_info['rsp_data']}
            hdl_info['web_handle_response'](response)
        clear_msg_hdl(channel, message_id_channel)


class Packet(object):

    PACKET_RESERVED = 0
    PACKET_SEND = 1
    PACKET_ACK = 2  # for QOS1
    PACKET_REC = 3  # for QOS2
    PACKET_REL = 4  # for QOS2
    PACKET_COM = 5  # for QOS2

    def __init__(self, raw=None, command=None, msgtype=None, data=None,
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
                log.error('return 1')
                return

            command = (meta[0] & 0xF0) / 16
            if command not in [
                    self.PACKET_RESERVED,
                    self.PACKET_SEND,
                    self.PACKET_ACK,
                    self.PACKET_REC,
                    self.PACKET_REL,
                    self.PACKET_COM]:
                self._valid = False
                # log.error(u' 心跳吗 return')
                return
            log.debug('Packet __init__ command = ')
            log.debug(command)
            if command == self.PACKET_RESERVED:
                self._valid = False
                log.error('return 2')
                return

            qos = (meta[0] & 0x06) / 2
            dup = 1 if meta[0] & 0x08 == 8 else 0
            if command in (self.PACKET_SEND,
                           self.PACKET_ACK,
                           self.PACKET_REC):
                data_idx = 1
                if command == self.PACKET_SEND:
                    msgtype = struct.unpack(
                        '!H', raw[data_idx:data_idx + 2])[0]
                    data_idx = data_idx + 2
                    if qos > 0:
                        message_id = struct.unpack(
                            '!H', raw[data_idx:data_idx + 2])[0]
                        data_idx = data_idx + 2
                else:
                    message_id = struct.unpack(
                        '!H', raw[data_idx:data_idx + 2])[0]
                    data_idx = data_idx + 2
                if len(raw) > data_idx:
                    # data = json.loads(raw[data_idx:])
                    data = raw[data_idx:]
            else:
                message_id = struct.unpack('!H', raw[1:3])[0]

        log.debug('command')
        log.debug(command)
        # log.debug('msgtype')
        # log.debug(msgtype)
        log.debug('qos')
        log.debug(qos)
        # log.debug('dup')
        # log.debug(dup)
        log.debug('message_id')
        log.debug(message_id)
        # log.debug('data')
        # log.debug(data)

        self.command = command  # 命令
        self.msgtype = msgtype
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
                _raw += struct.pack('!H', self.msgtype)
                if self.qos > 0:
                    _raw += struct.pack('!H', self.message_id)
            else:
                _raw += struct.pack('!H', self.message_id)
            if self.data:
                _raw += self.data

            self._raw = _raw
        return self._raw

    @property
    def valid(self):
        return self._valid

    def packet_copy(self):
        new_packet = Packet(self.raw)
        new_packet._raw = None
        return new_packet


class WebSocketHandler(Handler):
    RB_Timeout = 3
    RB_Keepalive_Timeout = 20
    socket_handlers = {}
    socket_handlers2 = {}

    def open(self):
        """
        成功创建websocket连接，保存或更新用户信息。
        """
        set_identity_hdl(self.identity, self)
        log.info('Open connection finish for %s' % self.identity)

    def on_pong(self, data):
        log.debug('on_pong data = %s' % data)
        log.debug('*' * 20)
        log.debug('*' * 20)
        toh_ping = getattr(self, 'toh_ping', None)
        if toh_ping:
            IOLoop.current().remove_timeout(toh_ping)
            self.toh_ping = None

    def keep_alive(self):
        toh_ping = getattr(self, 'toh_ping', None)
        if toh_ping:
            return

        self.toh_ping = IOLoop.current().add_timeout(
            time.time() + self.RB_Keepalive_Timeout,
            self.keepalive_close)

        self.ping('')

    @tornado.gen.coroutine
    def keepalive_close(self):
        """从handler_map移除掉handler
        """
        try:
            log.debug('keepalive_close will close handler for identity = %s' %
                      self.identity)

            self.close_funcs()

            try:
                yield self.on_close_cb()
            except Exception, e:
                log.error(e)
                log.error(traceback.format_exc())

            self.close()

        except Exception, e:
            log.warning(e)
            log.warning(traceback.format_exc())

    def handler_init(self):
        # 每个 ws 连接也要维护 消息的 future
        self.future_rsp_hl = {}
        self.rsp_timeout_hl = {}
        self.pendding_message_ids = []
        self.received_message_ids = []
        self.next_message_id = 0
        self.packet_msg_id_to_channel = {}
        self.channels = []

    def gen_wshdl_next_message_id(self):
        if self.next_message_id >= 65534:
            self.next_message_id = 1
        else:
            self.next_message_id = self.next_message_id + 1

        return self.next_message_id

    def handler_close(self):
        log.debug('handler_close self.rsp_timeout_hl =')
        log.debug(self.rsp_timeout_hl)
        for _, toh in self.rsp_timeout_hl.iteritems():
            IOLoop.current().remove_timeout(toh)

        log.debug('handler_close self.future_rsp_hl =')
        log.debug(self.future_rsp_hl)
        for message_id, handle_response in self.future_rsp_hl.iteritems():
            try:
                handle_response(exception='handler_close')
            except Exception, e:
                log.error('handler_close exception')
                log.error(e)
                log.error(traceback.format_exc())

        clear_identity_hdl(self.identity, self)

    @tornado.gen.coroutine
    def on_close(self):
        """从handler_map移除掉handler
        """
        try:
            log.info('on_close for identity = %s' % self.identity)

            try:
                yield self.on_close_cb()
            except Exception, e:
                log.error(e)
                log.error(traceback.format_exc())

            self.close_funcs()

        except Exception, e:
            log.warning(e)
            log.warning(traceback.format_exc())

    def close_funcs(self):
        self.handler_close()

        self.channel_on_close()

        log.debug('self.channels = %s' % self.channels)
        for channel in self.channels:
            handlers = WebSocketHandler.socket_handlers2.get(channel, None)
            if not handlers:
                # 如果这个 channel 的没有客户端
                clear_channel_msg_data(channel)

        log.debug('WebSocketHandler.socket_handlers2 = %s' %
                  WebSocketHandler.socket_handlers2)

    @classmethod
    def send_message(
        cls, channel, msgtype, data,
            qos=0, timeout=0, web_handle_response=None):
        handlers = WebSocketHandler.socket_handlers2.get(channel, None)
        log.debug('send_message WebSocketHandler.socket_handlers2 = %s' %
                  WebSocketHandler.socket_handlers2)
        log.debug('channel = %s' % channel)

        if handlers:

            # 记录 消息 的回调情况
            client_count = len(handlers)
            message_id_channel = get_next_msgid(channel)
            set_msg_hdl(channel, message_id_channel,
                        web_handle_response, client_count)

            if timeout > 1:
                timeout = timeout - 1

            cnt = int(timeout / WebSocketHandler.RB_Timeout)  # 重试次数
            for identity, handler in handlers.iteritems():
                handler.send_packet(
                    channel, message_id_channel,
                    msgtype, data, qos, cnt)

            if qos == 0:
                web_handle_response({})
        else:
            # 没有客户端在线
            if web_handle_response:
                web_handle_response({'connections': 0, 'data': ''})

    def send_packet(
            self, channel, message_id_channel,
            msgtype, data, qos=0, cnt=0):
        """此方法需要返回Future
        """
        packet = Packet(command=Packet.PACKET_SEND,
                        msgtype=msgtype,
                        data=data, qos=qos)

        # 因为一个 wshandler 可能存在多个 channel 中, 所以要各个 wshandler 维护 msg_id
        # 还要 和 这次发送的 message_id_channel 保持好映射
        # 建立映射的同时也要做好取消映射，节约内存

        if packet.qos == 0:
            try:
                self.write_message(packet.raw, binary=True)
            except WebSocketClosedError:
                log.warning(u'发消息前, 客户端关闭了WebSocketClosedError')
        else:
            message_id = self.gen_wshdl_next_message_id()
            self.packet_msg_id_to_channel[message_id] = message_id_channel
            packet.message_id = message_id
            future = TracebackFuture()
            log.debug('channel = %s, message_id_channel = %s, '
                      'packet.message_id = %s' % (
                          channel, message_id_channel, message_id))

            def handle_future(future):
                log.debug('send_packet handle_future func')
                packet = None
                exception = ''
                try:
                    packet = future.result()
                except:
                    # 有可能是没发确认就 on_close 了
                    exception = future.exception()
                    # 1.如果timeout了，是不会调用下面的 handle_response
                    #   然后也不会再调用 handle_future, 不会有 add_callback
                    # 2.客户端不发 ack 而直接 close
                    # 会调用 handle_response(exception='on_close')
                IOLoop.current().add_callback(
                    self.send_packet_cb, channel,
                    message_id, packet, exception)
            future.add_done_callback(handle_future)

            def handle_response(packet=None, exception=None):
                if packet:
                    future.set_result(packet)
                else:
                    future.set_exception(exception)
            self.future_rsp_hl[message_id] = handle_response

            toh = IOLoop.current().add_timeout(
                time.time() + self.RB_Timeout,
                self.send_packet_cb_timeout,
                channel, message_id, packet, cnt)

            self.rsp_timeout_hl[message_id] = toh
            try:
                self.write_message(packet.raw, binary=True)
                log.debug('self.write_message = %s' % packet.raw)
            except Exception, e:
                log.warning(e)
                log.warning(traceback.format_exc())

            return future

    def on_message(self, message):
        """message的前两个以上的字节为header，表示这是什么样的数据包及数据包长度。
        - send
        - send_ack  //for QOS1。
        - send_receive  //for QOS2, 下同。
        - send_release
        - send_complete
        """
        log.debug(message)
        try:
            if not message:
                log.debug('if not message:')
                return
            pck = Packet(raw=message)
            if not pck.valid:
                log.debug('if not pck.valid')
                return

            {Packet.PACKET_SEND: self.on_packet_send,
             Packet.PACKET_ACK: self.on_packet_ack,
             Packet.PACKET_REC: self.on_packet_rec,
             Packet.PACKET_REL: self.on_packet_rel,
             Packet.PACKET_COM: self.on_packet_com}[pck.command](pck)
        except Exception:
            log.error('handle message error %s' % message, exc_info=True)

    def packet_send_business_server(self, packet):
        url = g_CONFIG['forward_url'] + str(packet.msgtype) + '/'

        req = self.make_request(url, 'POST', body=packet.data)

        http_client = AsyncHTTPClient()

        return http_client.fetch(req)

    # 客户端主动发消息的相应
    @tornado.gen.coroutine
    def on_packet_send(self, packet):
        """收到PACKET_SEND消息
        """
        try:
            log.debug('on_packet_send func')
            if packet.qos == 2 and \
                    (packet.dup == 1 and
                        packet.message_id in self.received_message_ids):
                log.info(u'重复消息，不再发去业务服务器')
                return
            else:
                try:
                    if packet.qos == 2:
                        self.received_message_ids.append(packet.message_id)
                    rsp = yield self.packet_send_business_server(packet)
                    self.rainbow_handle_header(rsp.headers)
                    data = rsp.body or None
                except Exception, e:
                    if packet.qos == 2:
                        self.received_message_ids.remove(packet.message_id)
                    log.info(e)
                    log.info(traceback.format_exc())
                    if settings.DEBUG:
                        data = traceback.format_exc()
                    else:
                        return

            if packet.qos == 0:
                return
            elif packet.qos == 1:
                command = Packet.PACKET_ACK
            elif packet.qos == 2:
                command = Packet.PACKET_REC
            rp = Packet(command=command,
                        message_id=packet.message_id,
                        data=data)
            self.write_message(rp.raw, binary=True)

        except Exception, e:
            log.info(e)
            log.info(traceback.format_exc())

    # 服务器主动发消息的返回
    def on_packet_ack(self, packet):
        """收到PACKET_ACK消息, 结果要通知给http的回调者。这里要怎么搞？
        """
        log.debug('on_packet_ack func')

        handle_response = self.future_rsp_hl.get(packet.message_id)
        if handle_response:
            del self.future_rsp_hl[packet.message_id]
            # 这里会 set future
            handle_response(packet)

    # 服务器主动发消息的返回
    def on_packet_rec(self, packet):
        """收到PACKET_REC消息, 要通知给http调用者。
        """
        log.debug('on_packet_rec func')

        handle_response = self.future_rsp_hl.get(packet.message_id)
        if handle_response:
            del self.future_rsp_hl[packet.message_id]
            handle_response(packet)
            # 如果我 响应 业务服务器，业务方已经超时退出
            # 但是 rainbow 已经保证了此次发送成功并且只发送一次
            # 所以直接回复 rel 是 OK 的

        rp = Packet(command=Packet.PACKET_REL, message_id=packet.message_id)
        try:
            self.write_message(rp.raw, binary=True)
        except Exception, e:
            log.warning(u'发消息前, 客户端关闭了WebSocketClosedError')
            log.warning(e)
            log.warning(traceback.format_exc())

    # 客户端主动发消息的相应
    def on_packet_rel(self, packet):
        """收到PACKET_REL消息，删除消息ID，返回COM消息。
        """
        log.debug('on_packet_rel func')
        if packet.message_id not in self.received_message_ids:
            pass
        else:
            self.received_message_ids.remove(packet.message_id)

        rp = Packet(command=Packet.PACKET_COM, message_id=packet.message_id)
        try:
            self.write_message(rp.raw, binary=True)
        except Exception, e:
            log.warning(u'发消息前, 客户端关闭了WebSocketClosedError')
            log.warning(e)
            log.warning(traceback.format_exc())

    # 服务器主动发消息的返回
    def on_packet_com(self, packet):
        """收到PACKET_COM消息。删除消息ID即可。
        """
        log.debug('on_packet_com func')
        # todo 收不到 com, 要多发几次rel

    # 服务器主动发消息成功后的回调
    def send_packet_cb(self, channel, message_id, packet, exception):
        log.debug('send_packet_cb func')
        if self.rsp_timeout_hl:
            # 如果是on_close 触发的 回调，就已经去掉timeout了
            toh = self.rsp_timeout_hl.get(message_id)
            if toh:
                IOLoop.current().remove_timeout(toh)
                del self.rsp_timeout_hl[message_id]

        data = packet.data if packet else ''
        send_msg_response(
            channel, message_id, self, data=data, error=exception)

    # 服务器主动发消息后的超时
    def send_packet_cb_timeout(self, channel, message_id, packet, cnt):
        log.debug('send_packet_cb_timeout func')
        if cnt <= 0:
            toh = self.rsp_timeout_hl.get(message_id)
            if toh:
                del self.rsp_timeout_hl[message_id]
            send_msg_response(channel, message_id, self, error='timeout')

            # 客户端可能断网或者切换了网络
            self.keep_alive()
        else:
            dup_packet = packet.packet_copy()
            dup_packet.dup = 1
            toh = IOLoop.current().add_timeout(
                time.time() + self.RB_Timeout,
                self.send_packet_cb_timeout,
                channel, message_id, dup_packet, cnt - 1)
            self.rsp_timeout_hl[message_id] = toh
            try:
                self.write_message(dup_packet.raw, binary=True)
            except Exception, e:
                log.warning(u'发消息前, 客户端关闭了WebSocketClosedError')
                log.warning(e)
                log.warning(traceback.format_exc())

    @classmethod
    def shutdown(cls):
        """把当前线程所有的用户状态标记为离线。
        再关闭用户连接。
        """
        for channel, handlers in WebSocketHandler.socket_handlers2.iteritems():
            for identity, handler in handlers.iteritems():
                log.debug('identity = %s close' % identity)
                handler.close()
        # shutdown 不会引起 on_close

    def channel_add(self, channel):
        log.debug('channel_add func')
        self.channels.append(channel)

        log.debug('channel_add WebSocketHandler.socket_handlers2 = %s' %
                  WebSocketHandler.socket_handlers2)

        handlers = WebSocketHandler.socket_handlers2.get(channel, None)
        if handlers:
            handlers[self.identity] = self
        else:
            WebSocketHandler.socket_handlers2[channel] = {}
            WebSocketHandler.socket_handlers2[channel][self.identity] = self
        log.debug('channel_add WebSocketHandler.socket_handlers2 = %s' %
                  WebSocketHandler.socket_handlers2)

    def channel_remove(self, channel):
        log.debug('channel_remove func')
        log.debug('channel_add WebSocketHandler.socket_handlers2 = %s' %
                  WebSocketHandler.socket_handlers2)
        self._channel_remove(channel)
        log.debug('before self.channels = %s' % self.channels)
        self.channels.remove(channel)
        log.debug('after self.channels = %s' % self.channels)
        log.debug('channel_add WebSocketHandler.socket_handlers2 = %s' %
                  WebSocketHandler.socket_handlers2)

    def _channel_remove(self, channel):
        handlers = WebSocketHandler.socket_handlers2.get(channel, None)
        if handlers:
            hdl = handlers.get(self.identity)
            if hdl:
                del handlers[self.identity]
            if not handlers:
                del WebSocketHandler.socket_handlers2[channel]

    def channel_occupy(self, channel):
        """ 独占这个channel
            将所有其它连接退订这个channel
            然后把自己订阅这个channel
        """
        handlers = WebSocketHandler.socket_handlers2.get(channel, None)
        if handlers:
            for _, wshandler in handlers.iteritems():
                wshandler.channels.remove(channel)
            del WebSocketHandler.socket_handlers2[channel]
        self.channel_add(channel)

    def channel_on_close(self):
        for channel in self.channels:
            self._channel_remove(channel)

    @tornado.gen.coroutine
    def get(self, *args, **kwds):
        """在打开websocket前先进行权限校验，如果没权限直接断开连接。
        """
        try:
            self.handler_init()
            log.debug(u'在打开websocket前先进行权限校验，如果没权限直接断开连接')

            connect_flag = False
            req = self.get_valid_req_params()
            if req:
                try:
                    rsp = yield self.valid_request(req)
                    body = json.loads(rsp.body)
                    log.debug(u'get func body = %s' % body)
                    if body['status'] == 'success':
                        connect_flag = True
                        self.rainbow_handle_header(rsp.headers)
                except Exception, e:
                    log.warning(e)
                    log.warning(traceback.format_exc())
            if not connect_flag:
                log.warning('invalid request, close!')
                self.set_status(401)
                self.finish('HTTP/1.1 401 Unauthorized\r\n\r\n'
                            'Not authenticated')
                return
            try:
                super(WebSocketHandler, self).get(*args, **kwds)
            except Exception, e:
                log.info(u'业务服务器校验返回前,客户端已经关闭了')
                log.info(e)
                log.info(traceback.format_exc())

        except Exception, e:
            log.warning(e)
            log.warning(traceback.format_exc())

    def check_origin(self, origin):
        """ 在 get 会去验证 connect 这里返回 True就可以
        """
        log.debug('check_origin func')
        return True

    def valid_request(self, req):
        """检查该请求是否合法，如果合法则返回channel，否则为空。
        """
        log.debug('valid_request func')

        http_client = AsyncHTTPClient()
        return http_client.fetch(req)

    def get_valid_req_params(self):
        log.debug('self.request.arguments = %s' % self.request.arguments)
        log.debug('self.request.headers = %s' % self.request.headers)

        headers = self.request.headers
        deviceid1 = 'X-DEVICEID'
        deviceid2 = 'X_deviceid'
        deviceid3 = 'X-Deviceid'
        deviceid = headers.get(deviceid1) or headers.get(
            deviceid2) or headers.get(deviceid3) or \
            self.get_query_argument(deviceid1, '')
        platform1 = 'X-CLIENT-OS'
        platform2 = 'X_client_os'
        platform3 = 'X-Client-Os'
        platform = headers.get(platform1) or headers.get(
            platform2) or headers.get(platform3) or \
            self.get_query_argument(platform1, '')
        if not deviceid or not platform:
            log.warning('if not deviceid or not platform')
            return None
        ip = self.request.remote_ip

        identity_raw = '%s.%s.%s.%f' % (platform, deviceid, ip, time.time())
        log.debug('identity_raw = %s' % identity_raw)
        self.identity = sha256(identity_raw).hexdigest()

        req_headers = {}
        for k, v in headers.iteritems():
            req_headers[k] = v
        for k, v in self.request.arguments.iteritems():
            req_headers[k] = v[0]

        if req_headers.get('Upgrade') is not None:
            del req_headers['Upgrade']
        if req_headers.get('Sec-Websocket-Version') is not None:
            del req_headers['Sec-Websocket-Version']
        if req_headers.get('Sec-Websocket-Key') is not None:
            del req_headers['Sec-Websocket-Key']
        if req_headers.get('Connection') is not None:
            del req_headers['Connection']
        if req_headers.get('Origin') is not None:
            del req_headers['Origin']
        if req_headers.get('Host') is not None:
            del req_headers['Host']  # Host 是坏人，会导致nginx502和599

        req = self.make_request(
            g_CONFIG['connect_url'], 'GET', headers=req_headers)

        return req

    def make_request(
            self, url, method, params=None, headers=None, body=None):
        headers = self.rainbow_add_header(headers)

        if not params:
            params = {}
        param_sign = param_signature()
        for key, value in param_sign.iteritems():
            params[key] = value
        params_str = urllib.urlencode(params)

        url = '%s?%s' % (url, params_str)
        req = HTTPRequest(
            url=url, method=method, headers=headers, body=body,
            connect_timeout=10, request_timeout=10)
        log.debug('\n')
        log.debug('req.headers = %s' % req.headers)
        log.debug('req.url = %s' % req.url)
        log.debug('req.body = %s' % req.body)
        log.debug('\n')
        return req

    def on_close_cb(self):
        log.debug('on_close_cb func')
        req = self.make_request(g_CONFIG['close_url'], 'GET')
        http_client = AsyncHTTPClient()
        return http_client.fetch(req)

    def rainbow_add_header(self, headers=None):
        if not headers:
            headers = {}
        headers['RAINBOWCLIENTIDENTITY'] = self.identity
        rainbow_cookie = getattr(self, 'rainbow_cookie', '')
        if rainbow_cookie:
            headers['RAINBOWCLIENTCOOKIE'] = rainbow_cookie

        return headers

    def rainbow_handle_header(self, headers):
        log.debug('headers = %s' % headers)
        h_cookie1 = 'Rainbowclientcookie'
        h_cookie2 = 'RAINBOWCLIENTCOOKIE'
        rainbow_cookie = headers.get(h_cookie1) or headers.get(h_cookie2)
        log.debug('RAINBOWCLIENTCOOKIE = %s' % rainbow_cookie)
        if rainbow_cookie is not None:
            self.rainbow_cookie = rainbow_cookie

        h_channel1 = 'RAINBOWCLIENTCHANNEL'
        h_channel2 = 'Rainbowclientchannel'
        channel = headers.get(h_channel1) or headers.get(h_channel2)
        log.debug('RAINBOWCLIENTCHANNEL = %s' % channel)
        if channel:
            self.channel_add(channel)


# identity 与 websocket handler 的映射
g_identity_wshandler = {}


def set_identity_hdl(identity, wshandler):
    """ 用户 sub/unsub 的时候，根据 identity 找到 wshandler
    """
    g_identity_wshandler[identity] = wshandler


def clear_identity_hdl(identity, wshandler):
    if g_identity_wshandler.get(identity):
        del g_identity_wshandler[identity]


def sub(identity, channel, occupy=False):
    """ occupy 是否独占这个channel
    """
    wshandler = g_identity_wshandler.get(identity)
    if wshandler:
        if occupy:
            wshandler.channel_occupy(channel)
        else:
            wshandler.channel_add(channel)
        return True, None
    log.warning('no such identity = %s' % identity)
    return False, 'no such connection'


def unsub(identity, channel):
    wshandler = g_identity_wshandler.get(identity)
    if wshandler:
        wshandler.channel_remove(channel)
    return True, None
