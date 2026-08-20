"""Import confirmed mouse-left screenshots as compact detector regression samples.

The script crops only the configured right-side ROI and derives one additional
20x20 grayscale template from the hardest supplied positive screenshot.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import cv2

from screen_state_detector import (
    MOUSE_TEMPLATE_FILES,
    REGION_MOUSE,
    gradient_map,
)


ROOT = Path(__file__).resolve().parent


def read_gray(path: Path):
    image = cv2.imread(os.fspath(path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise FileNotFoundError(f"无法读取截图: {path}")
    return image


def write_png(path: Path, image) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(os.fspath(path), image):
        raise OSError(f"无法写入图像: {path}")


def best_match(gray_roi, templates):
    edges = gradient_map(gray_roi)
    best_score = -1.0
    best_location = (0, 0)
    best_shape = templates[0].shape
    for template in templates:
        result = cv2.matchTemplate(edges, template, cv2.TM_CCOEFF_NORMED)
        _, score, _, location = cv2.minMaxLoc(result)
        if score > best_score:
            best_score = float(score)
            best_location = location
            best_shape = template.shape
    return best_score, best_location, best_shape


def import_samples(screenshots: list[Path]) -> None:
    templates = [
        gradient_map(read_gray(ROOT / "assets" / filename))
        for filename in MOUSE_TEMPLATE_FILES
    ]
    x, y, width, height = REGION_MOUSE
    candidates = []
    for index, screenshot in enumerate(screenshots, 1):
        gray = read_gray(screenshot)
        if gray.shape[0] < y + height or gray.shape[1] < x + width:
            raise ValueError(f"截图尺寸不足: {screenshot} ({gray.shape[1]}x{gray.shape[0]})")
        roi = gray[y : y + height, x : x + width]
        score, location, shape = best_match(roi, templates)
        output = ROOT / "detector_test_samples" / f"mouse_pos_character2_{index:02}.png"
        write_png(output, roi)
        candidates.append((score, location, shape, roi, screenshot.name))
        print(f"{screenshot.name}: score={score:.4f}, location=({x + location[0]}, {y + location[1]})")

    score, (match_x, match_y), (template_h, template_w), roi, source = min(candidates)
    template = roi[match_y : match_y + template_h, match_x : match_x + template_w]
    output = ROOT / "assets" / "template_mouse_left_5.png"
    write_png(output, template)
    print(f"新增模板: {output.name}, source={source}, previous_score={score:.4f}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("screenshots", nargs="+", type=Path)
    args = parser.parse_args()
    import_samples(args.screenshots)


if __name__ == "__main__":
    main()
