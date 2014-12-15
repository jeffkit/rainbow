#encoding=utf-8
import tornado.web
import tornado.ioloop
from tornado.httpserver import HTTPServer

from wshandler import WebSocketHandler
from webhandler import SendMessageHandler

import signal
import time
import logging as log

MAX_WAIT_SECONDS_BEFORE_SHUTDOWN = 3

application = tornado.web.Application([
    # for websocket
    (r'/connect/', WebSocketHandler),

    # for HTTP
    (r'/send/', SendMessageHandler),
    ])


def shutdown():
    log.info('Stopping http server')
    server.stop()

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
    # self.subscriber.close()

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

if __name__ == '__main__':

    default_port = 1984
    standalone = True

    # load configs

    server = HTTPServer(application)
    server.listen(default_port)
    signal.signal(signal.SIGTERM, sig_handler)
    signal.signal(signal.SIGINT, sig_handler)
    if not standalone:
        # TODO: 到redis注册一下。需要心跳。
        pass
    tornado.ioloop.IOLoop.instance().start()

    if not standalone:
        # TODO: 到redis注销一下。
        pass
    
    log.info('server exit')
