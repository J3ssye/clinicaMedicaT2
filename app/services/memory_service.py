MAX_HISTORY = 20  # antes devia ser 5 ou 10

import redis
import json

r = redis.Redis(host="redis", port=6379, decode_responses=True)

def save_message(user_id: str, role: str, content: str):
    key = f"history:{user_id}"
    
    history = r.lrange(key, 0, -1)
    history.append(json.dumps({"role": role, "content": content}))

    history = history[-20:]  # mantém últimos 20

    r.delete(key)
    for item in history:
        r.rpush(key, item)


def get_history(user_id: str):
    key = f"history:{user_id}"
    history = r.lrange(key, 0, -1)
    return [json.loads(h) for h in history]