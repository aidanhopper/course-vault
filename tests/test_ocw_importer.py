from vault.importers.ocw import is_ocw_url, normalize_ocw_course_url


def test_is_ocw_url():
    assert is_ocw_url("https://ocw.mit.edu/courses/6-7960-deep-learning-fall-2024/")
    assert is_ocw_url("https://ocw.mit.edu/courses/6-7960-deep-learning-fall-2024/lists/lecture-notes/")
    assert is_ocw_url("http://ocw.mit.edu/courses/18-06-linear-algebra-spring-2010/")
    assert not is_ocw_url("https://youtube.com/watch?v=123")
    assert not is_ocw_url("https://example.com/course.pdf")


def test_normalize_ocw_course_url():
    u1 = "https://ocw.mit.edu/courses/6-7960-deep-learning-fall-2024/lists/lecture-notes/"
    assert normalize_ocw_course_url(u1) == "https://ocw.mit.edu/courses/6-7960-deep-learning-fall-2024/"

    u2 = "https://ocw.mit.edu/courses/6-7960-deep-learning-fall-2024"
    assert normalize_ocw_course_url(u2) == "https://ocw.mit.edu/courses/6-7960-deep-learning-fall-2024/"
