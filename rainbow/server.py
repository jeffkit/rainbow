# encoding=utf-8
import ConfigParser
from optparse import OptionParser
import signal
import time
import settings
import logging as log
log.basicConfig(level=settings.LOG_LEVEL, format=settings.LOG_FORMAT)
import traceback
import json
import socket
import thread

import tornado.web
import tornado.ioloop
from tornado.httpserver import HTTPServer

from wshandler import WebSocketHandler
from webhandler import SendMessageHandler
from webhandler import SubChannelHandler
from webhandler import UnSubChannelHandler
from webhandler import HelloHandler
from discover import broadcast_online, broadcast_offline
from discover import udp_listen


from config import g_CONFIG


def init_config():
    parser = OptionParser()
    parser.add_option("-f", "--file", dest="config_filename", type="string")
    parser.add_option("-p", "--port", dest="port", type="string")
    options, args = parser.parse_args()
    if not getattr(options, 'config_filename', None):
        log.error('miss -f')
        return False
    if not getattr(options, 'port', None):
        log.error('miss -p')
        return False
    config_filename = options.config_filename
    port = options.port

    try:
        config = ConfigParser.SafeConfigParser()
        config.read(config_filename)
        g_CONFIG['connect_url'] = config.get("main", "connect_url")
        # g_CONFIG['socket_port'] = int(config.get("main", "socket_port"))
        # g_CONFIG['http_port'] = config.get("main", "http_port")
        g_CONFIG['security_token'] = config.get("main", "security_token")
        g_CONFIG['forward_url'] = config.get("main", "forward_url")
        g_CONFIG['close_url'] = config.get("main", "close_url")
        g_CONFIG['udp_ports'] = json.loads(config.get("main", "udp_ports"))
        g_CONFIG['local_ip'] = socket.gethostbyname(socket.gethostname())
    except Exception, e:
        log.error(e)
        log.error(traceback.format_exc())
        return False

    g_CONFIG['socket_port'] = int(port)

    log.info(g_CONFIG)
    return True

if settings.DEBUG:
    MAX_WAIT_SECONDS_BEFORE_SHUTDOWN = 1
else:
    MAX_WAIT_SECONDS_BEFORE_SHUTDOWN = 3

application = tornado.web.Application([
    # for websocket
    (r'/connect/', WebSocketHandler),

    # for HTTP
    (r'/send/', SendMessageHandler),
    (r'/sub/', SubChannelHandler),
    (r'/unsub/', UnSubChannelHandler),
    (r'/hello/', HelloHandler),
])


def shutdown():
    log.info('Stopping http server')

    broadcast_offline()

    # 关闭本进程所有的客户端连接并将用户从在线列表中去掉。
    log.info('make all user offline')
    try:
        WebSocketHandler.shutdown()
    except Exception, e:
        log.error(e, exc_info=True)

    log.info('Will shutdown in %s seconds ...',
             MAX_WAIT_SECONDS_BEFORE_SHUTDOWN)
    io_loop = tornado.ioloop.IOLoop.instance()
    deadline = time.time() + MAX_WAIT_SECONDS_BEFORE_SHUTDOWN

    def stop_loop():
        now = time.time()
        if now < deadline and (io_loop._callbacks or io_loop._timeouts):
            io_loop.add_timeout(now + 1, stop_loop)
        else:
            io_loop.stop()
            log.info('Shutdown')
    stop_loop()


def sig_handler(sig, frame):
    log.warning('Caught signal: %s', sig)
    tornado.ioloop.IOLoop.instance().add_callback(shutdown)


def udp_handler():
    thread.start_new_thread(udp_listen, ())


def udp_send():
    thread.start_new_thread(broadcast_online, ())


def main():
    ret = init_config()
    if not ret:
        log.error('config err, server exit')
        return

    default_port = g_CONFIG.get('socket_port') or 1984
    # todo http 和 websocket 不是共用一个port咩
    standalone = True

    server = HTTPServer(application)
    server.listen(default_port)
    signal.signal(signal.SIGTERM, sig_handler)
    signal.signal(signal.SIGINT, sig_handler)
    if not standalone:
        # TODO: 到redis注册一下。需要心跳。
        pass

    udp_handler()
    udp_send()
    tornado.ioloop.IOLoop.instance().start()

    if not standalone:
        # TODO: 到redis注销一下。
        pass

    log.info('server exit')

if __name__ == '__main__':
    main()
