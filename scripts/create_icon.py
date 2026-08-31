"""Создание иконки для CorpusBuilder."""
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import os

def create_icon(size=256):
    s = size / 256.0
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))

    # Градиентный фон со скруглёнными углами
    for y in range(size):
        ratio = y / size
        r = int(10 + (26 - 10) * ratio)
        g = int(22 + (43 - 22) * ratio)
        b = int(40 + (64 - 40) * ratio)
        for x in range(size):
            radius = int(40 * s)
            if _is_corner(x, y, size, radius):
                continue
            img.putpixel((x, y), (r, g, b, 255))

    draw = ImageDraw.Draw(img)

    # Документ
    doc_x = int(55 * s)
    doc_y = int(35 * s)
    doc_w = int(146 * s)
    doc_h = int(170 * s)
    doc_radius = int(12 * s)

    # Тень
    shadow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle([doc_x+int(3*s), doc_y+int(5*s), doc_x+doc_w+int(3*s), doc_y+doc_h+int(5*s)], radius=doc_radius, fill=(0,0,0,80))
    shadow = shadow.filter(ImageFilter.GaussianBlur(int(4*s)))
    img = Image.alpha_composite(img, shadow)
    draw = ImageDraw.Draw(img)

    # Белый документ
    draw.rounded_rectangle([doc_x, doc_y, doc_x+doc_w, doc_y+doc_h], radius=doc_radius, fill=(244,248,252,255))

    # Загнутый уголок
    cs = int(25*s)
    draw.polygon([(doc_x+doc_w-cs, doc_y), (doc_x+doc_w, doc_y), (doc_x+doc_w, doc_y+cs)], fill=(200,215,230,255))

    # Текстовые строки
    colors = [(91,141,184),(91,141,184),(130,150,175),(91,141,184),(130,150,175),(91,141,184),(130,150,175),(91,141,184),(130,150,175)]
    lx = doc_x + int(18*s)
    ly = doc_y + int(20*s)
    lw = doc_w - int(36*s)
    lh = int(7*s)
    lg = int(12*s)
    for i, c in enumerate(colors):
        y = ly + i * lg
        if y + lh > doc_y + doc_h - int(15*s):
            break
        w = lw if i % 3 != 2 else int(lw * 0.7)
        draw.rounded_rectangle([lx, y, lx+w, y+lh], radius=int(2*s), fill=c+(255,))

    # Микросхема
    cx = doc_x + int(25*s)
    cy = doc_y + doc_h - int(50*s)
    cw = doc_w - int(50*s)
    ch = int(25*s)
    draw.rounded_rectangle([cx, cy, cx+cw, cy+ch], radius=int(4*s), fill=(10,22,40,255))

    # Пины
    pn = 6
    ph = int(3*s)
    pg = (ch - pn*ph) // (pn+1)
    for i in range(pn):
        y = cy + pg + i*(ph+pg)
        draw.rectangle([cx-int(5*s), y, cx, y+ph], fill=(180,180,180,255))
        draw.rectangle([cx+cw, y, cx+cw+int(5*s), y+ph], fill=(180,180,180,255))

    # Точки на чипе
    dy = cy + ch//2
    for dx in range(-2,3):
        x = cx + cw//2 + dx*int(5*s)
        draw.ellipse([x-int(1*s), dy-int(1*s), x+int(1*s), dy+int(1*s)], fill=(91,141,184,255))

    # Бейдж с буквой C
    bx = doc_x + doc_w - int(20*s)
    by = doc_y - int(10*s)
    br = int(28*s)

    shadow2 = Image.new("RGBA", (size, size), (0,0,0,0))
    sd2 = ImageDraw.Draw(shadow2)
    sd2.ellipse([bx-br+int(2*s), by-br+int(3*s), bx+br+int(2*s), by+br+int(3*s)], fill=(0,0,0,100))
    shadow2 = shadow2.filter(ImageFilter.GaussianBlur(int(3*s)))
    img = Image.alpha_composite(img, shadow2)
    draw = ImageDraw.Draw(img)

    draw.ellipse([bx-br, by-br, bx+br, by+br], fill=(0,122,204,255))

    # Буква C
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", int(br*1.2))
    except:
        try:
            font = ImageFont.truetype("arial.ttf", int(br*1.2))
        except:
            font = ImageFont.load_default()

    text = "C"
    bbox = draw.textbbox((0,0), text, font=font)
    tw = bbox[2]-bbox[0]
    th = bbox[3]-bbox[1]
    draw.text((bx-tw//2-bbox[0], by-th//2-bbox[1]), text, font=font, fill=(255,255,255,255))

    return img

def _is_corner(x, y, size, radius):
    corners = [(radius,radius),(size-radius-1,radius),(radius,size-radius-1),(size-radius-1,size-radius-1)]
    for cx, cy in corners:
        dx = x - cx
        dy = y - cy
        if dx*dx + dy*dy > radius*radius:
            if (x < radius or x >= size-radius) and (y < radius or y >= size-radius):
                return True
    return False

def main():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.makedirs(os.path.join(base, "assets"), exist_ok=True)

    icon_256 = create_icon(256)

    # PNG
    png_path = os.path.join(base, "assets", "icon.png")
    icon_256.save(png_path, "PNG")
    print(f"PNG: {png_path}")

    # ICO (multi-size)
    sizes = [16, 32, 48, 64, 128, 256]
    icons = [create_icon(s) for s in sizes]

    ico_path = os.path.join(base, "assets", "icon.ico")
    icons[-1].save(ico_path, format="ICO", sizes=[(s,s) for s in sizes])
    print(f"ICO: {ico_path}")

    # Корень для PyInstaller
    ico_root = os.path.join(base, "icon.ico")
    icons[-1].save(ico_root, format="ICO", sizes=[(s,s) for s in sizes])
    print(f"ICO root: {ico_root}")

    # Preview
    prev = os.path.join(base, "assets", "icon_preview.png")
    icon_256.save(prev, "PNG")
    print(f"Preview: {prev}")

if __name__ == "__main__":
    main()
