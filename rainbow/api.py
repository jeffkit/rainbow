
import time
import random
from hashlib import sha1

from config import g_CONFIG


def param_signature():
    ts = int(time.time())
    nonce = random.randint(1000, 99999)
    sign_ele = [g_CONFIG['security_token'], str(ts), str(nonce)]
    sign_ele.sort()
    sign = sha1(''.join(sign_ele)).hexdigest()
    params = {'timestamp': ts, 'nonce': nonce, 'signature': sign}
    return params
