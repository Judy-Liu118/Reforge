"""Reforge forge-ember mascot — glowing metal block + flame, 28-col ANSI."""
from __future__ import annotations

_RST = "\033[0m"

# Flame: yellow core → red tip
_FY, _FO, _FR = 226, 214, 202
# Hot metal: highlight / main / outer
_MH, _MM, _MO = 220, 214, 208
# Metal lower / shadow edge
_MD, _MS = 172, 130
# Eyes / mouth (dark)
_EY = 16

_W = 28


def _mc(content: list[int]) -> list[int]:
    pad = _W - len(content)
    return [0] * (pad // 2) + content + [0] * (pad - pad // 2)


def _me() -> list[int]:
    return [0] * _W


_MASCOT_PIXELS: list[list[int]] = [
    _me(),
    # flame — fat base, wide, hugging the top of the block
    _mc([0, 0, 0, _FR, _FR, 0, 0, 0]),
    _mc([0, 0, _FR, _FO, _FO, _FR, 0, 0]),
    _mc([0, _FR, _FO, _FO, _FO, _FO, _FR, 0]),
    _mc([0, _FR, _FO, _FY, _FY, _FO, _FR, 0]),
    _mc([_FR, _FO, _FO, _FY, _FY, _FO, _FO, _FR]),
    _mc([0, _FO, _FO, _FO, _FO, _FO, _FO, 0]),
    # metal block top — rounded corners, glowing
    _mc([0, 0, _MH, _MH, _MH, _MH, _MH, _MH, _MH, _MH, _MH, _MH, _MH, _MH, 0, 0]),
    _mc([0, _MH, _MH, _MH, _MH, _MH, _MH, _MH, _MH, _MH, _MH, _MH, _MH, _MH, _MH, 0]),
    _mc([_MH, _MM, _MM, _MM, _MM, _MM, _MM, _MM, _MM, _MM, _MM, _MM, _MM, _MM, _MM, _MH]),
    _mc([_MM, _MM, _MH, _MH, _MM, _MM, _MM, _MM, _MM, _MM, _MM, _MM, _MM, _MM, _MM, _MM]),
    _mc([_MM, _MM, _MH, _MM, _MM, _MM, _MM, _MM, _MM, _MM, _MM, _MM, _MM, _MM, _MM, _MM]),
    # eyes
    _mc([_MM, _MM, _MM, _MM, _EY, _EY, _MM, _MM, _MM, _MM, _EY, _EY, _MM, _MM, _MM, _MM]),
    _mc([_MM, _MM, _MM, _MM, _EY, _EY, _MM, _MM, _MM, _MM, _EY, _EY, _MM, _MM, _MM, _MM]),
    # arms / mitt hands — block widens on both sides
    _mc([0, _MD, _MO] + [_MM] * 16 + [_MO, _MD, 0]),
    _mc([_MD, _MO, _MO] + [_MM, _MM, _MM, _MM, _MM, _EY, _MM, _MM, _MM, _MM, _EY, _MM, _MM, _MM, _MM, _MM] + [_MO, _MO, _MD]),
    _mc([0, _MD, _MO] + [_MM, _MM, _MM, _MM, _MM, _MM, _EY, _EY, _EY, _EY, _MM, _MM, _MM, _MM, _MM, _MM] + [_MO, _MD, 0]),
    # lower body
    _mc([_MM] * 16),
    _mc([_MO, _MO] + [_MM] * 12 + [_MO, _MO]),
    _mc([_MD, _MD] + [_MO] * 12 + [_MD, _MD]),
    _mc([0, _MS] + [_MD] * 12 + [_MS, 0]),
    # two feet
    _mc([0, 0, 0, _MD, _MD, _MD, 0, 0, 0, 0, _MD, _MD, _MD, 0, 0, 0]),
    _mc([0, 0, 0, _MS, _MS, _MS, 0, 0, 0, 0, _MS, _MS, _MS, 0, 0, 0]),
    _me(),
]


def _half_line(top: list[int], bot: list[int]) -> str:
    out = ""
    for t, b in zip(top, bot):
        if t == 0 and b == 0:
            out += " "
        elif t == 0:
            out += f"\033[38;5;{b}m▄{_RST}"
        elif b == 0:
            out += f"\033[38;5;{t}m▀{_RST}"
        else:
            out += f"\033[38;5;{b};48;5;{t}m▄{_RST}"
    return out


def _build_mascot() -> list[str]:
    lines = []
    for i in range(0, len(_MASCOT_PIXELS), 2):
        top = _MASCOT_PIXELS[i]
        bot = _MASCOT_PIXELS[i + 1] if i + 1 < len(_MASCOT_PIXELS) else _me()
        lines.append(_half_line(top, bot))
    return lines


MASCOT_LINES: list[str] = _build_mascot()
MASCOT_VW: int = 28
