# encoding=utf-8

import socket
import logging as log
import time
import traceback

from tornado.httpclient import HTTPClient
from tornado.httpclient import HTTPRequest

from config import g_CONFIG
from config import g_Online_Server
from config import g_Online_Server_List

g_msg_prefix = 'rainbow http port '


def udp_listen():
    address = ('', g_CONFIG['socket_port'])
    log.debug(address)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.bind(address)
    while True:
        try:
            data, addr = s.recvfrom(1024)
            if not data.startswith(g_msg_prefix):
                continue
            http_port = int(data[len(g_msg_prefix):])
            if addr[0] == g_CONFIG['local_ip'] and\
                    http_port == g_CONFIG['socket_port']:
                pass
            else:
                remote_http = 'http://%s:%d' % (addr[0], http_port)
                if not g_Online_Server.get(remote_http):
                    url = '%s/hello/' % remote_http
                    req = HTTPRequest(
                        url=url, method='GET',
                        connect_timeout=2, request_timeout=2)
                    http_client = HTTPClient()
                    rsp = http_client.fetch(req)
                    if rsp.code == 200:
                        g_Online_Server[remote_http] = time.time()
                        g_Online_Server_List.append(remote_http)
                else:
                    g_Online_Server[remote_http] = time.time()
        except Exception, e:
            log.error(e)
            log.error(traceback.format_exc())


def send_broadcast():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    msg = '%s%d' % (g_msg_prefix, g_CONFIG['socket_port'])
    while True:
        try:
            for udp_port in g_CONFIG['udp_ports']:
                s.sendto(msg, ('<broadcast>', udp_port))
            time.sleep(10)
        except Exception, e:
            log.error(e)
            log.error(traceback.format_exc())
