"""Gunicorn settings, picked up automatically from the working directory.

Threads let one slow Claude call (the assistant, a scanned statement) wait without
blocking every other request; the timeout covers the longest of those calls.
"""
workers = 2
threads = 4
timeout = 120
