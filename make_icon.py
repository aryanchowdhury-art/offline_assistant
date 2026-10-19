"""Generate app icon: icon.png + offline_assistant.ico."""
from pathlib import Path

from PIL import Image, ImageDraw


OUT_DIR = Path(__file__).resolve().parent
SIZE = 512


def _diagonal_gradient(size: int, c1: tuple, c2: tuple) -> Image.Image:
    img = Image.new("RGBA", (size, size))
    px = img.load()
    denom = 2 * size - 2
    for y in range(size):
        for x in range(size):
            t = (x + y) / denom
            r = int(c1[0] * (1 - t) + c2[0] * t)
            g = int(c1[1] * (1 - t) + c2[1] * t)
            b = int(c1[2] * (1 - t) + c2[2] * t)
            px[x, y] = (r, g, b, 255)
    return img


def build_icon() -> Image.Image:
    # 1. Gradient background, diagonal violet -> cyan
    gradient = _diagonal_gradient(SIZE, (124, 58, 237), (6, 182, 212))

    # 2. Rounded-square mask
    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, SIZE - 1, SIZE - 1), radius=112, fill=255
    )

    icon = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    icon.paste(gradient, (0, 0), mask)

    # 3. Two overlapping semi-transparent white circles (venn)
    layer = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)

    cx, cy = SIZE // 2, SIZE // 2
    r = 122
    offset = 68

    left = (cx - offset - r, cy - r, cx - offset + r, cy + r)
    right = (cx + offset - r, cy - r, cx + offset + r, cy + r)

    ld.ellipse(left, fill=(255, 255, 255, 200))
    ld.ellipse(right, fill=(255, 255, 255, 200))

    icon = Image.alpha_composite(icon, layer)

    # 4. Re-apply rounded mask so circles don't spill outside corners
    final = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    final.paste(icon, (0, 0), mask)
    return final


def main() -> None:
    icon = build_icon()

    png_path = OUT_DIR / "icon.png"
    ico_path = OUT_DIR / "offline_assistant.ico"

    icon.save(png_path)
    icon.save(
        ico_path,
        format="ICO",
        sizes=[
            (16, 16),
            (24, 24),
            (32, 32),
            (48, 48),
            (64, 64),
            (128, 128),
            (256, 256),
        ],
    )
    print(f"Wrote {png_path}")
    print(f"Wrote {ico_path}")


if __name__ == "__main__":
    main()