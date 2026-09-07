"""
Watches the audit trail while the server runs.

The trail records everything, but a recording nobody reads is not much use.
This runs the analyzers periodically and logs whatever they find, so a
broken conversation appears in the server window on its own rather than
waiting for someone to think to look.

Each trace is reported once. Without that the same finding would be
repeated at every pass and the warning would stop meaning anything.
"""
import logging
from collections import OrderedDict

from app.config import settings
from app.core.audit import group_traces, read_events
from app.services.audit_analysis import ERROR, INFO, WARNING, analyze_trace

logger = logging.getLogger(__name__)

# Remembering every trace forever would grow without bound; a few thousand
# is far more than one poll can produce.
MAX_REMEMBERED = 5000
_reported: OrderedDict = OrderedDict()

LOG_LEVEL = {ERROR: logging.ERROR, WARNING: logging.WARNING, INFO: logging.INFO}


def already_reported(trace_id: str) -> bool:
    if trace_id in _reported:
        return True
    _reported[trace_id] = True
    while len(_reported) > MAX_REMEMBERED:
        _reported.popitem(last=False)
    return False


def reset():
    """Forget what has been reported. For tests."""
    _reported.clear()


def check_recent(path: str = None) -> dict:
    """
    Analyze traces that have not been reported yet and log what turns up.

    Returns counts by severity, so a caller can act on the numbers without
    parsing log output.
    """
    counts = {ERROR: 0, WARNING: 0, INFO: 0}

    try:
        events = read_events(path or settings.AUDIT_LOG_PATH)
    except Exception as e:
        logger.warning(f"Could not read the audit trail: {e}")
        return counts

    if not events:
        return counts

    for trace_id, trace_events in group_traces(events).items():
        if already_reported(trace_id):
            continue

        for finding in analyze_trace(trace_events):
            # Below the threshold these are for reading in a report, not for
            # interrupting someone watching the server.
            if finding.severity == INFO and not settings.AUDIT_WATCH_INFO:
                continue
            counts[finding.severity] = counts.get(finding.severity, 0) + 1
            logger.log(
                LOG_LEVEL.get(finding.severity, logging.INFO),
                f"AUDIT {finding.code} [{trace_id}]: {finding.message}"
                + (f" {finding.detail}" if finding.detail else ""),
            )

    if counts[ERROR]:
        logger.error(
            f"AUDIT: {counts[ERROR]} error(s) in recent conversations. "
            f"Inspect with: python scripts/analyze_audit.py --show <trace_id>"
        )

    return counts


def prime():
    """
    Mark everything currently in the log as seen.

    Called at startup so a restart does not replay findings from traces that
    were already dealt with days ago.
    """
    try:
        events = read_events(settings.AUDIT_LOG_PATH)
    except Exception:
        return 0
    for trace_id in group_traces(events):
        already_reported(trace_id)
    return len(_reported)
