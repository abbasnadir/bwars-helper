"""Color parsing and naming for color roles.

Naming is a port of colord's names plugin (https://github.com/omgovich/colord, src/plugins/names.ts):
the same CSS color dictionary in the same order, an exact hex lookup first, then the closest name by
squared RGB distance, so results match `colord(color).toName({ closest: true })`.
"""

import re

# Same keys and order as colord's NAME_HEX_STORE. Order matters: it decides which alias wins
# (e.g. "cyan" over "aqua") and which name wins a distance tie.
NAME_HEX_STORE = {
    "white": "#ffffff",
    "bisque": "#ffe4c4",
    "blue": "#0000ff",
    "cadetblue": "#5f9ea0",
    "chartreuse": "#7fff00",
    "chocolate": "#d2691e",
    "coral": "#ff7f50",
    "antiquewhite": "#faebd7",
    "aqua": "#00ffff",
    "azure": "#f0ffff",
    "whitesmoke": "#f5f5f5",
    "papayawhip": "#ffefd5",
    "plum": "#dda0dd",
    "blanchedalmond": "#ffebcd",
    "black": "#000000",
    "gold": "#ffd700",
    "goldenrod": "#daa520",
    "gainsboro": "#dcdcdc",
    "cornsilk": "#fff8dc",
    "cornflowerblue": "#6495ed",
    "burlywood": "#deb887",
    "aquamarine": "#7fffd4",
    "beige": "#f5f5dc",
    "crimson": "#dc143c",
    "cyan": "#00ffff",
    "darkblue": "#00008b",
    "darkcyan": "#008b8b",
    "darkgoldenrod": "#b8860b",
    "darkkhaki": "#bdb76b",
    "darkgray": "#a9a9a9",
    "darkgreen": "#006400",
    "darkgrey": "#a9a9a9",
    "peachpuff": "#ffdab9",
    "darkmagenta": "#8b008b",
    "darkred": "#8b0000",
    "darkorchid": "#9932cc",
    "darkorange": "#ff8c00",
    "darkslateblue": "#483d8b",
    "gray": "#808080",
    "darkslategray": "#2f4f4f",
    "darkslategrey": "#2f4f4f",
    "deeppink": "#ff1493",
    "deepskyblue": "#00bfff",
    "wheat": "#f5deb3",
    "firebrick": "#b22222",
    "floralwhite": "#fffaf0",
    "ghostwhite": "#f8f8ff",
    "darkviolet": "#9400d3",
    "magenta": "#ff00ff",
    "green": "#008000",
    "dodgerblue": "#1e90ff",
    "grey": "#808080",
    "honeydew": "#f0fff0",
    "hotpink": "#ff69b4",
    "blueviolet": "#8a2be2",
    "forestgreen": "#228b22",
    "lawngreen": "#7cfc00",
    "indianred": "#cd5c5c",
    "indigo": "#4b0082",
    "fuchsia": "#ff00ff",
    "brown": "#a52a2a",
    "maroon": "#800000",
    "mediumblue": "#0000cd",
    "lightcoral": "#f08080",
    "darkturquoise": "#00ced1",
    "lightcyan": "#e0ffff",
    "ivory": "#fffff0",
    "lightyellow": "#ffffe0",
    "lightsalmon": "#ffa07a",
    "lightseagreen": "#20b2aa",
    "linen": "#faf0e6",
    "mediumaquamarine": "#66cdaa",
    "lemonchiffon": "#fffacd",
    "lime": "#00ff00",
    "khaki": "#f0e68c",
    "mediumseagreen": "#3cb371",
    "limegreen": "#32cd32",
    "mediumspringgreen": "#00fa9a",
    "lightskyblue": "#87cefa",
    "lightblue": "#add8e6",
    "midnightblue": "#191970",
    "lightpink": "#ffb6c1",
    "mistyrose": "#ffe4e1",
    "moccasin": "#ffe4b5",
    "mintcream": "#f5fffa",
    "lightslategray": "#778899",
    "lightslategrey": "#778899",
    "navajowhite": "#ffdead",
    "navy": "#000080",
    "mediumvioletred": "#c71585",
    "powderblue": "#b0e0e6",
    "palegoldenrod": "#eee8aa",
    "oldlace": "#fdf5e6",
    "paleturquoise": "#afeeee",
    "mediumturquoise": "#48d1cc",
    "mediumorchid": "#ba55d3",
    "rebeccapurple": "#663399",
    "lightsteelblue": "#b0c4de",
    "mediumslateblue": "#7b68ee",
    "thistle": "#d8bfd8",
    "tan": "#d2b48c",
    "orchid": "#da70d6",
    "mediumpurple": "#9370db",
    "purple": "#800080",
    "pink": "#ffc0cb",
    "skyblue": "#87ceeb",
    "springgreen": "#00ff7f",
    "palegreen": "#98fb98",
    "red": "#ff0000",
    "yellow": "#ffff00",
    "slateblue": "#6a5acd",
    "lavenderblush": "#fff0f5",
    "peru": "#cd853f",
    "palevioletred": "#db7093",
    "violet": "#ee82ee",
    "teal": "#008080",
    "slategray": "#708090",
    "slategrey": "#708090",
    "aliceblue": "#f0f8ff",
    "darkseagreen": "#8fbc8f",
    "darkolivegreen": "#556b2f",
    "greenyellow": "#adff2f",
    "seagreen": "#2e8b57",
    "seashell": "#fff5ee",
    "tomato": "#ff6347",
    "silver": "#c0c0c0",
    "sienna": "#a0522d",
    "lavender": "#e6e6fa",
    "lightgreen": "#90ee90",
    "orange": "#ffa500",
    "orangered": "#ff4500",
    "steelblue": "#4682b4",
    "royalblue": "#4169e1",
    "turquoise": "#40e0d0",
    "yellowgreen": "#9acd32",
    "salmon": "#fa8072",
    "saddlebrown": "#8b4513",
    "sandybrown": "#f4a460",
    "rosybrown": "#bc8f8f",
    "darksalmon": "#e9967a",
    "lightgoldenrodyellow": "#fafad2",
    "snow": "#fffafa",
    "lightgrey": "#d3d3d3",
    "lightgray": "#d3d3d3",
    "dimgray": "#696969",
    "dimgrey": "#696969",
    "olivedrab": "#6b8e23",
    "olive": "#808000",
}

# Later aliases overwrite earlier ones, exactly like colord's HEX_NAME_STORE.
HEX_NAME_STORE = {hex_value: name for name, hex_value in NAME_HEX_STORE.items()}


def _hex_to_rgb(hex_value: str) -> tuple[int, int, int]:
    return int(hex_value[1:3], 16), int(hex_value[3:5], 16), int(hex_value[5:7], 16)


NAME_RGB_STORE = {name: _hex_to_rgb(hex_value) for name, hex_value in NAME_HEX_STORE.items()}

_HEX_RE = re.compile(r"#?([0-9a-f]{3}|[0-9a-f]{6})")
_RGB_RE = re.compile(r"(?:rgb\(\s*)?(\d{1,3})\s*[,\s]\s*(\d{1,3})\s*[,\s]\s*(\d{1,3})\s*\)?")


def normalise_name(text: str) -> str:
    """'Light Sea-Green' -> 'lightseagreen'."""
    return re.sub(r"[\s_-]+", "", text.strip().lower())


def parse_color(text: str) -> tuple[int, int, int] | None:
    """Reads a CSS color name, a hex code (#f80, #ff8800, ff8800) or RGB values (255, 136, 0 / rgb(255 136 0))."""
    text = text.strip().lower()
    if len(text) > 32:
        return None

    name = normalise_name(text)
    if name in NAME_RGB_STORE:
        return NAME_RGB_STORE[name]

    match = _HEX_RE.fullmatch(text)
    if match:
        digits = match.group(1)
        if len(digits) == 3:
            digits = "".join(c * 2 for c in digits)
        return _hex_to_rgb(f"#{digits}")

    match = _RGB_RE.fullmatch(text)
    if match:
        rgb = tuple(int(v) for v in match.groups())
        if all(0 <= v <= 255 for v in rgb):
            return rgb

    return None


def to_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def color_name(rgb: tuple[int, int, int]) -> str:
    """Same result as colord's `toName({ closest: true })`."""
    exact = HEX_NAME_STORE.get(to_hex(rgb))
    if exact:
        return exact

    closest, min_distance = "black", float("inf")
    for name, (r, g, b) in NAME_RGB_STORE.items():
        distance = (rgb[0] - r) ** 2 + (rgb[1] - g) ** 2 + (rgb[2] - b) ** 2
        if distance < min_distance:
            closest, min_distance = name, distance
    return closest


def is_exact_name(rgb: tuple[int, int, int]) -> bool:
    return to_hex(rgb) in HEX_NAME_STORE


def canonical_name(name: str) -> str:
    """One name per color. colord's closest match can return 'aqua' while the exact match returns 'cyan'
    for the very same #00ffff, so map every alias to the name used for exact matches."""
    return HEX_NAME_STORE[NAME_HEX_STORE[name]]


def role_name_for(rgb: tuple[int, int, int]) -> str:
    """A plain color name only when the color is exactly that name; otherwise the nearest name plus the hex.

    '#00ffff' -> 'cyan', but '#10f0f0' -> 'cyan-10f0f0', so a role called 'cyan' is always really cyan.
    """
    name = canonical_name(color_name(rgb))
    if is_exact_name(rgb):
        return name
    return f"{name}-{to_hex(rgb)[1:]}"
