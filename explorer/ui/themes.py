"""
themes - the colour themes, as plain data.

Each theme is written as a short "spec": the dozen key colours of the
original theme (most are modelled on popular VS Code themes). ``build()``
turns a spec into the full palette the app uses, deriving everything else
(hover, row striping, borders, inputs, terminal colours...) with the same
rules for every theme. That keeps all themes equally polished: a new theme
only needs its key colours, and a fix to a rule improves every theme at once.

Spec keys
---------
Required:
    name     shown in menus
    bg       content background (VS Code: editor)  - views, the current tab
    chrome   surrounding UI (VS Code: side bar / tab strip) - toolbar, sidebar, tabs
    fg       main text            dim     secondary text (headers, inactive tabs)
    accent   focus lines, current-tab marker, checked buttons, progress
    select   selected rows
    folder   folder icon colour
    red green yellow blue magenta cyan   the theme's colour set (terminal, errors...)
Optional (derived when missing):
    border select_fg accent_fg hover terminal_bg error ok

Plain Python - no Qt - so tests can check contrast without a display.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

# ---------------------------------------------------------------------------
# Colour helpers (sRGB hex strings)
# ---------------------------------------------------------------------------


def _rgb(color: str) -> Tuple[int, int, int]:
    c = color.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def _hex(r: float, g: float, b: float) -> str:
    return "#{:02x}{:02x}{:02x}".format(*(max(0, min(255, round(v))) for v in (r, g, b)))


def mix(a: str, b: str, t: float) -> str:
    """Blend colour ``a`` towards ``b`` by ``t`` (0 = a, 1 = b)."""
    ra, ga, ba = _rgb(a)
    rb, gb, bb = _rgb(b)
    return _hex(ra + (rb - ra) * t, ga + (gb - ga) * t, ba + (bb - ba) * t)


def luminance(color: str) -> float:
    """WCAG relative luminance (0 = black, 1 = white)."""
    def channel(v: int) -> float:
        v = v / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = _rgb(color)
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a: str, b: str) -> float:
    """WCAG contrast ratio, 1 (none) .. 21 (black on white). Text wants >= 4.5."""
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def is_dark(color: str) -> bool:
    return luminance(color) < 0.2


def best_text_on(background: str, *candidates: str) -> str:
    """The candidate (default: black / white) with the most contrast on ``background``."""
    options = candidates or ("#ffffff", "#000000")
    return max(options, key=lambda c: contrast(c, background))


def ensure_contrast(color: str, against: str, minimum: float, toward: str = "") -> str:
    """Nudge ``color`` (in small steps) until it reaches ``minimum`` contrast
    with ``against``. By default it moves away from ``against``: darker on a
    light colour, lighter on a dark one. Stops as soon as the target is met, so
    a theme keeps its character and only fixes what is too faint."""
    if contrast(color, against) >= minimum:
        return color
    if not toward:
        toward = "#000000" if luminance(color) <= luminance(against) else "#ffffff"
    for step in range(1, 21):
        candidate = mix(color, toward, step / 20)
        if contrast(candidate, against) >= minimum:
            return candidate
    return mix(color, toward, 1.0)


# ---------------------------------------------------------------------------
# The themes
# ---------------------------------------------------------------------------
_VSCODE_ANSI_DARK = dict(red="#f14c4c", green="#23d18b", yellow="#e5e510", blue="#3b8eea",
                         magenta="#d670d6", cyan="#29b8db")

SPECS: Dict[str, dict] = {
    # -- PyExplorer's own -----------------------------------------------------
    "dark": dict(name="PyExplorer Dark", bg="#1d2026", chrome="#24282f", fg="#dde2ea", dim="#8c95a6",
                 accent="#5b9cf6", select="#2b4a7a", folder="#e3b04b", red="#f47067", green="#8bc34a",
                 yellow="#e5c07b", blue="#61afef", magenta="#c678dd", cyan="#56b6c2"),
    "light": dict(name="PyExplorer Light", bg="#ffffff", chrome="#f3f4f7", fg="#1f2329", dim="#646b78",
                  accent="#2f6fdd", select="#cfe0fb", select_fg="#0d1b33", folder="#e0a930",
                  red="#c62828", green="#2e7d32", yellow="#a66a00", blue="#1f6fd1", magenta="#8e44ad",
                  cyan="#138a96"),

    # -- VS Code built-in --------------------------------------------------------
    "dark_modern": dict(name="Dark Modern", bg="#1f1f1f", chrome="#181818", fg="#cccccc", dim="#9d9d9d",
                        accent="#0078d4", select="#04395e", select_fg="#ffffff", border="#2b2b2b",
                        folder="#dcb67a", error="#f85149", ok="#2ea043", **_VSCODE_ANSI_DARK),
    "dark_plus": dict(name="Dark+ (classic)", bg="#1e1e1e", chrome="#252526", fg="#d4d4d4", dim="#969696",
                      accent="#007acc", select="#04395e", select_fg="#ffffff", border="#3c3c3c",
                      folder="#dcb67a", error="#f48771", ok="#89d185", **_VSCODE_ANSI_DARK),
    "light_modern": dict(name="Light Modern", bg="#ffffff", chrome="#f8f8f8", fg="#3b3b3b", dim="#6e6e6e",
                         accent="#005fb8", select="#cfe3f8", select_fg="#1f1f1f", border="#e5e5e5",
                         folder="#c9a24d", red="#cd3131", green="#107c10", yellow="#949800", blue="#0451a5",
                         magenta="#bc05bc", cyan="#0598bc"),
    "quiet_light": dict(name="Quiet Light", bg="#f5f5f5", chrome="#ececec", fg="#333333", dim="#6f6f6f",
                        accent="#705697", select="#c4d9b1", select_fg="#333333", folder="#c9a044",
                        red="#aa3731", green="#448c27", yellow="#9c6a00", blue="#4b69c6", magenta="#7a3e9d",
                        cyan="#2f7d7d"),

    # -- Popular dark themes ---------------------------------------------------
    "one_dark_pro": dict(name="One Dark Pro", bg="#282c34", chrome="#21252b", fg="#abb2bf", dim="#7f848e",
                         accent="#61afef", select="#3a4150", select_fg="#e6e9ef", border="#181a1f",
                         folder="#e5c07b", red="#e06c75", green="#98c379", yellow="#e5c07b", blue="#61afef",
                         magenta="#c678dd", cyan="#56b6c2"),
    "dracula": dict(name="Dracula", bg="#282a36", chrome="#21222c", fg="#f8f8f2", dim="#7b88b8",
                    accent="#bd93f9", select="#44475a", border="#191a21", folder="#ffb86c", red="#ff5555",
                    green="#50fa7b", yellow="#f1fa8c", blue="#bd93f9", magenta="#ff79c6", cyan="#8be9fd"),
    "monokai": dict(name="Monokai", bg="#272822", chrome="#1e1f1c", fg="#f8f8f2", dim="#a59f85",
                    accent="#66d9ef", select="#575649", border="#15160f", folder="#e6db74", red="#f92672",
                    green="#a6e22e", yellow="#e6db74", blue="#66d9ef", magenta="#ae81ff", cyan="#a1efe4"),
    "monokai_pro": dict(name="Monokai Pro", bg="#2d2a2e", chrome="#221f22", fg="#fcfcfa", dim="#939293",
                        accent="#ffd866", select="#5b595c", border="#19181a", folder="#ffd866", red="#ff6188",
                        green="#a9dc76", yellow="#ffd866", blue="#78dce8", magenta="#ab9df2", cyan="#78dce8"),
    "github_dark": dict(name="GitHub Dark", bg="#0d1117", chrome="#010409", fg="#e6edf3", dim="#848d97",
                        accent="#2f81f7", select="#1f3a5f", border="#30363d", folder="#d29922",
                        red="#ff7b72", green="#3fb950", yellow="#d29922", blue="#58a6ff", magenta="#bc8cff",
                        cyan="#39c5cf"),
    "github_dark_dimmed": dict(name="GitHub Dark Dimmed", bg="#22272e", chrome="#1c2128", fg="#adbac7",
                               dim="#8b97a4", accent="#539bf5", select="#2d4566", border="#444c56",
                               folder="#c69026", red="#f47067", green="#57ab5a", yellow="#c69026",
                               blue="#539bf5", magenta="#b083f0", cyan="#39c5cf"),
    "tokyo_night": dict(name="Tokyo Night", bg="#1a1b26", chrome="#16161e", fg="#a9b1d6", dim="#7a80a6",
                        accent="#7aa2f7", select="#283457", select_fg="#c0caf5", border="#101014",
                        folder="#e0af68", red="#f7768e", green="#9ece6a", yellow="#e0af68", blue="#7aa2f7",
                        magenta="#bb9af7", cyan="#7dcfff"),
    "tokyo_night_storm": dict(name="Tokyo Night Storm", bg="#24283b", chrome="#1f2335", fg="#c0caf5",
                              dim="#8089b3", accent="#7aa2f7", select="#2e3c64", border="#1b1e2e",
                              folder="#e0af68", red="#f7768e", green="#9ece6a", yellow="#e0af68",
                              blue="#7aa2f7", magenta="#bb9af7", cyan="#7dcfff"),
    "catppuccin_mocha": dict(name="Catppuccin Mocha", bg="#1e1e2e", chrome="#181825", fg="#cdd6f4",
                             dim="#9399b2", accent="#cba6f7", select="#45475a", border="#313244",
                             folder="#f9e2af", red="#f38ba8", green="#a6e3a1", yellow="#f9e2af",
                             blue="#89b4fa", magenta="#f5c2e7", cyan="#94e2d5"),
    "catppuccin_macchiato": dict(name="Catppuccin Macchiato", bg="#24273a", chrome="#1e2030", fg="#cad3f5",
                                 dim="#939ab7", accent="#c6a0f6", select="#494d64", border="#363a4f",
                                 folder="#eed49f", red="#ed8796", green="#a6da95", yellow="#eed49f",
                                 blue="#8aadf4", magenta="#f5bde6", cyan="#8bd5ca"),
    "catppuccin_frappe": dict(name="Catppuccin Frappé", bg="#303446", chrome="#292c3c", fg="#c6d0f5",
                              dim="#a1a8c6", accent="#ca9ee6", select="#51576d", border="#414559",
                              folder="#e5c890", red="#e78284", green="#a6d189", yellow="#e5c890",
                              blue="#8caaee", magenta="#f4b8e4", cyan="#81c8be"),
    "night_owl": dict(name="Night Owl", bg="#011627", chrome="#01111d", fg="#d6deeb", dim="#89a4bb",
                      accent="#82aaff", select="#234d70", border="#122d42", folder="#ecc48d", red="#ef5350",
                      green="#22da6e", yellow="#addb67", blue="#82aaff", magenta="#c792ea", cyan="#21c7a8"),
    "gruvbox_dark": dict(name="Gruvbox Dark", bg="#282828", chrome="#1d2021", fg="#ebdbb2", dim="#a89984",
                         accent="#fe8019", select="#504945", border="#3c3836", folder="#fabd2f",
                         red="#fb4934", green="#b8bb26", yellow="#fabd2f", blue="#83a598", magenta="#d3869b",
                         cyan="#8ec07c"),
    "ayu_dark": dict(name="Ayu Dark", bg="#0d1017", chrome="#0b0e14", fg="#bfbdb6", dim="#7a808a",
                     accent="#e6b450", select="#1c3048", border="#1b1f29", folder="#e6b450", red="#f07178",
                     green="#aad94c", yellow="#e6b450", blue="#59c2ff", magenta="#d2a6ff", cyan="#95e6cb"),
    "ayu_mirage": dict(name="Ayu Mirage", bg="#1f2430", chrome="#1c212b", fg="#cccac2", dim="#8a919f",
                       accent="#ffcc66", select="#2a3c56", border="#171b24", folder="#ffcc66", red="#f28779",
                       green="#d5ff80", yellow="#ffcc66", blue="#73d0ff", magenta="#dfbfff", cyan="#95e6cb"),
    "material_ocean": dict(name="Material Ocean", bg="#0f111a", chrome="#090b10", fg="#a6accd",
                           dim="#717cb4", accent="#80cbc4", select="#292e45", border="#1a1c25",
                           folder="#ffcb6b", red="#f07178", green="#c3e88d", yellow="#ffcb6b",
                           blue="#82aaff", magenta="#c792ea", cyan="#89ddff"),
    "material_palenight": dict(name="Material Palenight", bg="#292d3e", chrome="#232637", fg="#a6accd",
                               dim="#8a91b4", accent="#82aaff", select="#414863", border="#1f2233",
                               folder="#ffcb6b", red="#f07178", green="#c3e88d", yellow="#ffcb6b",
                               blue="#82aaff", magenta="#c792ea", cyan="#89ddff"),
    "cobalt2": dict(name="Cobalt2", bg="#193549", chrome="#15232d", fg="#e1efff", dim="#9fb6c9",
                    accent="#ffc600", select="#0050a4", select_fg="#ffffff", border="#0d3a58",
                    folder="#ffc600", red="#ff628c", green="#3ad900", yellow="#ffc600", blue="#1e9bff",
                    magenta="#fb94ff", cyan="#80fcff"),
    "shades_of_purple": dict(name="Shades of Purple", bg="#2d2b55", chrome="#222244", fg="#ffffff",
                             dim="#a599e9", accent="#fad000", select="#5333c8", select_fg="#ffffff",
                             border="#1e1e3f", folder="#fad000", red="#ec3a37", green="#3ad900",
                             yellow="#fad000", blue="#7b6cff", magenta="#ff628c", cyan="#80fcff"),
    "synthwave_84": dict(name="SynthWave '84", bg="#262335", chrome="#1f1b2e", fg="#f0eff1", dim="#9ea3cc",
                         accent="#ff7edb", select="#463465", border="#171520", folder="#fede5d",
                         red="#fe4450", green="#72f1b8", yellow="#fede5d", blue="#03edf9", magenta="#ff7edb",
                         cyan="#36f9f6"),
    "rose_pine": dict(name="Rosé Pine", bg="#191724", chrome="#1f1d2e", fg="#e0def4", dim="#908caa",
                      accent="#c4a7e7", select="#403d52", border="#26233a", folder="#f6c177", red="#eb6f92",
                      green="#3e8fb0", yellow="#f6c177", blue="#9ccfd8", magenta="#c4a7e7", cyan="#ebbcba"),
    "rose_pine_moon": dict(name="Rosé Pine Moon", bg="#232136", chrome="#2a273f", fg="#e0def4",
                           dim="#908caa", accent="#c4a7e7", select="#44415a", border="#393552",
                           folder="#f6c177", red="#eb6f92", green="#3e8fb0", yellow="#f6c177",
                           blue="#9ccfd8", magenta="#c4a7e7", cyan="#ea9a97"),
    "everforest_dark": dict(name="Everforest Dark", bg="#2d353b", chrome="#232a2e", fg="#d3c6aa",
                            dim="#9da9a0", accent="#a7c080", select="#475258", border="#3d484d",
                            folder="#dbbc7f", red="#e67e80", green="#a7c080", yellow="#dbbc7f",
                            blue="#7fbbb3", magenta="#d699b6", cyan="#83c092"),
    "kanagawa": dict(name="Kanagawa", bg="#1f1f28", chrome="#16161d", fg="#dcd7ba", dim="#938aa9",
                     accent="#7e9cd8", select="#2d4f67", border="#2a2a37", folder="#e6c384", error="#e46876",
                     red="#c34043", green="#76946a", yellow="#c0a36e", blue="#7e9cd8", magenta="#957fb8",
                     cyan="#6a9589"),
    "horizon": dict(name="Horizon", bg="#1c1e26", chrome="#232530", fg="#cbced0", dim="#8c8fa8",
                    accent="#e95378", select="#3a3c4e", border="#16161c", folder="#fab795", red="#e95678",
                    green="#29d398", yellow="#fab795", blue="#26bbd9", magenta="#ee64ac", cyan="#59e1e3"),
    "andromeda": dict(name="Andromeda", bg="#23262e", chrome="#20232b", fg="#d5ced9", dim="#a0a1a7",
                      accent="#00e8c6", select="#3d4352", border="#1b1d23", folder="#ffe66d", red="#ee5d43",
                      green="#96e072", yellow="#ffe66d", blue="#7cb7ff", magenta="#c74ded", cyan="#00e8c6"),
    "poimandres": dict(name="Poimandres", bg="#1b1e28", chrome="#171922", fg="#a6accd", dim="#767c9d",
                       accent="#5de4c7", select="#303340", border="#12141b", folder="#fffac2",
                       red="#d0679d", green="#5de4c7", yellow="#fffac2", blue="#89ddff", magenta="#fcc5e9",
                       cyan="#add7ff"),
    "moonlight": dict(name="Moonlight", bg="#222436", chrome="#1e2030", fg="#c8d3f5", dim="#828bb8",
                      accent="#82aaff", select="#383e5c", border="#191a2a", folder="#ffc777", red="#ff757f",
                      green="#c3e88d", yellow="#ffc777", blue="#82aaff", magenta="#c099ff", cyan="#86e1fc"),
    "panda": dict(name="Panda", bg="#292a2b", chrome="#242526", fg="#e6e6e6", dim="#999999",
                  accent="#ff75b5", select="#45474b", border="#1e1f20", folder="#ffb86c", red="#ff2c6d",
                  green="#19f9d8", yellow="#ffb86c", blue="#45a9f9", magenta="#ff75b5", cyan="#6fc1ff"),
    "oceanic_next": dict(name="Oceanic Next", bg="#1b2b34", chrome="#17242b", fg="#d8dee9", dim="#93a1ad",
                         accent="#6699cc", select="#4f5b66", border="#13202a", folder="#fac863",
                         red="#ec5f67", green="#99c794", yellow="#fac863", blue="#6699cc", magenta="#c594c5",
                         cyan="#5fb3b3"),
    "vitesse_dark": dict(name="Vitesse Dark", bg="#121212", chrome="#181818", fg="#dbd7ca", dim="#8f8c85",
                         accent="#4d9375", select="#2b3d35", border="#252525", folder="#e6cc77",
                         red="#cb7676", green="#4d9375", yellow="#e6cc77", blue="#6394bf", magenta="#d9739f",
                         cyan="#5eaab5"),
    "nord": dict(name="Nord", bg="#2e3440", chrome="#3b4252", fg="#eceff4", dim="#a3adbf", accent="#88c0d0",
                 select="#4c6a8a", border="#4c566a", folder="#ebcb8b", red="#bf616a", green="#a3be8c",
                 yellow="#ebcb8b", blue="#81a1c1", magenta="#b48ead", cyan="#88c0d0"),
    "solarized": dict(name="Solarized Dark", bg="#002b36", chrome="#073642", fg="#e3dcc8", dim="#93a1a1",
                      accent="#268bd2", select="#1f5f7a", select_fg="#fdf6e3", border="#1c5361",
                      folder="#b58900", red="#dc322f", green="#859900", yellow="#b58900", blue="#268bd2",
                      magenta="#d33682", cyan="#2aa198"),

    # -- Popular light themes ----------------------------------------------------
    "github_light": dict(name="GitHub Light", bg="#ffffff", chrome="#f6f8fa", fg="#1f2328", dim="#656d76",
                         accent="#0969da", select="#ddf4ff", select_fg="#1f2328", border="#d0d7de",
                         folder="#d4a72c", red="#cf222e", green="#1a7f37", yellow="#9a6700", blue="#0969da",
                         magenta="#8250df", cyan="#1b7c83"),
    "solarized_light": dict(name="Solarized Light", bg="#fdf6e3", chrome="#eee8d5", fg="#586e75",
                            dim="#7a8b8c", accent="#268bd2", select="#dfca88", select_fg="#073642",
                            border="#ddd6c1", folder="#b58900", red="#dc322f", green="#859900",
                            yellow="#b58900", blue="#268bd2", magenta="#d33682", cyan="#2aa198"),
    "catppuccin_latte": dict(name="Catppuccin Latte", bg="#eff1f5", chrome="#e6e9ef", fg="#4c4f69",
                             dim="#6c6f85", accent="#8839ef", select="#ccd0da", select_fg="#4c4f69",
                             border="#ccd0da", folder="#df8e1d", red="#d20f39", green="#40a02b",
                             yellow="#df8e1d", blue="#1e66f5", magenta="#ea76cb", cyan="#179299"),
    "one_light": dict(name="One Light", bg="#fafafa", chrome="#eaeaeb", fg="#383a42", dim="#696c77",
                      accent="#526fff", select="#d8defa", select_fg="#232324", border="#dbdbdc",
                      folder="#c18401", red="#e45649", green="#50a14f", yellow="#c18401", blue="#4078f2",
                      magenta="#a626a4", cyan="#0184bc"),
    "ayu_light": dict(name="Ayu Light", bg="#fcfcfc", chrome="#f3f4f5", fg="#5c6166", dim="#7d848c",
                      accent="#fa8d3e", select="#dae6f7", select_fg="#3b4046", border="#e1e3e6",
                      folder="#f2ae49", red="#f07171", green="#86b300", yellow="#eba400", blue="#399ee6",
                      magenta="#a37acc", cyan="#4cbf99"),
    "gruvbox_light": dict(name="Gruvbox Light", bg="#fbf1c7", chrome="#f2e5bc", fg="#3c3836", dim="#7c6f64",
                          accent="#af3a03", select="#e6d5a8", select_fg="#282828", border="#d5c4a1",
                          folder="#b57614", red="#9d0006", green="#79740e", yellow="#b57614", blue="#076678",
                          magenta="#8f3f71", cyan="#427b58"),
    "rose_pine_dawn": dict(name="Rosé Pine Dawn", bg="#faf4ed", chrome="#f2e9e1", fg="#575279",
                           dim="#797593", accent="#907aa9", select="#dfdad9", select_fg="#464261",
                           border="#e4dcd4", folder="#ea9d34", red="#b4637a", green="#286983",
                           yellow="#ea9d34", blue="#56949f", magenta="#907aa9", cyan="#d7827e"),
    "everforest_light": dict(name="Everforest Light", bg="#fdf6e3", chrome="#f4f0d9", fg="#5c6a72",
                             dim="#7a8478", accent="#8da101", select="#e6e2cc", select_fg="#4a565d",
                             border="#e0dcc7", folder="#dfa000", red="#f85552", green="#8da101",
                             yellow="#dfa000", blue="#3a94c5", magenta="#df69ba", cyan="#35a77c"),
    "tokyo_night_light": dict(name="Tokyo Night Light", bg="#d5d6db", chrome="#cbccd1", fg="#343b58",
                              dim="#5a5e70", accent="#34548a", select="#b4b8c8", select_fg="#343b58",
                              border="#b9bac0", folder="#8f5e15", red="#8c4351", green="#485e30",
                              yellow="#8f5e15", blue="#34548a", magenta="#5a4a78", cyan="#0f4b6e"),
    "light_owl": dict(name="Night Owl Light", bg="#fbfbfb", chrome="#f0f0f0", fg="#403f53", dim="#6d7783",
                      accent="#2aa298", select="#d3e8f8", select_fg="#403f53", border="#e0e0e0",
                      folder="#daaa01", red="#de3d3b", green="#08916a", yellow="#c96f00", blue="#288ed7",
                      magenta="#d6438a", cyan="#2aa298"),
    "vitesse_light": dict(name="Vitesse Light", bg="#ffffff", chrome="#f7f7f7", fg="#393a34", dim="#6f6d66",
                          accent="#1c6b48", select="#d6eadf", select_fg="#22231f", border="#eeeeee",
                          folder="#bda437", red="#ab5959", green="#1e754f", yellow="#a08a2a", blue="#296aa3",
                          magenta="#a13865", cyan="#2993a3"),
}


def build(spec: dict) -> Dict[str, str]:
    """A theme spec -> the full palette (every key the app reads)."""
    bg, chrome = spec["bg"], spec["chrome"]
    dark = is_dark(bg)
    away = "#ffffff" if dark else "#000000"            # "more contrast" direction for text
    # Minimum contrast for everything that must stay readable / visible, whatever the theme.
    # (Each value is only nudged if it falls short - see ensure_contrast.)
    chrome = ensure_contrast(chrome, bg, 1.04)                     # tabs vs. the current tab
    fg = ensure_contrast(ensure_contrast(spec["fg"], bg, 4.5, away), chrome, 4.5, away)
    dim = ensure_contrast(ensure_contrast(spec["dim"], chrome, 3.0, fg), bg, 3.0, fg)
    select = ensure_contrast(spec["select"], bg, 1.25, spec["accent"])
    if contrast(select, bg) < 1.25:
        select = ensure_contrast(select, bg, 1.25, fg)
    accent = spec["accent"]
    border = ensure_contrast(spec.get("border") or mix(chrome, fg, 0.13 if dark else 0.12), chrome, 1.15)
    error = ensure_contrast(spec.get("error") or spec["red"], chrome, 3.0, away)
    select_fg = spec.get("select_fg") or best_text_on(select, fg, "#ffffff", "#000000")
    select_fg = ensure_contrast(select_fg, select, 4.5, best_text_on(select))
    p = {
        "name": spec["name"],
        "bg": bg,
        "surface": chrome,
        "surface2": mix(chrome, fg, 0.09 if dark else 0.06),          # buttons, tooltips
        "border": border,
        "fg": fg,
        "fg_dim": dim,
        "accent": accent,
        "accent_fg": spec.get("accent_fg") or best_text_on(accent, bg, "#ffffff", "#000000"),
        "select": select,
        "select_fg": select_fg,
        "hover": spec.get("hover") or mix(bg, fg, 0.07 if dark else 0.05),
        "chrome_hover": mix(chrome, fg, 0.08 if dark else 0.06),
        "alt_row": mix(bg, fg, 0.025 if dark else 0.02),
        "entry": mix(bg, "#000000", 0.22) if dark else mix(bg, "#ffffff", 0.7),
        "folder": spec["folder"],
        "page": mix("#ffffff", bg, 0.12) if dark else "#ffffff",           # paper colour of file icons
        "error": error,
        "ok": spec.get("ok") or spec["green"],
        "terminal_bg": spec.get("terminal_bg") or (chrome if dark else bg),
        "terminal_fg": fg,
    }
    # The selection colour of an unfocused view: the same hue, calmer.
    p["select_inactive"] = mix(select, bg, 0.4)
    # Quieter lines (column separators in the header) and scroll bar handles.
    p["line_soft"] = mix(p["border"], bg, 0.35)
    # Utility tabs (Search, Rename...): a gentle accent tint so they never look like a folder.
    p["utility_bg"] = mix(chrome, accent, 0.14 if dark else 0.10)
    p["utility_line"] = mix(p["border"], accent, 0.45)
    p["scroll"] = mix(bg, fg, 0.22 if dark else 0.2)
    p["scroll_hover"] = mix(bg, fg, 0.36 if dark else 0.32)
    p.update(_ansi(dict(spec, fg=fg, dim=dim), dark, p["terminal_bg"]))
    return p


def _ansi(spec: dict, dark: bool, term_bg: str) -> Dict[str, str]:
    """The 16 terminal colours, from the theme's colour set."""
    fg, dim = spec["fg"], spec["dim"]
    base = {name: spec[name] for name in ("red", "green", "yellow", "blue", "magenta", "cyan")}
    out = {"ansi_black": mix(term_bg, fg, 0.28) if dark else mix(fg, "#000000", 0.3),
           "ansi_white": mix(fg, term_bg, 0.12) if dark else mix(dim, term_bg, 0.25),
           "ansi_brightblack": dim,
           "ansi_brightwhite": mix(fg, "#ffffff", 0.5) if dark else mix(fg, "#000000", 0.4)}
    for name, color in base.items():
        out[f"ansi_{name}"] = color
        out[f"ansi_bright{name}"] = mix(color, "#ffffff", 0.22) if dark else mix(color, "#000000", 0.18)
    return out


PALETTES: Dict[str, Dict[str, str]] = {key: build(spec) for key, spec in SPECS.items()}


def display_name(key: str) -> str:
    return "System" if key == "system" else SPECS[key]["name"] if key in SPECS else key


def grouped() -> List[Tuple[str, List[str]]]:
    """[("Dark", [keys sorted by name]), ("Light", [...])] for menus."""
    dark = sorted((k for k in SPECS if is_dark(SPECS[k]["bg"])), key=lambda k: SPECS[k]["name"].lower())
    light = sorted((k for k in SPECS if not is_dark(SPECS[k]["bg"])), key=lambda k: SPECS[k]["name"].lower())
    return [("Dark", dark), ("Light", light)]
