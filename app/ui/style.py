"""Shared visual tokens matching the project brief's dashboard guidance."""

from __future__ import annotations

import streamlit as st


_CSS = """
<style>
:root {
  --ik-ink: #17202a;
  --ik-muted: #667085;
  --ik-line: #e4e7ec;
  --ik-bg: #f6f7f9;
  --ik-blue: #2563eb;
  --ik-green: #16835f;
  --ik-purple: #7c3aed;
  --ik-coral: #d5533f;
}
.stApp { background: var(--ik-bg); color: var(--ik-ink); }
[data-testid="stHeader"] { background: rgba(246,247,249,.94); }
[data-testid="stMainBlockContainer"] { max-width: 1240px; padding-top: 2rem; }
[data-testid="stSidebar"] { background: #ffffff; border-right: 1px solid var(--ik-line); }
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p { color: var(--ik-muted); }
[data-testid="stVerticalBlockBorderWrapper"] {
  border: 1px solid var(--ik-line) !important;
  border-radius: 12px !important;
  background: #ffffff;
  box-shadow: 0 1px 2px rgba(16,24,40,.04);
}
[data-testid="stVerticalBlockBorderWrapper"] > div { padding: 16px !important; }
[data-testid="stMetric"] { background: transparent; padding: 0; }
[data-testid="stMetricValue"] { font-size: 1.65rem; letter-spacing: 0; }
[data-testid="stDataFrame"] { border: 1px solid var(--ik-line); border-radius: 8px; overflow: hidden; }
.stButton > button, .stDownloadButton > button { border-radius: 8px; min-height: 40px; }
.stButton > button[kind="primary"], .stDownloadButton > button[kind="primary"] {
  background: var(--page-accent, var(--ik-blue)); border-color: var(--page-accent, var(--ik-blue));
}
.stTextInput input, .stTextArea textarea, .stSelectbox [data-baseweb="select"] > div {
  border-radius: 8px;
}
.ik-brand { display:flex; align-items:center; gap:10px; margin: 2px 0 18px; }
.ik-brand-mark { width:30px; height:30px; border-radius:8px; background:#17202a; color:#fff;
  display:grid; place-items:center; font-weight:750; font-size:15px; }
.ik-brand-name { font-size:17px; font-weight:750; color:#17202a; }
.ik-brand-sub { color:#667085; font-size:12px; }
.ik-page-head { border-left: 4px solid var(--page-accent); padding-left: 16px; margin: 0 0 24px; }
.ik-page-head h1 { font-size: 30px; line-height: 1.2; margin:0 0 6px; letter-spacing:0; }
.ik-page-head p { color: var(--ik-muted); margin:0; font-size:14px; }
.ik-section-title { display:flex; align-items:center; justify-content:space-between; gap:16px; margin-bottom:8px; }
.ik-section-title strong { font-size:15px; color:var(--ik-ink); }
.ik-eyebrow { text-transform:uppercase; color:var(--page-accent); font-size:11px; font-weight:750; }
.ik-status { display:inline-flex; align-items:center; gap:7px; font-size:12px; font-weight:700; }
.ik-dot { width:8px; height:8px; border-radius:50%; background:#98a2b3; }
.ik-dot.ok { background:#16835f; }
.ik-dot.bad { background:#d5533f; }
.ik-muted { color:var(--ik-muted); font-size:13px; }
.ik-source { border-left:3px solid var(--page-accent); padding:8px 12px; background:#f8fafc; margin:8px 0; }
.ik-danger { color:#b42318; font-weight:650; }
.ik-blue { --page-accent: var(--ik-blue); }
.ik-green { --page-accent: var(--ik-green); }
.ik-purple { --page-accent: var(--ik-purple); }
.ik-coral { --page-accent: var(--ik-coral); }
@media (max-width: 720px) {
  [data-testid="stMainBlockContainer"] { padding: 1.25rem .8rem 3rem; }
  .ik-page-head h1 { font-size: 25px; }
  [data-testid="stHorizontalBlock"] { gap: .6rem; }
  [data-testid="stMetricValue"] { font-size: 1.35rem; }
}
</style>
"""


def apply_style(accent: str = "blue") -> None:
    st.markdown(_CSS, unsafe_allow_html=True)
    st.markdown(f"<div class='ik-{accent}'></div>", unsafe_allow_html=True)


def page_header(title: str, subtitle: str, accent: str) -> None:
    color = {
        "blue": "#2563eb",
        "green": "#16835f",
        "purple": "#7c3aed",
        "coral": "#d5533f",
    }[accent]
    st.markdown(f"<style>:root {{ --page-accent: {color}; }}</style>", unsafe_allow_html=True)
    st.markdown(
        f"<div class='ik-page-head ik-{accent}'><h1>{title}</h1><p>{subtitle}</p></div>",
        unsafe_allow_html=True,
    )


def section_title(title: str, eyebrow: str | None = None) -> None:
    suffix = f"<span class='ik-eyebrow'>{eyebrow}</span>" if eyebrow else ""
    st.markdown(
        f"<div class='ik-section-title'><strong>{title}</strong>{suffix}</div>",
        unsafe_allow_html=True,
    )


def status_label(connected: bool, text: str) -> None:
    state = "ok" if connected else "bad"
    st.markdown(
        f"<span class='ik-status'><span class='ik-dot {state}'></span>{text}</span>",
        unsafe_allow_html=True,
    )
