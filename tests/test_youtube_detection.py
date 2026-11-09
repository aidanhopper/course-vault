from vault.importers.youtube import is_playlist_url, is_youtube_url


def test_youtube_url_detection():
    assert is_youtube_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert is_youtube_url("https://youtu.be/dQw4w9WgXcQ")
    assert is_youtube_url("https://www.youtube.com/playlist?list=PLB2BE3D6041052A62")
    assert not is_youtube_url("https://vimeo.com/123456")
    assert not is_youtube_url("https://example.com/lecture.mp4")


def test_playlist_url_detection():
    assert is_playlist_url("https://www.youtube.com/playlist?list=PLB2BE3D6041052A62")
    assert is_playlist_url("https://www.youtube.com/watch?v=abc&list=PLB2BE3D6041052A62")
    assert not is_playlist_url("https://www.youtube.com/watch?v=abc")
    assert not is_playlist_url("https://youtu.be/abc")
    assert not is_playlist_url("https://www.youtube.com/watch?v=abc&list=RDabc")  # Mix radio
