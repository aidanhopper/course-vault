from vault.schemas.transcript import parse_vtt_content, parse_vtt_timestamp


def test_parse_vtt_timestamp():
    assert parse_vtt_timestamp("00:01:23.456") == 83.456
    assert parse_vtt_timestamp("02:15.500") == 135.500
    assert parse_vtt_timestamp("10.000") == 10.0


def test_parse_vtt_content():
    sample_vtt = """WEBVTT

1
00:00:01.000 --> 00:00:04.500
<c>Welcome</c> to lecture one.

2
00:00:05.200 --> 00:00:09.800
Today we will cover <i>binary search trees</i>.
"""
    cues = parse_vtt_content(sample_vtt)
    assert len(cues) == 2
    assert cues[0].start_seconds == 1.0
    assert cues[0].end_seconds == 4.5
    assert cues[0].text == "Welcome to lecture one."
    assert cues[1].start_seconds == 5.2
    assert cues[1].end_seconds == 9.8
    assert cues[1].text == "Today we will cover binary search trees."
