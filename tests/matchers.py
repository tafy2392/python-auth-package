from datetime import datetime, timedelta
from operator import methodcaller

from testtools.matchers import AfterPreprocessing as After  # type: ignore
from testtools.matchers import (  # type: ignore
    ContainsDict,
    Equals,
    GreaterThan,
    LessThan,
    MatchesAll,
    MatchesAny,
    MatchesDict,
    MatchesStructure,
)


def is_response(code, content_type=None, **fields):
    if content_type is not None:
        assert "headers" not in fields
        headers = {"content-type": Equals(content_type)}
        fields["headers"] = ContainsDict(headers)
    return MatchesStructure(status_code=Equals(code), **fields)


def is_response_with_body(body, content_type=None, code=200, method="read"):
    return MatchesAll(
        is_response(code, content_type),
        After(methodcaller(method), Equals(body)),
    )


def _parse_marathon_event_timestamp(timestamp):
    """
    Parse Marathon's ISO8601-like timestamps into a datetime.
    """
    return datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%S.%fZ")


def matches_time_or_just_before(time, tolerance=timedelta(seconds=10)):
    """
    Match a time to be equal to a certain time or just before it. Useful when
    checking for a time that is now +/- some amount of time.
    """
    return MatchesAll(
        GreaterThan(time - tolerance), MatchesAny(LessThan(time), Equals(time))
    )


def is_marathon_event(event_type, **fields):
    """
    Match a dict (deserialized from JSON) as a Marathon event. Matches the
    event type and checks for a recent timestamp.

    :param event_type: The event type ('eventType' field value)
    :param kwargs: Any other matchers to apply to the dict
    """
    matching_dict = {
        "eventType": Equals(event_type),
        "timestamp": After(
            _parse_marathon_event_timestamp,
            matches_time_or_just_before(datetime.utcnow()),
        ),
    }
    matching_dict.update(fields)
    return MatchesDict(matching_dict)
