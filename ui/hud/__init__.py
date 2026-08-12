"""The ODIN instrument HUD — native PyQt6, built to the spec in ODIN-HUD.md.

Nothing in this package touches a browser, a web view, or a socket. Producer
and consumer share one process; Qt signals/slots are the entire transport.
"""
