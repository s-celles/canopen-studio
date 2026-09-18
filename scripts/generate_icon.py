"""
Generate high-resolution application icon (.png and .ico) for CAN & CANopen Studio.
"""

import os
from PIL import Image, ImageDraw, ImageFont


def generate():
    os.makedirs("assets", exist_ok=True)
    size = (256, 256)
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Background rounded rectangle
    bg_color = (18, 26, 38)
    draw.rounded_rectangle([8, 8, 248, 248], radius=48, fill=bg_color, outline=(40, 70, 110), width=4)

    # Grid lines (subtle oscilloscope background)
    grid_color = (25, 45, 70)
    for y in range(40, 220, 30):
        draw.line([(24, y), (232, y)], fill=grid_color, width=1)
    for x in range(40, 220, 30):
        draw.line([(x, 40), (x, 216)], fill=grid_color, width=1)

    # CAN Differential Waveform (CAN_H in cyan, CAN_L in orange)
    # CAN_H wave
    h_points = [
        (24, 110),
        (60, 110),
        (70, 70),
        (120, 70),
        (130, 110),
        (160, 110),
        (170, 70),
        (210, 70),
        (220, 110),
        (232, 110),
    ]
    draw.line(h_points, fill=(0, 210, 255), width=5, joint="curve")

    # CAN_L wave
    l_points = [
        (24, 130),
        (60, 130),
        (70, 170),
        (120, 170),
        (130, 130),
        (160, 130),
        (170, 170),
        (210, 170),
        (220, 130),
        (232, 130),
    ]
    draw.line(l_points, fill=(255, 170, 0), width=5, joint="curve")

    # Network Nodes (CAN nodes circles)
    nodes = [(40, 120), (95, 70), (95, 170), (185, 70), (185, 170)]
    for nx, ny in nodes:
        draw.ellipse([nx - 7, ny - 7, nx + 7, ny + 7], fill=(240, 245, 255), outline=(0, 180, 240), width=2)

    # Central Badge / Text
    try:
        font = ImageFont.truetype("arialbd.ttf", 36)
        sub_font = ImageFont.truetype("arial.ttf", 15)
    except Exception:
        font = ImageFont.load_default()
        sub_font = ImageFont.load_default()

    # Text overlay in bottom area
    draw.rounded_rectangle([30, 184, 226, 236], radius=12, fill=(10, 16, 25, 230), outline=(0, 190, 255), width=2)
    draw.text((128, 202), "CANopen", fill=(255, 255, 255), font=font, anchor="mm")
    draw.text((128, 223), "STUDIO", fill=(0, 210, 255), font=sub_font, anchor="mm")

    png_path = os.path.join("assets", "icon.png")
    ico_path = os.path.join("assets", "icon.ico")

    img.save(png_path, format="PNG")

    # Multi-resolution .ico
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    img.save(ico_path, format="ICO", sizes=sizes)
    print(f"Generated {png_path} and {ico_path} successfully!")


if __name__ == "__main__":
    generate()
