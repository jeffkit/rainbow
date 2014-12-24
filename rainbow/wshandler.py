# encoding=utf-8
import time
import json
import struct
import traceback
from hashlib import sha256

import tornado
from tornado.websocket import WebSocketHandler as Handler
from tornado.concurrent import TracebackFuture
from tornado.ioloop import IOLoop
from tornado.httpclient import AsyncHTTPClient
# from tornado.httpclient import HTTPClient
from tornado.httpclient import HTTPRequest
from tornado.websocket import WebSocketClosedError
import redis

from config import g_CONFIG
import logging as log
log.basicConfig(level='DEBUG')
import settings
redis_client = redis.Redis(
    settings.REDIS_HOST,
    settings.REDIS_PORT,
    settings.REDIS_DATABASE,
    settings.REDIS_PASSWORD)
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
    if g_channel_msgid.get(channel):
        del g_channel_msgid[channel]
    if g_channel_msgid_hdl.get(channel):
        del g_channel_msgid_hdl[channel]


def get_next_msgid(channel):
    # 单线程？
    message_id_channel = g_channel_msgid.get(channel, 0)
    if message_id_channel >= 65535:
        message_id_channel = 0
    message_id_channel = message_id_channel + 1
    g_channel_msgid[channel] = message_id_channel
    return message_id_channel


def del_msgid_memory(channel):
    if g_channel_msgid.get(channel):
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


def clear_msg_hdl(channel, message_id_channel):
    if not g_channel_msgid_hdl.get(channel):
        return
    if not g_channel_msgid_hdl[channel].get(message_id_channel):
        return

    del g_channel_msgid_hdl[channel][message_id_channel]


# 对 webhandler的回应
def send_msg_response(channel, packet_msg_id, ws_handler, error=''):
    if ws_handler.future_rsp_hl.get(packet_msg_id):
        del ws_handler.future_rsp_hl[packet_msg_id]

    log.info('send_msg_response ')
    log.info('send_msg_response message_id')
    log.info(packet_msg_id)
    log.info('send_msg_response error')
    log.info(error)

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
    log.info('send_msg_response hdl_info =')
    log.info(hdl_info)
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

    log.info('send_msg_response error_list %d' % len(hdl_info['error_list']))
    log.info('send_msg_response finish_count %d' % finish_count)
    log.info('send_msg_response rsp_count %d' % rsp_count)
    log.info('send_msg_response client_count = %d' % hdl_info['client_count'])
    if rsp_count == hdl_info['client_count']:
        # 所有客户端都有返回或者超时
        if hdl_info['web_handle_response']:
            hdl_info['web_handle_response'](finish_count)
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
            log.info('Packet __init__ command = ')
            log.info(command)
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

        log.info('command')
        log.info(command)
        log.info('msgtype')
        log.info(msgtype)
        log.info('qos')
        log.info(qos)
        log.info('dup')
        log.info(dup)
        log.info('message_id')
        log.info(message_id)
        log.info('data')
        log.info(data)

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


class WebSocketHandler(Handler):
    QOS_LEVEL1 = 0
    QOS_LEVEL2 = 1
    QOS_LEVEL3 = 2

    socket_handlers = {}
    socket_handlers2 = {}

    def open(self):
        """
        成功创建websocket连接，保存或更新用户信息。
        """
        log.info('Open connection for %s' % self.identity)

        # todo 这个有什么用的
        # try:
        #     redis_client.hsetnx(USER_ID_HASH, self.uid, 1)
        # except:
        #     pass

        set_identity_hdl(self.identity, self)
        log.info('Open connection for %s finish' % self.identity)

    def handler_init(self):
        # 每个 ws 连接也要维护 消息的 future
        log.info('handler_init func')
        self.future_rsp_hl = {}
        self.rsp_timeout_hl = {}
        self.pendding_message_ids = []
        self.received_message_ids = []
        self.next_message_id = 0
        self.packet_msg_id_to_channel = {}
        self.channels = []

    def gen_next_message_id(self):
        if self.next_message_id >= 65534:
            self.next_message_id = 1
        else:
            self.next_message_id = self.next_message_id + 1

        return self.next_message_id

    def handler_close(self):
        log.info('handler_close self.rsp_timeout_hl =')
        log.info(self.rsp_timeout_hl)
        for _, toh in self.rsp_timeout_hl.iteritems():
            log.info('handle_response toh = ')
            log.info(toh)
            IOLoop.current().remove_timeout(toh)

        log.info('handler_close self.future_rsp_hl =')
        log.info(self.future_rsp_hl)
        for message_id, handle_response in self.future_rsp_hl.iteritems():
            log.info('handler_close message_id = %d' % message_id)
            log.info('handler_close self id = %d' % id(self))
            log.info('handler_close id(handle_response)=%d' %
                     id(handle_response))
            try:
                handle_response(exception='on_close')
            except Exception, e:
                log.error('handler_close exception')
                log.error(e)
                log.error(traceback.format_exc())

        self.future_rsp_hl = None
        self.rsp_timeout_hl = None
        clear_identity_hdl(self.identity, self)

    @tornado.gen.coroutine
    def on_close(self):
        """从handler_map移除掉handler
        """
        log.info('on_close will close handler for user')

        try:
            rsp = yield self.on_close_cb()
            log.debug(dir(rsp))
        except Exception, e:
            log.error(e)
            log.error(traceback.format_exc())

        self.handler_close()

        self.channel_on_close()

        for channel in self.channels:
            handlers = WebSocketHandler.socket_handlers2.get(channel, None)
            if not handlers:
                # 如果这个 channel 的没有客户端
                clear_channel_msg_data(self.channel)

        log.info('handlers after close %d' %
                 len(WebSocketHandler.socket_handlers2))

        # todo 这个用来干嘛的
        # try:
        #     if redis_client.hexists(USER_ID_HASH, self.uid):
        #         redis_client.hdel(USER_ID_HASH, self.uid)
        #     redis_client.delete(self.get_packet_status_key(self.uid))
        # except:
        #     pass

        # if self.uid in self.client_versions:
        #     del self.client_versions[self.uid]
        # 干嘛用的？

        self.close_flag = True

    @classmethod
    def send_message(
        cls, channel, msgtype, data,
            qos=0, timeout=0, web_handle_response=None):
        handlers = WebSocketHandler.socket_handlers2.get(channel, None)
        log.info('send_message WebSocketHandler.socket_handlers2 = %s' %
                 WebSocketHandler.socket_handlers2)
        if handlers:

            # 记录 消息 的回调情况
            client_count = len(handlers)
            message_id_channel = get_next_msgid(channel)
            set_msg_hdl(channel, message_id_channel,
                        web_handle_response, client_count)

            if timeout > 1:
                # todo 处理重发的
                timeout = timeout - 1
            for identity, handler in handlers.iteritems():
                handler.send_packet(
                    channel, message_id_channel,
                    msgtype, data, qos, timeout)
        else:
            # 没有客户端在线
            if web_handle_response:
                web_handle_response(0)

    def send_packet(
            self, channel, message_id_channel,
            msgtype, data, qos=0, timeout=0):
        """此方法需要返回Future
        """
        packet = Packet(command=Packet.PACKET_SEND,
                        msgtype=msgtype,
                        data=data, qos=qos)
        packet.message_id = self.gen_next_message_id()

        # 因为一个 wshandler 可能存在多个 channel 中, 所以要各个 wshandler 维护 msg_id
        # 还要 和 这次发送的 message_id_channel 保持好映射
        # 建立映射的同时也要做好取消映射，节约内存
        self.packet_msg_id_to_channel[packet.message_id] = message_id_channel

        if packet.qos == self.QOS_LEVEL1:
            self.write_message(packet.raw, binary=True)
        else:
            # qos = 1 或 2
            # 生成消息id，将消息发送至客户端，然后记录消息id。等待ack
            message_id = packet.message_id
            future = TracebackFuture()

            def handle_future(future):
                log.info('send_packet handle_future func')
                packet = None
                exception = ''
                try:
                    packet = future.result()
                except:
                    # 有可能是没发确认就 on_close 了
                    exception = future.exception()
                    # todo, 记下来, 如果timeout了，是不会有 下面的 handle_response
                    # 客户端不发 ack 而直接 close 会怎么样, handler_close 函数会有效果吗
                    # add_callback 的数据，如果 timeout 了，内存是如何清空
                IOLoop.current().add_callback(
                    self.send_packet_cb, channel,
                    message_id, packet, exception)
            future.add_done_callback(handle_future)

            def handle_response(packet=None, exception=None):
                log.info('send_packet handle_response')
                log.info('send_packet message_id = %d' % message_id)
                log.info('send_packet future id = %d' % id(future))
                if packet:
                    future.set_result(packet)
                else:
                    future.set_exception(exception)
            self.future_rsp_hl[message_id] = handle_response
            log.info('send_packet future id = %d' % id(future))
            log.info('send_packet self id = %d' % id(self))
            log.info('send_packet handle_response id = %d' %
                     id(handle_response))

            toh = IOLoop.current().add_timeout(
                time.time() + timeout,
                self.send_packet_cb_timeout,
                channel,
                message_id)
            log.info('send_packet  toh = ')
            log.info(toh)
            log.info('send_packet  message_id = ')
            log.info(message_id)

            self.rsp_timeout_hl[message_id] = toh

            packet.message_id = message_id

            try:
                self.write_message(packet.raw, binary=True)
            except WebSocketClosedError:
                log.info('WebSocketClosedError')

            return future

    def on_message(self, message):
        """message的前两个以上的字节为header，表示这是什么样的数据包及数据包长度。
        - send
        - send_ack  //for QOS1。
        - send_receive  //for QOS2, 下同。
        - send_release
        - send_complete
        """
        log.info("***************************")
        log.info("***************************")
        log.info("***************************")
        log.info('on_message')
        log.info(message)
        try:
            if not message:
                log.error('if not message:')
                return
            pck = Packet(raw=message)
            if not pck.valid:
                # log.error('if not pck.valid')
                return

            # for test
            # pck.command = 1
            # pck.qos = 2
            # pck.data = "{'haha': 'jjj'}"
            # pck.dup = 0

            {Packet.PACKET_SEND: self.on_packet_send,
             Packet.PACKET_ACK: self.on_packet_ack,
             Packet.PACKET_REC: self.on_packet_rec,
             Packet.PACKET_REL: self.on_packet_rel,
             Packet.PACKET_COM: self.on_packet_com}[pck.command](pck)
        except Exception:
            log.error('handle message error %s' % message, exc_info=True)

    def packet_send_business_server(self, packet):
        http_client = AsyncHTTPClient()
        headers = self.rainbow_add_header()
        url = g_CONFIG['forward_url'] + str(packet.msgtype) + '/'
        log.info('url = %s' % url)
        body = packet.data
        req = HTTPRequest(
            url=url, method='POST', body=body, headers=headers)
        return http_client.fetch(req)

    # 客户端主动发消息的相应
    @tornado.gen.coroutine
    def on_packet_send(self, packet):
        """收到PACKET_SEND消息
        """
        log.info('on_packet_send func')

        if packet.qos == 2 and \
                (packet.dup == 1 or
                    packet.message_id in self.received_message_ids):
            # 重复消息，不再发去业务服务器
            pass
        else:
            try:
                rsp = yield self.packet_send_business_server(packet)
                self.rainbow_handle_header(rsp.headers)
            except Exception, e:
                log.info(e)
                log.info(traceback.format_exc())
                return
            if packet.qos == 2:
                self.received_message_ids.append(packet.message_id)

        data = rsp.body or None

        log.info('rsp.body = ')
        log.info(data)
        if packet.qos == 0:
            return
        elif packet.qos == 1:
            command = Packet.PACKET_ACK
        elif packet.qos == 2:
            command = Packet.PACKET_REC
        rp = Packet(command=command,
                    message_id=packet.message_id,
                    data=data)
        # todo test
        # http回来前, websocket 已经关闭, 是什么效果
        # on_close已经把 futrue 回复了一个close
        self.write_message(rp.raw, binary=True)
        log.info('after self.write_message(rp.raw)')

    # 服务器主动发消息的返回
    def on_packet_ack(self, packet):
        """收到PACKET_ACK消息, 结果要通知给http的回调者。这里要怎么搞？
        """
        log.info('on_packet_ack func')

        handle_response = self.future_rsp_hl.get(packet.message_id)
        if handle_response:
            # 这里会 set future
            handle_response(packet)

    # 服务器主动发消息的返回
    def on_packet_rec(self, packet):
        """收到PACKET_REC消息, 要通知给http调用者。
        """
        log.info('on_packet_rec func')

        if packet.message_id not in self.pendding_message_ids:
            handle_response = self.future_rsp_hl.get(packet.message_id)
            if handle_response:
                handle_response(packet)

        packet = Packet(command=Packet.PACKET_REL,
                        message_id=packet.message_id)
        self.write_message(packet.raw, binary=True)

    # 客户端主动发消息的相应
    def on_packet_rel(self, packet):
        """收到PACKET_REL消息，删除消息ID，返回COM消息。
        """
        log.info('on_packet_rel func')

        if packet.message_id not in self.received_message_ids:
            return
        self.received_message_ids.remove(packet.message_id)
        rp = Packet(command=Packet.PACKET_COM, message_id=packet.message_id)
        self.write_message(rp.raw, binary=True)

    # 服务器主动发消息的返回
    def on_packet_com(self, packet):
        """收到PACKET_COM消息。删除消息ID即可。
        """
        log.info('on_packet_com func')

        if packet.message_id in self.pendding_message_ids:
            self.pendding_message_ids.remove(packet.message_id)

    # 服务器主动发消息成功后的回调
    def send_packet_cb(self, channel, message_id, packet, exception):
        log.info('send_packet_cb func')
        if self.rsp_timeout_hl:
            # 如果是on_close 触发的 回调，就已经去掉timeout了
            toh = self.rsp_timeout_hl.get(message_id)
            if toh:
                IOLoop.current().remove_timeout(toh)
                del self.rsp_timeout_hl[message_id]
        send_msg_response(channel, message_id, self, error=exception)

    # 服务器主动发消息后的超时
    def send_packet_cb_timeout(self, channel, message_id):
        log.info('send_packet_cb_timeout func')
        toh = self.rsp_timeout_hl.get(message_id)
        if toh:
            del self.rsp_timeout_hl[message_id]
        send_msg_response(channel, message_id, self, error='timeout')

    @classmethod
    def shutdown(cls):
        """把当前线程所有的用户状态标记为离线。
        再关闭用户连接。
        """
        # todo 改，已经用了 map
        for channel, handler in cls.socket_handlers.iteritems():
            if isinstance(handler, list):
                for h in handler:
                    log.info('close here 1')
                    h.close()
            else:
                handler.close()
                log.info('close here 2')
        # todo shutdown 会调用 on_close 吗

    def channel_add(self, channel):
        log.info('channel_add func')
        self.channels.append(channel)

        log.info('channel_add WebSocketHandler.socket_handlers2 = %s' %
                 WebSocketHandler.socket_handlers2)

        handlers = WebSocketHandler.socket_handlers2.get(channel, None)
        if handlers:
            handlers[self.identity] = self
        else:
            WebSocketHandler.socket_handlers2[channel] = {}
            WebSocketHandler.socket_handlers2[channel][self.identity] = self
        log.info('channel_add WebSocketHandler.socket_handlers2 = %s' %
                 WebSocketHandler.socket_handlers2)

    def channel_remove(self, channel):
        log.info('channel_remove func')
        self._channel_remove(channel)
        self.channels.remove(channel)

    def _channel_remove(self, channel):
        handlers = WebSocketHandler.socket_handlers2.get(channel, None)
        if handlers:
            hdl = handlers.get(self.identity)
            if hdl:
                del handlers[self.identity]

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
        self.handler_init()
        log.info(u'get func 在打开websocket前先进行权限校验，如果没权限直接断开连接')

        connect_flag = False
        req = self.get_valid_req_params()
        if req:
            try:
                rsp = yield self.valid_request(req)
                body = json.loads(rsp.body)
                log.info(u'get func body = %s' % body)
                if body['status'] == 'success':
                    connect_flag = True
                    self.rainbow_handle_header(rsp.headers)
            except Exception, e:
                log.info(e)
                log.info(traceback.format_exc())

        if not connect_flag:
            log.warning('invalid request, close!')
            self.set_status(401)
            self.finish('HTTP/1.1 401 Unauthorized\r\n\r\nNot authenticated')
            return
        log.info('super(WebSocketHandler, self).get(*args, **kwds)')
        super(WebSocketHandler, self).get(*args, **kwds)

    def check_origin(self, origin):
        """ 在 get 会去验证 connect 这里返回 True就可以
        """
        log.info('check_origin func')
        return True

    def valid_request(self, req):
        """检查该请求是否合法，如果合法则返回channel，否则为空。
        """
        log.info('valid_request func')

        http_client = AsyncHTTPClient()
        return http_client.fetch(req)

    def on_close_cb(self):
        log.info('on_close_cb func')
        url = g_CONFIG['close_url']

        headers = self.rainbow_add_header()
        req = HTTPRequest(
            url=url, method='GET', headers=headers)
        http_client = AsyncHTTPClient()
        return http_client.fetch(req)

    def get_valid_req_params(self):
        log.info('get_valid_req_params func')
        log.info(self.request.arguments)
        headers = self.request.headers
        deviceid1 = 'X_DEVICEID'
        deviceid2 = 'X_deviceid'
        deviceid = headers.get(deviceid1) or headers.get(
            deviceid2) or self.get_query_argument(deviceid1, '')
        platform1 = 'X_CLIENT_OS'
        platform2 = 'X_client_os'
        platform = headers.get(platform1) or headers.get(
            platform2) or self.get_query_argument(platform1, '')
        if not deviceid or not platform:
            log.warning('if not deviceid or not platform')
            # return None

        identity_raw = '%s.%s.%f' % (platform, deviceid, time.time())
        self.identity = sha256(identity_raw).hexdigest()

        url = g_CONFIG['connect_url']

        headers = self.rainbow_add_header(headers)

        log.info(headers)

        req = HTTPRequest(
            url=url, method='GET', headers=headers)

        return req

    def rainbow_add_header(self, headers=None):
        if not headers:
            headers = {}
        headers['RAINBOW_CLIENT_IDENTITY'] = self.identity
        headers['RAINBOW_CLIENT_COOKIE'] = getattr(self, 'rainbow_cookie', '')

        return headers

    def rainbow_handle_header(self, headers):
        h_cookie1 = 'Rainbow_client_cookie'
        h_cookie2 = 'RAINBOW_CLIENT_COOKIE'
        rainbow_cookie = headers.get(h_cookie1) or headers.get(h_cookie2) or ''
        self.rainbow_cookie = rainbow_cookie

        h_channel1 = 'RAINBOW_CLIENT_CHANNEL'
        h_channel2 = 'Rainbow_client_channel'
        channel = headers.get(h_channel1) or headers.get(h_channel2)
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
