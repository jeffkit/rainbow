# encoding=utf-8

import sys
import os
parentdir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, parentdir)
import random
import logging as log
import traceback
import thread
import time
from optparse import OptionParser
from uuid import uuid4
from websocket import create_connection

from wshandler import Packet
import settings
log.basicConfig(level=settings.LOG_LEVEL, format=settings.LOG_FORMAT)

from config import g_CONFIG


def init_config():
    parser = OptionParser()
    try:
        parser.add_option("--host", dest="wshost", type="string")
        parser.add_option(
            "-c", "--channelcnt", dest="channelcnt", type="int")
        parser.add_option("-l", "--linkcnt", dest="linkcnt", type="int")
        options, args = parser.parse_args()
    except Exception, e:
        log.error(e)
        log.error(traceback.format_exc())
        return False

    if not getattr(options, 'channelcnt', None):
        log.error('Options miss -c')
        return False
    if not getattr(options, 'wshost', None):
        log.error('Options miss --host')
        return False
    if not getattr(options, 'linkcnt', None):
        log.error('Options miss -l')
        return False

    g_CONFIG['wshost'] = options.wshost
    g_CONFIG['channelcnt'] = options.channelcnt
    g_CONFIG['linkcnt'] = options.linkcnt

    log.info(g_CONFIG)
    return True


class websocket_test(object):

    def create_connection(self, channel):
        header = ["X-CLIENT-OS: ios7", ]
        c = channel
        header.append('User-Agent: %d' % c)

        device_id = ('X-DEVICEID: ajok%fmk%sdm'
                     '%f' % (time.time(),
                             uuid4().get_hex(), random.uniform(1, 10000000)))
        header.append(device_id)
        url = 'ws://%s/connect/' % g_CONFIG['wshost']
        self.ws = create_connection(url, header=header)
        self.message_id = 1
        return self.ws

    def get_next_messageid(self):
        if self.message_id > 65530:
            self.message_id = 1
        else:
            self.message_id = self.message_id + 1
        return self.message_id

    def on_packet_send(self, packet):
        """收到PACKET_SEND消息
        """
        log.debug('on_packet_send func')

        if packet.qos == 0:
            return
        elif packet.qos == 1:
            command = Packet.PACKET_ACK
        elif packet.qos == 2:
            command = Packet.PACKET_REC
        rp = Packet(command=command,
                    message_id=packet.message_id,
                    data='')
        try:
            self.ws.send_binary(rp.raw)
        except Exception, e:
            log.warning(e)
            log.warning(traceback.format_exc())

    def on_packet_ack(self, packet):
        """收到PACKET_ACK消息, 结果要通知给http的回调者。这里要怎么搞？
        """
        log.debug('on_packet_ack func')

    def on_packet_rec(self, packet):
        """收到PACKET_REC消息, 要通知给http调用者。
        """
        log.debug('on_packet_rec func')

        rp = Packet(command=Packet.PACKET_REL, message_id=packet.message_id)
        try:
            self.ws.send_binary(rp.raw)
        except Exception, e:
            log.warning(e)
            log.warning(traceback.format_exc())

    def on_packet_rel(self, packet):
        """收到PACKET_REL消息，删除消息ID，返回COM消息。
        """
        log.debug('on_packet_rel func')

        rp = Packet(command=Packet.PACKET_COM, message_id=packet.message_id)
        try:
            self.ws.send_binary(rp.raw)
        except Exception, e:
            log.warning(e)
            log.warning(traceback.format_exc())

    def on_packet_com(self, packet):
        """收到PACKET_COM消息。删除消息ID即可。
        """
        log.debug('on_packet_com func')

    def msg_handler(self, msg):
        packet = Packet(raw=msg)

        cmd = {Packet.PACKET_SEND: self.on_packet_send,
               Packet.PACKET_ACK: self.on_packet_ack,
               Packet.PACKET_REC: self.on_packet_rec,
               Packet.PACKET_REL: self.on_packet_rel,
               Packet.PACKET_COM: self.on_packet_com}.get(packet.command)
        if cmd:
            cmd(packet)
        else:
            log.warning(msg)

    def run(self):
        p = Packet(command=1, msgtype=1, data='Hello, World',
                   qos=1, dup=0, message_id=self.get_next_messageid())
        self.ws.send_binary(p.raw)

        # cnt = random.randint(100, 200)
        while True:
                # cnt = cnt - 1
            msg = self.ws.recv()
            if not msg:
                break
            self.msg_handler(msg)
            ret = random.randint(1, 4)
            if ret <= 1:
                p = Packet(
                    command=1, msgtype=1, data='Hello, World',
                    qos=ret, dup=0, message_id=self.get_next_messageid())
                self.ws.send_binary(p.raw)

        self.ws.close()

    def run2(self):
        p = Packet(command=1, msgtype=1, data='Hello, World',
                   qos=1, dup=0, message_id=self.get_next_messageid())
        self.ws.send_binary(p.raw)

        cnt = random.randint(10, 100)
        while cnt > 0:
            cnt = cnt - 1
            msg = self.ws.recv()
            if not msg:
                break
            self.msg_handler(msg)
            ret = random.randint(1, 4)
            if ret <= 1:
                p = Packet(
                    command=1, msgtype=1, data='Hello, World',
                    qos=ret, dup=0, message_id=self.get_next_messageid())
                self.ws.send_binary(p.raw)

        self.ws.close()


def run_client(channel):
    o = websocket_test()
    try:
        o.create_connection(channel)
        o.run()
    except Exception, e:
        log.warning(e)
        log.warning(traceback.format_exc())


def main():
    if not init_config():
        log.error('init_config fail, close')
        return

    for channel in range(0, g_CONFIG['channelcnt']):
        for i in range(0, g_CONFIG['linkcnt']):
            time.sleep(0.01)
            thread.start_new_thread(run_client, (channel,))

    while True:
        time.sleep(random.randint(2, 10))


if __name__ == '__main__':
    main()
