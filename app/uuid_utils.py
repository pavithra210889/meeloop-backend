"""UUID v7 generation utility (RFC 9562).

UUID v7 encodes a Unix timestamp in milliseconds in the most-significant 48 bits,
followed by random data. This makes them:
  - Time-sortable (good for DB index performance)
  - Globally unique (safe across distributed systems)
  - Unguessable (random component prevents enumeration)

No external dependency required.
"""

import os
import time
import uuid


def uuid7() -> uuid.UUID:
    """Generate a UUID v7 per RFC 9562."""
    timestamp_ms = int(time.time() * 1000)
    rand_bytes = os.urandom(10)

    # 48-bit unix timestamp in ms
    uuid_bytes = timestamp_ms.to_bytes(6, "big")

    # 4-bit version (0b0111 = 7) + 12-bit rand_a
    rand_a = int.from_bytes(rand_bytes[:2], "big") & 0x0FFF
    uuid_bytes += (0x7000 | rand_a).to_bytes(2, "big")

    # 2-bit variant (0b10) + 62-bit rand_b
    rand_b = int.from_bytes(rand_bytes[2:], "big") & 0x3FFFFFFFFFFFFFFF
    uuid_bytes += (0x8000000000000000 | rand_b).to_bytes(8, "big")

    return uuid.UUID(bytes=uuid_bytes)


def generate_uuid() -> str:
    """Generate a UUID v7 as a string. Use this as default_factory for model IDs."""
    return str(uuid7())
