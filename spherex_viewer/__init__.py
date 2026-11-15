"""BeyondtheBlue: backend package.

Given a sky position it finds every SPHEREx spectral image covering that spot,
extracts small cutouts directly from the IRSA archive with HTTP range requests,
aligns them on a common north-up grid, stacks them per survey epoch and
detector band, and serves the result to a browser-based frame player.
"""

__version__ = "0.2.0"
