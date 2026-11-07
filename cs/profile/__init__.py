"""A13 – the whole profile: weekly hours that drifted, and replies to closed-door reviews.

    from cs.profile import drift, review_reply
    drift.drift_suggestion("SE-D0016"); drift.compose_drift_message(...); drift.apply_drift(...)
    review_reply.review_reply_drafts("SE-D0016"); review_reply.compose_review_reply_offer(...)
    review_reply.approve_review_reply(...)

CLI: ``python -m cs.profile drift SE-D0016 --lang sv`` / ``python -m cs.profile reviews FI-D0001 --lang fi``.
"""
