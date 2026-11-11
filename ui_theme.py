"""Tema visual de la interfaz: oscuro, con acento lima y botones tipo píldora.

Siempre en modo oscuro (los mismos valores para el tema claro y el oscuro de Gradio).
"""
from __future__ import annotations

import inspect

import gradio as gr

BG = "#0a0a0b"
SURFACE = "#141416"
SURFACE_2 = "#1c1c1f"
BORDER = "#2a2a2e"
TEXT = "#f4f4f5"
MUTED = "#a1a1aa"
ACCENT = "#d4ff3a"
ACCENT_HOVER = "#e2ff73"
ACCENT_SOFT = "rgba(212, 255, 58, 0.12)"

FONT = [gr.themes.GoogleFont("Inter"), "system-ui", "Segoe UI", "sans-serif"]
FONT_MONO = [gr.themes.GoogleFont("JetBrains Mono"), "Consolas", "monospace"]


_THEME_VARS = set(inspect.signature(gr.themes.Base.set).parameters)


def _both(**kw: str) -> dict[str, str]:
    """Mismo valor para la variable clara y la *_dark (si existe)."""
    out: dict[str, str] = {}
    for k, val in kw.items():
        out[k] = val
        if f"{k}_dark" in _THEME_VARS:
            out[f"{k}_dark"] = val
    return out


def build_theme() -> gr.themes.Base:
    theme = gr.themes.Base(
        primary_hue=gr.themes.colors.lime,
        neutral_hue=gr.themes.colors.zinc,
        radius_size=gr.themes.sizes.radius_lg,
        text_size=gr.themes.sizes.text_lg,
        spacing_size=gr.themes.sizes.spacing_lg,
        font=FONT,
        font_mono=FONT_MONO,
    )
    return theme.set(**_both(
        body_background_fill=BG,
        body_text_color=TEXT,
        body_text_color_subdued=MUTED,
        background_fill_primary=SURFACE,
        background_fill_secondary=SURFACE_2,
        border_color_primary=BORDER,
        border_color_accent=ACCENT,
        color_accent=ACCENT,
        color_accent_soft=ACCENT_SOFT,
        link_text_color=ACCENT,
        link_text_color_hover=ACCENT_HOVER,
        block_background_fill=SURFACE,
        block_border_color=BORDER,
        block_label_background_fill=SURFACE_2,
        block_label_text_color=MUTED,
        block_title_text_color=TEXT,
        block_info_text_color=MUTED,
        panel_background_fill=SURFACE,
        panel_border_color=BORDER,
        input_background_fill=SURFACE_2,
        input_background_fill_focus=SURFACE_2,
        input_border_color=BORDER,
        input_border_color_focus=ACCENT,
        input_placeholder_color="#6b6b73",
        slider_color=ACCENT,
        checkbox_background_color=SURFACE_2,
        checkbox_background_color_selected=ACCENT,
        checkbox_border_color=BORDER,
        checkbox_border_color_selected=ACCENT,
        checkbox_label_background_fill=SURFACE_2,
        checkbox_label_background_fill_hover="#232327",
        checkbox_label_background_fill_selected=ACCENT_SOFT,
        checkbox_label_border_color=BORDER,
        checkbox_label_border_color_selected=ACCENT,
        checkbox_label_text_color=TEXT,
        checkbox_label_text_color_selected=ACCENT,
        button_primary_background_fill=ACCENT,
        button_primary_background_fill_hover=ACCENT_HOVER,
        button_primary_text_color="#0a0a0b",
        button_primary_border_color=ACCENT,
        button_secondary_background_fill=SURFACE_2,
        button_secondary_background_fill_hover="#26262a",
        button_secondary_text_color=TEXT,
        button_secondary_border_color=BORDER,
        table_even_background_fill=SURFACE,
        table_odd_background_fill=SURFACE_2,
        table_border_color=BORDER,
        table_row_focus=ACCENT_SOFT,
        code_background_fill=SURFACE_2,
        shadow_drop="none",
        shadow_drop_lg="none",
    ))


# Fuerza el modo oscuro aunque Windows esté en claro.
JS = "() => { document.body.classList.add('dark'); }"

CSS = f"""
.gradio-container {{ max-width: 1480px !important; margin: 0 auto !important; padding: 8px 20px 40px !important; box-sizing: border-box !important; }}
footer {{ display: none !important; }}

/* ---------- Cabecera ---------- */
#cf-header {{ display: flex; align-items: center; justify-content: space-between; gap: 16px;
  padding: 18px 4px 22px; border-bottom: 1px solid {BORDER}; margin-bottom: 8px; }}
#cf-header .brand {{ display: flex; align-items: center; gap: 14px; }}
#cf-header .logo {{ width: 44px; height: 44px; border-radius: 12px; background: {ACCENT};
  display: grid; place-items: center; color: #0a0a0b; font-size: 20px; font-weight: 900; }}
#cf-header h1 {{ margin: 0; font-size: 26px; font-weight: 800; letter-spacing: -0.02em; color: {TEXT}; }}
#cf-header p {{ margin: 2px 0 0; color: {MUTED}; font-size: 15px; }}
#cf-header .pill {{ border: 1px solid {BORDER}; color: {MUTED}; border-radius: 999px; padding: 6px 14px;
  font-size: 13px; font-family: 'JetBrains Mono', Consolas, monospace; }}

/* ---------- Tarjetas por paso ---------- */
.cf-card {{ background: {SURFACE} !important; border: 1px solid {BORDER} !important; border-radius: 20px !important;
  padding: 20px !important; gap: 14px !important; }}
.cf-step {{ display: flex; align-items: center; gap: 12px; margin: 0 0 2px; }}
.cf-step .num {{ width: 30px; height: 30px; border-radius: 999px; background: {ACCENT_SOFT}; color: {ACCENT};
  display: grid; place-items: center; font-weight: 800; font-size: 15px; flex: none; }}
.cf-step h2 {{ margin: 0; font-size: 19px; font-weight: 700; color: {TEXT}; letter-spacing: -0.01em; }}
.cf-step span.hint {{ color: {MUTED}; font-size: 14px; font-weight: 400; margin-left: 4px; }}
.cf-note, .cf-note p {{ color: {MUTED} !important; font-size: 14px !important; line-height: 1.55 !important; }}
.cf-section, .cf-section p {{ font-size: 15px !important; font-weight: 700 !important; color: {TEXT} !important;
  margin-top: 6px !important; }}

/* ---------- Botones tipo píldora ---------- */
button.lg, button.md, button.sm {{ border-radius: 999px !important; font-weight: 700 !important; }}
button.primary {{ box-shadow: 0 0 0 0 {ACCENT}; transition: transform .12s ease, box-shadow .2s ease, background .2s ease !important; }}
button.primary:hover {{ transform: translateY(-1px); box-shadow: 0 8px 28px -8px rgba(212,255,58,.55) !important; }}
button.secondary:hover {{ border-color: {ACCENT} !important; }}
#cf-go {{ min-height: 60px !important; font-size: 18px !important; font-weight: 800 !important; letter-spacing: .01em; }}

/* ---------- Interruptores rápidos como chips ---------- */
.cf-chip-row {{ flex-wrap: wrap !important; gap: 10px !important; }}
.cf-chip-row > .cf-chip {{ margin: 0 !important; }}
.cf-chip {{ flex: 0 0 auto !important; min-width: 0 !important; width: auto !important;
  background: {SURFACE_2} !important; border: 1px solid {BORDER} !important; border-radius: 999px !important;
  padding: 2px 14px !important; transition: border-color .15s ease, background .15s ease; }}
.cf-chip:hover {{ border-color: #4a4a52 !important; }}
.cf-chip:has(input:checked) {{ background: {ACCENT_SOFT} !important; border-color: {ACCENT} !important; }}
.cf-chip:has(input:checked) span {{ color: {ACCENT} !important; }}
.cf-chip label {{ gap: 8px !important; cursor: pointer; }}
.cf-chip span {{ font-size: 15px !important; font-weight: 600 !important; }}

/* Radios (modo, layout, estilo) como botones segmentados */
.cf-seg .wrap {{ gap: 8px !important; }}
.cf-seg label {{ border-radius: 14px !important; padding: 12px 16px !important; font-weight: 600 !important; }}
.cf-seg label.selected {{ border-color: {ACCENT} !important; box-shadow: inset 0 0 0 1px {ACCENT}; }}
.cf-actions .wrap {{ flex-direction: column !important; }}
.cf-moments {{ max-width: 100% !important; min-width: 0 !important; overflow-x: auto; }}
html, body {{ overflow-x: hidden; }}
.cf-moments table {{ table-layout: fixed; width: 100% !important; }}
.cf-actions label {{ width: 100% !important; padding: 16px 18px !important; font-size: 15px !important; }}

/* ---------- Pestañas ---------- */
.tab-wrapper, .tab-container {{ border-bottom: 1px solid {BORDER} !important; }}
.tab-container button {{ font-weight: 600 !important; font-size: 14px !important; color: {MUTED} !important;
  padding: 8px 10px !important; }}
.tab-container button.selected {{ color: {ACCENT} !important; }}
.tab-container button.selected::after {{ background: {ACCENT} !important; height: 2px !important; }}

/* ---------- Zona de subida ---------- */
#cf-upload {{ border: 1.5px dashed #3a3a40 !important; border-radius: 16px !important; min-height: 150px; }}
#cf-upload:hover {{ border-color: {ACCENT} !important; }}

/* Etiquetas más legibles */
label > span, .block-title, .label-wrap span {{ font-size: 14px !important; font-weight: 600 !important; }}
textarea, input {{ font-size: 15px !important; }}
#cf-player video, #cf-result img {{ max-height: 68vh; object-fit: contain; }}
#cf-log textarea {{ font-family: 'JetBrains Mono', Consolas, monospace !important; font-size: 13px !important;
  line-height: 1.55 !important; }}
"""


def header_html(version: str) -> str:
    return (
        '<div id="cf-header"><div class="brand"><div class="logo">▶</div>'
        "<div><h1>ClipForge</h1><p>Clips de gaming con subtítulos, listos para publicar</p></div></div>"
        f'<span class="pill">v{version}</span></div>'
    )


def step_html(num: int | str, title: str, hint: str = "") -> str:
    hint_html = f'<span class="hint">{hint}</span>' if hint else ""
    return f'<div class="cf-step"><div class="num">{num}</div><h2>{title}{hint_html}</h2></div>'
