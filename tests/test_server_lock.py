import os

import pytest

from serve import acquire_instance_lock


def test_server_lock_rejects_duplicate_instance():
    port = 59123
    first = acquire_instance_lock(port)
    try:
        if os.name == "nt":
            with pytest.raises(RuntimeError, match="已有本系统服务实例"):
                acquire_instance_lock(port)
    finally:
        first.close()
