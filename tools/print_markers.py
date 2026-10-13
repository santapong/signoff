"""Generate printable vector reference/tool markers without accessing a camera."""
from __future__ import annotations

import argparse
from pathlib import Path

import cv2


def marker_svg(marker_id: int) -> str:
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    pixels = cv2.aruco.generateImageMarker(dictionary, marker_id, 60)
    cells = pixels[5::10, 5::10]
    boxes = [f'<rect x="{10+x*10}" y="{10+y*10}" width="10" height="10"/>'
             for y in range(6) for x in range(6) if cells[y, x] == 0]
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="64mm" height="64mm" '
            'viewBox="0 0 80 80"><rect width="80" height="80" fill="white"/>'
            '<g fill="black">'+''.join(boxes)+'</g></svg>')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("data/markers"))
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    for marker_id, name in ((0, "station-reference"), (1, "tool")):
        dest = args.out / f"{name}-id{marker_id}.svg"
        dest.write_text(marker_svg(marker_id))
        print(dest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
