
import time
import random
from hashlib import sha1
import ConfigParser
from optparse import OptionParser
import logging as log

import socket
import json
import traceback

from config import g_CONFIG
import settings
log.basicConfig(level=settings.LOG_LEVEL, format=settings.LOG_FORMAT)


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


def param_signature():
    ts = int(time.time())
    nonce = random.randint(1000, 99999)
    sign_ele = [g_CONFIG['security_token'], str(ts), str(nonce)]
    sign_ele.sort()
    sign = sha1(''.join(sign_ele)).hexdigest()
    params = {'timestamp': ts, 'nonce': nonce, 'signature': sign}
    return params
