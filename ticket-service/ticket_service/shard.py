import uuid
import zlib


def shard_id(account_id: uuid.UUID, count: int) -> int:
    if count <= 0:
        return 0
    return zlib.crc32(account_id.bytes) % count
