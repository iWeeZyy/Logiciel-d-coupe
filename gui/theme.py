"""Tokens de design (couleurs/typo/espacements) + feuille de style Qt (QSS)
generee a partir d'eux. Aucune couleur/police ne doit etre ecrite en dur
ailleurs dans gui/ -- tout passe par ce fichier, seul endroit a modifier
pour ajuster l'identite visuelle.

Palette : cote clair, sidebar sombre -- pattern d'app "outil" moderne
(Linear/Notion/Figma), pas un vieux look Windows. Accent ambre, deliberement
choisi pour eviter le cliche "vert acide/vermillon sur fond quasi-noir" :
il reste chaleureux (coherent avec le 🔥 des scores de hook) sans etre criard.

Typographie : Segoe UI (police systeme Windows, natif, zero risque de repli)
pour tout le texte d'interface -- c'est le choix le plus "vraie appli
Windows moderne" possible, plus fiable qu'importer une police web dans une
appli desktop. JetBrains Mono (assets/fonts/, licence OFL, embarquee) est
reservee aux donnees chiffrees alignees (scores, timestamps, durees) : un
detail deliberement lie aux racines CLI de l'outil, pas une police d'appoint
generique.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QFontDatabase

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
FONTS_DIR = ASSETS_DIR / "fonts"

FONT_FAMILY = "Segoe UI"
FONT_FAMILY_FALLBACK = f'"{FONT_FAMILY}", "Segoe UI Variable", Arial, sans-serif'
MONO_FAMILY = "JetBrains Mono"
MONO_FAMILY_FALLBACK = f'"{MONO_FAMILY}", Consolas, monospace'

COLORS = {
    "bg": "#F5F6F8",
    "surface": "#FFFFFF",
    "surface_alt": "#FAFBFC",
    "border": "#E3E5EA",
    "ink": "#1B1D22",
    "muted": "#6C707B",
    "muted_2": "#9498A2",
    "sidebar_bg": "#16181D",
    "sidebar_border": "#24272E",
    "sidebar_text": "#9AA0AC",
    "sidebar_text_active": "#FFFFFF",
    "sidebar_active_bg": "#20232B",
    "accent": "#E0A24C",
    "accent_hover": "#EAB05E",
    "accent_pressed": "#CC8F3E",
    "accent_ink": "#241505",
    "accent_soft": "#FBEEDA",
    "success": "#3FAF77",
    "success_soft": "#E4F6EC",
    "danger": "#E1554A",
    "danger_soft": "#FBEAE8",
    "danger_hover": "#EA6459",
}

SPACING = {"xs": 4, "sm": 8, "md": 16, "lg": 24, "xl": 32, "xxl": 48}
RADIUS = 10
RADIUS_SM = 6
SIDEBAR_WIDTH = 232


def load_fonts() -> None:
    """A appeler une fois, avant de creer la moindre fenetre."""
    if not FONTS_DIR.exists():
        return
    for ttf in FONTS_DIR.glob("*.ttf"):
        QFontDatabase.addApplicationFont(str(ttf))


def build_stylesheet() -> str:
    c = COLORS
    return f"""
    * {{
        font-family: {FONT_FAMILY_FALLBACK};
        color: {c['ink']};
        outline: none;
    }}

    QMainWindow, #ContentArea {{
        background: {c['bg']};
    }}

    /* ---------- Barre laterale ---------- */
    #Sidebar {{
        background: {c['sidebar_bg']};
        border-right: 1px solid {c['sidebar_border']};
    }}
    #SidebarBrand {{
        color: {c['sidebar_text_active']};
        font-size: 17px;
        font-weight: 700;
        padding: {SPACING['lg']}px {SPACING['md']}px {SPACING['sm']}px;
    }}
    #SidebarTagline {{
        color: {c['sidebar_text']};
        font-size: 11.5px;
        padding: 0 {SPACING['md']}px {SPACING['lg']}px;
    }}
    QPushButton[navItem="true"] {{
        background: transparent;
        color: {c['sidebar_text']};
        border: none;
        border-radius: {RADIUS_SM}px;
        text-align: left;
        padding: 11px 14px;
        font-size: 13.5px;
        font-weight: 500;
        margin: 2px 10px;
    }}
    QPushButton[navItem="true"]:hover {{
        background: {c['sidebar_active_bg']};
        color: {c['sidebar_text_active']};
    }}
    QPushButton[navItem="true"]:checked {{
        background: {c['sidebar_active_bg']};
        color: {c['sidebar_text_active']};
        border-left: 3px solid {c['accent']};
        padding-left: 11px;
    }}

    /* ---------- Typographie generique ---------- */
    QLabel[role="pageTitle"] {{
        font-size: 22px;
        font-weight: 700;
        color: {c['ink']};
    }}
    QLabel[role="subtitle"] {{
        font-size: 13.5px;
        color: {c['muted']};
    }}
    QLabel[role="sectionLabel"] {{
        font-size: 11.5px;
        font-weight: 700;
        color: {c['muted_2']};
        letter-spacing: 0.06em;
    }}
    QLabel[role="muted"] {{
        color: {c['muted']};
        font-size: 12.5px;
    }}
    QLabel[role="mono"] {{
        font-family: {MONO_FAMILY_FALLBACK};
        color: {c['ink']};
    }}

    /* ---------- Cartes ---------- */
    QFrame[role="card"] {{
        background: {c['surface']};
        border: 1px solid {c['border']};
        border-radius: {RADIUS}px;
    }}
    QFrame[role="dropzone"] {{
        background: {c['surface_alt']};
        border: 2px dashed {c['border']};
        border-radius: {RADIUS}px;
    }}
    QFrame[role="dropzone"][active="true"] {{
        background: {c['accent_soft']};
        border: 2px dashed {c['accent']};
    }}

    /* ---------- Boutons ---------- */
    QPushButton {{
        border-radius: {RADIUS_SM}px;
        padding: 9px 18px;
        font-size: 13px;
        font-weight: 600;
        border: 1px solid {c['border']};
        background: {c['surface']};
        color: {c['ink']};
    }}
    QPushButton:hover {{ background: {c['surface_alt']}; }}
    QPushButton:disabled {{ color: {c['muted_2']}; background: {c['surface_alt']}; }}

    QPushButton[variant="primary"] {{
        background: {c['accent']};
        border: 1px solid {c['accent']};
        color: {c['accent_ink']};
    }}
    QPushButton[variant="primary"]:hover {{ background: {c['accent_hover']}; }}
    QPushButton[variant="primary"]:pressed {{ background: {c['accent_pressed']}; }}
    QPushButton[variant="primary"]:disabled {{
        background: {c['border']}; border-color: {c['border']}; color: {c['muted_2']};
    }}

    QPushButton[variant="danger"] {{
        background: {c['surface']};
        border: 1px solid {c['danger']};
        color: {c['danger']};
    }}
    QPushButton[variant="danger"]:hover {{ background: {c['danger_soft']}; }}

    QPushButton[variant="ghost"] {{
        background: transparent;
        border: 1px solid transparent;
        color: {c['muted']};
    }}
    QPushButton[variant="ghost"]:hover {{ background: {c['surface_alt']}; color: {c['ink']}; }}

    /* ---------- Champs ---------- */
    QLineEdit, QComboBox, QSpinBox {{
        background: {c['surface']};
        border: 1px solid {c['border']};
        border-radius: {RADIUS_SM}px;
        padding: 8px 10px;
        font-size: 13px;
        selection-background-color: {c['accent_soft']};
    }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{
        border: 1px solid {c['accent']};
    }}
    QComboBox::drop-down {{ border: none; width: 24px; }}
    QComboBox QAbstractItemView {{
        background: {c['surface']};
        border: 1px solid {c['border']};
        selection-background-color: {c['accent_soft']};
        selection-color: {c['ink']};
        outline: none;
    }}

    /* ---------- Progression ---------- */
    QProgressBar {{
        background: {c['border']};
        border: none;
        border-radius: 5px;
        height: 10px;
        text-align: center;
    }}
    QProgressBar::chunk {{
        background: {c['accent']};
        border-radius: 5px;
    }}

    /* ---------- Divers ---------- */
    QScrollArea {{ border: none; background: transparent; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
    QScrollBar::handle:vertical {{ background: {c['border']}; border-radius: 5px; min-height: 24px; }}
    QScrollBar::handle:vertical:hover {{ background: {c['muted_2']}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}

    QSlider::groove:horizontal {{
        height: 4px; background: {c['border']}; border-radius: 2px;
    }}
    QSlider::handle:horizontal {{
        background: {c['accent']}; width: 13px; height: 13px;
        margin: -5px 0; border-radius: 6px;
    }}
    QSlider::sub-page:horizontal {{ background: {c['accent']}; border-radius: 2px; }}

    QCheckBox::indicator {{
        width: 16px; height: 16px; border-radius: 4px;
        border: 1px solid {c['border']}; background: {c['surface']};
    }}
    QCheckBox::indicator:checked {{ background: {c['accent']}; border-color: {c['accent']}; }}
    """
