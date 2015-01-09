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

from websocket import create_connection

from wshandler import Packet
import settings
log.basicConfig(level=settings.LOG_LEVEL, format=settings.LOG_FORMAT)


g_ws = []


def on_packet_send(packet):
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
        g_ws[0].send_binary(rp.raw)
    except Exception, e:
        log.warning(e)
        log.warning(traceback.format_exc())


def on_packet_ack(packet):
    """收到PACKET_ACK消息, 结果要通知给http的回调者。这里要怎么搞？
    """
    log.debug('on_packet_ack func')


def on_packet_rec(packet):
    """收到PACKET_REC消息, 要通知给http调用者。
    """
    log.debug('on_packet_rec func')

    rp = Packet(command=Packet.PACKET_REL, message_id=packet.message_id)
    try:
        g_ws[0].send_binary(rp.raw)
    except Exception, e:
        log.warning(e)
        log.warning(traceback.format_exc())


def on_packet_rel(packet):
    """收到PACKET_REL消息，删除消息ID，返回COM消息。
    """
    log.debug('on_packet_rel func')

    rp = Packet(command=Packet.PACKET_COM, message_id=packet.message_id)
    try:
        g_ws[0].send_binary(rp.raw)
    except Exception, e:
        log.warning(e)
        log.warning(traceback.format_exc())


def on_packet_com(packet):
    """收到PACKET_COM消息。删除消息ID即可。
    """
    log.debug('on_packet_com func')


def msg_handler(msg):
    packet = Packet(raw=msg)
    log.info(packet.data)

    {Packet.PACKET_SEND: on_packet_send,
     Packet.PACKET_ACK: on_packet_ack,
     Packet.PACKET_REC: on_packet_rec,
     Packet.PACKET_REL: on_packet_rel,
     Packet.PACKET_COM: on_packet_com}[packet.command](packet)


class websocket_test(object):

    def create_connection(self):
        header = ["X-CLIENT-OS: ios7", ]
        c = random.randint(1, 20)
        header.append('User-Agent: aaa%d' % c)
        header.append('X-DEVICEID: ajokmksdm%f' % random.uniform(1, 10000000))
        self.ws = create_connection(
            "ws://192.168.0.111:1984/connect/", header=header)
        self.message_id = 1

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
        log.info(packet.data)

        {Packet.PACKET_SEND: self.on_packet_send,
         Packet.PACKET_ACK: self.on_packet_ack,
         Packet.PACKET_REC: self.on_packet_rec,
         Packet.PACKET_REL: self.on_packet_rel,
         Packet.PACKET_COM: self.on_packet_com}[packet.command](packet)

    def run(self):
        p = Packet(command=1, msgtype=1, data='Hello, World',
                   qos=1, dup=0, message_id=self.get_next_messageid())
        self.ws.send_binary(p.raw)

        cnt = random.randint(100, 200)
        while cnt > 0:
            cnt = cnt - 1
            msg = self.ws.recv()
            self.msg_handler(msg)
            ret = random.randint(1, 4)
            if ret <= 1:
                p = Packet(
                    command=1, msgtype=1, data='Hello, World',
                    qos=ret, dup=0, message_id=self.get_next_messageid())
                self.ws.send_binary(p.raw)

        self.ws.close()


def run_client():
    o = websocket_test()
    o.create_connection()
    o.run()


def main():
    for i in range(1, 10):
        thread.start_new_thread(run_client, ())

    while True:
        for i in range(1, 10):
            thread.start_new_thread(run_client, ())
        time.sleep(random.randint(2, 10))


if __name__ == '__main__':
    main()
