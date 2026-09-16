"""Regenerates a phonopy `band.yaml` (with eigenvectors) from janus-core's
own `*-phonopy.yml` + `*-force_constants.hdf5` output.

Fills a real gap in `janus`'s own CLI: it only ever writes band data to a
binary `*-auto_bands.hdf5`/`*-bands.hdf5`, never to YAML -- confirmed
against janus-core's own tutorial
(https://stfc.github.io/janus-core/tutorials/cli/phonons.html), which
visualizes bands purely from the SVG plot, never converts to YAML either.
This is *not* part of janus-core's public CLI -- it's a small, single-
purpose use of phonopy's own Python API (already installed here as a
janus-core dependency, so this adds no new weight), invoked as its own
subprocess by
`goldilocks_agent.tools.mlip_playground.client.run_phonons()`.

`auto_band_structure()` (not a hand-picked path) is phonopy's own
seekpath-derived automatic band path -- the same "auto" that names
janus-core's own `*-auto_bands.hdf5`, so this reconstructs the same band
path janus-core plotted, just also exporting eigenvectors as human/tool
readable YAML instead of leaving them opaque in HDF5.

Usage: python render_band_yaml.py <phonopy.yml> <force_constants.hdf5> <out band.yaml>
"""

from __future__ import annotations

import sys

import phonopy


def main() -> None:
    phonopy_yaml, force_constants_path, out_path = sys.argv[1:4]
    ph = phonopy.load(
        phonopy_yaml=phonopy_yaml, force_constants_filename=force_constants_path
    )
    ph.auto_band_structure(with_eigenvectors=True, write_yaml=True, filename=out_path)


if __name__ == "__main__":
    main()
