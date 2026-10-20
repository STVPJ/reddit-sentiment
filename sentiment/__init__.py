"""Reddit product sentiment: collect posts and comments, judge each product mention
with a typed classifier, and serve the results to a dashboard.

Deterministic pipeline: code fixes the whole sequence, and the model only answers fixed
typed questions about one comment at a time. There is no agent loop.
"""
