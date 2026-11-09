import xml.etree.ElementTree as ET
from pathlib import Path
from vault.jellyfin.nfo import write_episode_nfo, write_tvshow_nfo


def test_write_tvshow_nfo(tmp_path: Path):
    nfo_path = write_tvshow_nfo(
        series_dir=tmp_path,
        title="Introduction to Algorithms",
        overview="Comprehensive algorithms course.",
        instructors=["Prof. Demaine"],
        tags=["Algorithms", "Computer Science"],
    )
    assert nfo_path.is_file()

    tree = ET.parse(nfo_path)
    root = tree.getroot()
    assert root.tag == "tvshow"
    assert root.find("title").text == "Introduction to Algorithms"
    assert root.find("director").text == "Prof. Demaine"
    tags = [t.text for t in root.findall("tag")]
    assert "Algorithms" in tags


def test_write_episode_nfo(tmp_path: Path):
    ep_nfo = tmp_path / "ep01.nfo"
    write_episode_nfo(
        nfo_path=ep_nfo,
        title="01. Peak Finding",
        episode_number=1,
        plot="Finding local peaks in 1D and 2D arrays.",
        instructors=["Prof. Demaine"],
        tags=["Divide and Conquer"],
    )
    assert ep_nfo.is_file()

    tree = ET.parse(ep_nfo)
    root = tree.getroot()
    assert root.tag == "episodedetails"
    assert root.find("title").text == "01. Peak Finding"
    assert root.find("episode").text == "1"
    assert root.find("tag").text == "Divide and Conquer"
