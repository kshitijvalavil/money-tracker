"""Gunicorn settings, picked up automatically from the working directory.

One worker keeps the in-memory rate limits exact. Threads let a slow Claude call
(the assistant, a scanned statement) wait without blocking every other request,
and the timeout covers the longest of those calls.
"""
workers = 1
threads = 8
timeout = 120
