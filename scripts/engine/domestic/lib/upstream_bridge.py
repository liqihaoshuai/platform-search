"""Compatibility stub: overseas sources are owned by the platform-search engine.

The domestic adapter never discovers external installations. The top-level
runner dispatches overseas sources through the bundled global adapter.
"""

def status():
    return {"available": False, "reason": "managed by top-level platform-search dispatcher"}


def search_upstream(*args, **kwargs):
    raise RuntimeError("overseas sources must be dispatched by platform-search")
