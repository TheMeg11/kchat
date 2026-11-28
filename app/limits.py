"""Bounded local rate limits. Production uses one application process."""
from collections import OrderedDict, deque
import time


class WindowLimiter:
    def __init__(self, max_keys=10000):
        self.entries = OrderedDict()
        self.max_keys = max_keys

    def _events(self, key, seconds):
        now = time.monotonic()
        # Expire unused identities as well as the current identity.
        while self.entries:
            _, first = next(iter(self.entries.items()))
            if first and first[-1] > now - seconds:
                break
            self.entries.popitem(last=False)
        events = self.entries.pop(key, deque(maxlen=120))
        while events and events[0] <= now - seconds:
            events.popleft()
        self.entries[key] = events
        while len(self.entries) > self.max_keys:
            self.entries.popitem(last=False)
        return events

    def available(self, key, limit, seconds):
        return len(self._events(key, seconds)) < limit

    def hit(self, key, seconds):
        self._events(key, seconds).append(time.monotonic())

    def allow(self, key, limit, seconds):
        if not self.available(key, limit, seconds):
            return False
        self.hit(key, seconds)
        return True


login_limiter = WindowLimiter()
message_limiter = WindowLimiter()


def client_ip(request):
    from .config import settings
    peer = request.client.host if request.client else "unknown"
    # Never use untrusted X-Forwarded-For for rate limiting/audit attribution.
    if peer in settings.trusted_proxy_ips.split(","):
        import ipaddress
        try:
            return str(ipaddress.ip_address(request.headers.get("x-forwarded-for", "").split(",")[-1].strip()))
        except ValueError:
            pass
    return peer
