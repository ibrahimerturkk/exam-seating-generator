import threading
from collections.abc import Iterator

import pytest

from configure import EditorHandler, EditorServer


@pytest.fixture
def editor_server() -> Iterator[EditorServer]:
    with EditorServer(("127.0.0.1", 0), EditorHandler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server
        finally:
            server.shutdown()
            thread.join(timeout=5)
