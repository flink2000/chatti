"""Generate chatti.ico — the two eyes, white on black, like the device shows them.

Written by hand rather than with Pillow so the control panel keeps its four
dependencies. Plain 32-bit BGRA icons (no PNG payload) are what Explorer is
happiest with.

Run once:  .venv\\Scripts\\python.exe make_icon.py
"""

import os
import struct

SIZES = (16, 32, 48, 64, 128, 256)


def rounded(x, y, left, top, w, h, radius):
    """Is (x, y) inside a rounded rectangle?"""
    if not (left <= x < left + w and top <= y < top + h):
        return False
    radius = min(radius, w / 2, h / 2)
    cx = min(max(x, left + radius), left + w - radius)
    cy = min(max(y, top + radius), top + h - radius)
    return (x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2 + 1e-9


def render(size):
    """One image, BGRA, bottom-up as the DIB format wants it.

    Proportions follow the device: a 240 x 284 screen with eyes 62 x 84 at
    x = 74/166, y = 142. Scaled to a square icon the face is cropped to the
    part that carries it — the eyes — so it stays readable at 16 px.
    """
    s = size / 64.0
    pixels = bytearray()
    eye_w, eye_h = 15 * s, 22 * s
    radius = 6 * s
    eye_y = 21 * s
    lefts = (10 * s, 39 * s)
    corner = 12 * s

    for row in range(size - 1, -1, -1):          # bottom-up
        for col in range(size):
            x, y = col + 0.5, row + 0.5
            if not rounded(x, y, 0, 0, size, size, corner):
                pixels += b"\x00\x00\x00\x00"    # transparent outside the tile
                continue
            on_eye = any(rounded(x, y, lx, eye_y, eye_w, eye_h, radius) for lx in lefts)
            if on_eye:
                pixels += b"\xff\xff\xff\xff"    # white eyes
            else:
                pixels += b"\x10\x0c\x0a\xff"    # near-black tile (BGRA)
    return bytes(pixels)


def dib(size, pixels):
    header = struct.pack(
        "<IiiHHIIiiII",
        40, size, size * 2, 1, 32, 0, len(pixels), 0, 0, 0, 0,
    )
    mask_row = ((size + 31) // 32) * 4          # AND mask, padded to 4 bytes
    return header + pixels + b"\x00" * (mask_row * size)


def main():
    images = [dib(s, render(s)) for s in SIZES]
    out = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    for size, data in zip(SIZES, images):
        out += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32,
                           len(data), offset)
        offset += len(data)
    out += b"".join(images)

    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chatti.ico")
    with open(path, "wb") as f:
        f.write(out)
    print("wrote", path, len(out), "bytes")


if __name__ == "__main__":
    main()
