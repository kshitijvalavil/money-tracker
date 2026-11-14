"""Gunicorn settings, picked up automatically from the working directory.

One worker keeps the in-memory rate limits exact. Threads let a slow Claude call
(the assistant, a scanned statement) wait without blocking every other request,
and `timeout` covers the longest of those calls. `graceful_timeout` is how long
a restart waits for in-flight requests before closing them, kept below the
platform's own kill window so a deploy turns the app over promptly.
"""
workers = 1
threads = 8
timeout = 120
graceful_timeout = 10
