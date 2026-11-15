"""SPHEREx Sky Motion Viewer - backend package.

Public-facing web tool for the NASA Space Apps 2026 challenge
"Planet X and SPHEREx".  Given a sky position it finds every SPHEREx
spectral image covering that spot, extracts small cutouts directly from the
IRSA archive with HTTP range requests, aligns them on a common north-up
grid, stacks them per survey epoch and detector band, and serves the result
to a browser-based frame player.
"""

__version__ = "0.2.0"
