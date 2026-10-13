from functools import lru_cache

from redis import Redis

from legal_rag.config import settings


@lru_cache
def redis_client():
    return Redis.from_url(
        settings().redis_url.get_secret_value(),
        decode_responses=True,
        socket_timeout=5,
        socket_connect_timeout=5,
    )


RESERVE_QUERY = """
if redis.call('EXISTS', KEYS[1]) == 1 then return -1 end
local used = tonumber(redis.call('GET', KEYS[2]) or '0')
if used >= tonumber(ARGV[1]) then return -2 end
redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3])
redis.call('INCR', KEYS[2]); redis.call('EXPIRE', KEYS[2], 172800)
return used + 1
"""


RELEASE_LOCK = """
if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end
return 0
"""
