import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Optional


def write_tvshow_nfo(
    series_dir: Path,
    title: str,
    overview: Optional[str] = None,
    instructor: Optional[str] = None,
    tags: Optional[List[str]] = None,
) -> Path:
    """Generate and write a tvshow.nfo file for Jellyfin/Kodi inside series_dir."""
    root = ET.Element("tvshow")

    title_elem = ET.SubElement(root, "title")
    title_elem.text = title

    plot_elem = ET.SubElement(root, "plot")
    plot_elem.text = overview or f"Lectures for {title}."

    if instructor:
        director = ET.SubElement(root, "director")
        director.text = instructor
        studio = ET.SubElement(root, "studio")
        studio.text = instructor

    genre = ET.SubElement(root, "genre")
    genre.text = "Education"

    if tags:
        for t in tags:
            tag_elem = ET.SubElement(root, "tag")
            tag_elem.text = t

    tree = ET.ElementTree(root)
    nfo_path = series_dir / "tvshow.nfo"
    ET.indent(tree, space="  ", level=0)
    tree.write(nfo_path, encoding="utf-8", xml_declaration=True)
    return nfo_path


def write_episode_nfo(
    nfo_path: Path,
    title: str,
    episode_number: int,
    season_number: int = 1,
    plot: Optional[str] = None,
    tags: Optional[List[str]] = None,
) -> Path:
    """Generate and write an {episode}.nfo file alongside a video for Jellyfin."""
    root = ET.Element("episodedetails")

    title_elem = ET.SubElement(root, "title")
    title_elem.text = title

    season_elem = ET.SubElement(root, "season")
    season_elem.text = str(season_number)

    ep_elem = ET.SubElement(root, "episode")
    ep_elem.text = str(episode_number)

    if plot:
        plot_elem = ET.SubElement(root, "plot")
        plot_elem.text = plot

    if tags:
        for t in tags:
            tag_elem = ET.SubElement(root, "tag")
            tag_elem.text = t

    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ", level=0)
    tree.write(nfo_path, encoding="utf-8", xml_declaration=True)
    return nfo_path
