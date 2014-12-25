

DEBUG = False

PROJECT_NAME = 'MusicTalk'

REDIS_HOST = 'localhost'
REDIS_PORT = 6379
REDIS_DATABASE = 0
REDIS_PASSWORD = ''


try:
    from xsettings import *
except:
    pass

LOG_FORMAT = '[%(levelname)s] %(asctime)s %(funcName)s(%(filename)s:%(lineno)s) %(message)s'

if DEBUG:
    LOG_LEVEL = 'DEBUG'
else:
    LOG_LEVEL = 'INFO'
