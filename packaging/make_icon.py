"""Build the app icons from the 1024 px logo, with the standard library only.

Run from the project folder:
    python packaging/make_icon.py

It reads packaging/aulari-logo-1024.png (8-bit RGBA, not interlaced), scales it
down with area averaging on premultiplied alpha (no dark fringes on the
transparent edges) and writes:
- src/aulari/assets/aulari.ico, for Windows: its images are stored as PNG,
  which Windows Vista and later understand;
- packaging/aulari.icns, for the macOS app bundle: 16 to 1024 px, also as PNG.
"""

import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "packaging" / "aulari-logo-1024.png"
TARGET = ROOT / "src" / "aulari" / "assets" / "aulari.ico"
SIZES = (16, 24, 32, 48, 64, 128, 256)
FIRST_STEP = 256  # 1024 -> 256 is an exact 4x4 average; smaller sizes start from it
ICNS_TARGET = ROOT / "packaging" / "aulari.icns"
# Sizes the .icns needs on top of the .ico ones; they are scaled from the full logo.
ICNS_LARGE_SIZES = (512, 1024)
# (type, pixel size) of each .icns image; "@2x" types reuse the double size.
ICNS_ENTRIES = (
    (b"icp4", 16),
    (b"icp5", 32),
    (b"icp6", 64),
    (b"ic07", 128),
    (b"ic08", 256),
    (b"ic09", 512),
    (b"ic10", 1024),  # 512@2x
    (b"ic11", 32),  # 16@2x
    (b"ic12", 64),  # 32@2x
    (b"ic13", 256),  # 128@2x
    (b"ic14", 512),  # 256@2x
)
ICNS_HEADER_SIZE = 8  # four-byte type plus four-byte length

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
CHANNELS = 4
COLOR_TYPE_RGBA = 6
MAX_VALUE = 255.0
MAX_BYTE = 255
BIT_DEPTH = 8
# PNG row filters (PNG specification, section 9.2).
FILTER_SUB = 1
FILTER_UP = 2
FILTER_AVERAGE = 3
FILTER_PAETH = 4


def read_chunks(data: bytes) -> tuple[bytes, bytes]:
    """Return the IHDR body and the joined IDAT bodies of a PNG file.

    Raises: ValueError if the data is not a complete PNG file.
    """
    if not data.startswith(PNG_SIGNATURE):
        raise ValueError("Not a PNG file")
    header = b""
    image_data = bytearray()
    position = len(PNG_SIGNATURE)
    finished = False
    while position < len(data) and not finished:
        length, kind = struct.unpack(">I4s", data[position : position + 8])
        body = data[position + 8 : position + 8 + length]
        if kind == b"IHDR":
            header = body
        elif kind == b"IDAT":
            image_data.extend(body)
        elif kind == b"IEND":
            finished = True
        position = position + 12 + length
    if not finished or not header:
        raise ValueError("Incomplete PNG file")
    return header, bytes(image_data)


def paeth(left: int, up: int, up_left: int) -> int:
    """Return the Paeth predictor of the PNG specification."""
    estimate = left + up - up_left
    distance_left = abs(estimate - left)
    distance_up = abs(estimate - up)
    distance_up_left = abs(estimate - up_left)
    result = up_left
    if distance_left <= distance_up and distance_left <= distance_up_left:
        result = left
    elif distance_up <= distance_up_left:
        result = up
    return result


def unfilter_line(kind: int, line: bytearray, previous: bytearray) -> None:
    """Undo the PNG filter of one row, in place.

    Raises: ValueError for an unknown filter type.
    """
    if kind > FILTER_PAETH:
        raise ValueError(f"Unknown PNG filter {kind}")
    for index in range(len(line)):
        left = 0
        up_left = 0
        if index >= CHANNELS:
            left = line[index - CHANNELS]
            up_left = previous[index - CHANNELS]
        up = previous[index]
        predictor = 0
        if kind == FILTER_SUB:
            predictor = left
        elif kind == FILTER_UP:
            predictor = up
        elif kind == FILTER_AVERAGE:
            predictor = (left + up) // 2
        elif kind == FILTER_PAETH:
            predictor = paeth(left, up, up_left)
        line[index] = (line[index] + predictor) & 0xFF


def read_png(path: Path) -> tuple[int, int, bytearray]:
    """Return width, height and the RGBA bytes of an 8-bit RGBA PNG.

    Raises: OSError if the file cannot be read; ValueError if it is not an
    8-bit, non-interlaced RGBA PNG.
    """
    with path.open("rb") as png_file:
        data = png_file.read()
    header, compressed = read_chunks(data)
    width, height, depth, color, _compression, _filter, interlace = struct.unpack(
        ">IIBBBBB", header
    )
    if depth != BIT_DEPTH or color != COLOR_TYPE_RGBA or interlace != 0:
        raise ValueError("The logo must be an 8-bit RGBA PNG without interlacing")
    raw = zlib.decompress(compressed)
    stride = width * CHANNELS
    pixels = bytearray(stride * height)
    previous = bytearray(stride)
    for row in range(height):
        start = row * (stride + 1)
        line = bytearray(raw[start + 1 : start + 1 + stride])
        unfilter_line(raw[start], line, previous)
        pixels[row * stride : (row + 1) * stride] = line
        previous = line
    return width, height, pixels


def premultiplied_rows(width: int, height: int, pixels: bytearray) -> list[list[float]]:
    """Return the image as rows of floats with colour multiplied by alpha."""
    rows = []
    stride = width * CHANNELS
    for row in range(height):
        values = []
        for base in range(row * stride, (row + 1) * stride, CHANNELS):
            alpha = pixels[base + 3] / MAX_VALUE
            values.extend(
                (pixels[base] * alpha, pixels[base + 1] * alpha, pixels[base + 2] * alpha)
            )
            values.append(float(pixels[base + 3]))
        rows.append(values)
    return rows


def area_weights(source: int, target: int) -> list[list[tuple[int, float]]]:
    """For each output index, the source indices it covers and their weights (sum 1)."""
    scale = source / target
    result = []
    for out_index in range(target):
        start = out_index * scale
        end = start + scale
        pairs = []
        index = int(start)
        while index < end and index < source:
            overlap = min(end, index + 1) - max(start, index)
            if overlap > 0:
                pairs.append((index, overlap / scale))
            index = index + 1
        result.append(pairs)
    return result


def shrink_row(row: list[float], weights: list[list[tuple[int, float]]]) -> list[float]:
    """Scale one row horizontally."""
    result = []
    for pairs in weights:
        sums = [0.0, 0.0, 0.0, 0.0]
        for index, weight in pairs:
            base = index * CHANNELS
            for channel in range(CHANNELS):
                sums[channel] = sums[channel] + row[base + channel] * weight
        result.extend(sums)
    return result


def blend_rows(rows: list[list[float]], pairs: list[tuple[int, float]]) -> list[float]:
    """Return the weighted sum of some rows (vertical scaling of one output row)."""
    result = [0.0] * len(rows[0])
    for index, weight in pairs:
        source = rows[index]
        for position in range(len(result)):
            result[position] = result[position] + source[position] * weight
    return result


def shrink(rows: list[list[float]], size: int) -> list[list[float]]:
    """Scale a square premultiplied image down to size x size."""
    source = len(rows)
    weights = area_weights(source, size)
    narrow = [shrink_row(row, weights) for row in rows]
    result = [blend_rows(narrow, pairs) for pairs in weights]
    return result


def to_byte(value: float) -> int:
    """Round and clamp a channel value to 0..255."""
    result = int(round(value))
    if result < 0:
        result = 0
    elif result > MAX_BYTE:
        result = MAX_BYTE
    return result


def rgba_bytes(rows: list[list[float]]) -> bytes:
    """Undo the alpha premultiplication and return the RGBA bytes."""
    result = bytearray()
    for row in rows:
        for base in range(0, len(row), CHANNELS):
            alpha = row[base + 3]
            colour = [0, 0, 0]
            if alpha > 0:
                for channel in range(3):
                    colour[channel] = to_byte(row[base + channel] * MAX_VALUE / alpha)
            result.extend(colour)
            result.append(to_byte(alpha))
    return bytes(result)


def chunk(kind: bytes, body: bytes) -> bytes:
    """Return one PNG chunk with its length and CRC."""
    crc = zlib.crc32(kind + body) & 0xFFFFFFFF
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", crc)


def encode_png(size: int, rgba: bytes) -> bytes:
    """Return a square RGBA image as PNG bytes (filter 0 on every row)."""
    stride = size * CHANNELS
    raw = bytearray()
    for row in range(size):
        raw.append(0)
        raw.extend(rgba[row * stride : (row + 1) * stride])
    header = struct.pack(">IIBBBBB", size, size, BIT_DEPTH, COLOR_TYPE_RGBA, 0, 0, 0)
    result = (
        PNG_SIGNATURE
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )
    return result


def build_ico(images: list[tuple[int, bytes]]) -> bytes:
    """Return an .ico file that stores each (size, PNG bytes) image."""
    directory = struct.pack("<HHH", 0, 1, len(images))
    offset = len(directory) + 16 * len(images)
    entries = bytearray()
    bodies = bytearray()
    for size, png in images:
        side = size % 256  # 0 means 256 in the ICO format
        entries.extend(struct.pack("<BBBBHHII", side, side, 0, 0, 1, 32, len(png), offset))
        bodies.extend(png)
        offset = offset + len(png)
    result = directory + bytes(entries) + bytes(bodies)
    return result


def build_icns(pngs: dict[int, bytes]) -> bytes:
    """Return an .icns file with one PNG image per entry of ICNS_ENTRIES.

    Raises: KeyError if a size of ICNS_ENTRIES is missing from pngs.
    """
    body = bytearray()
    for kind, size in ICNS_ENTRIES:
        png = pngs[size]
        body.extend(kind + struct.pack(">I", ICNS_HEADER_SIZE + len(png)) + png)
    result = b"icns" + struct.pack(">I", ICNS_HEADER_SIZE + len(body)) + bytes(body)
    return result


def main() -> None:
    """Read the logo, build every size and write the .ico and the .icns.

    Raises: OSError or ValueError if the logo cannot be read or is not square.
    """
    width, height, pixels = read_png(SOURCE)
    if width != height or width < max(ICNS_LARGE_SIZES):
        raise ValueError("The logo must be square and at least 1024 px")
    full = premultiplied_rows(width, height, pixels)
    base = shrink(full, FIRST_STEP)
    images = []
    for size in SIZES:
        rows = base if size == FIRST_STEP else shrink(base, size)
        images.append((size, encode_png(size, rgba_bytes(rows))))
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    with TARGET.open("wb") as icon_file:
        icon_file.write(build_ico(images))
    print(f"Wrote {TARGET.relative_to(ROOT)} ({', '.join(str(size) for size in SIZES)} px)")
    pngs = dict(images)
    for size in ICNS_LARGE_SIZES:
        rows = full if size == width else shrink(full, size)
        pngs[size] = encode_png(size, rgba_bytes(rows))
    with ICNS_TARGET.open("wb") as icns_file:
        icns_file.write(build_icns(pngs))
    print(f"Wrote {ICNS_TARGET.relative_to(ROOT)} (16 to 1024 px)")


if __name__ == "__main__":
    main()
