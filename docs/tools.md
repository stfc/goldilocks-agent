# Tools

You can ask for everything in the chat. The Tools on the right show the same
work as panels: open one from the Tools list, or let the chat open it. The
expand button at the top of the list shows every Tool full-page.

## Find in Databases

Searches Materials Project, Materials Cloud, NOMAD and JARVIS for a chemical
formula, for example "find NaCl" in the chat or a formula in the panel.
Entries that are the same structure are grouped, and within each database
the lowest-energy structure is marked. Import one into the chat to use it.

Materials Project needs an API key (see [Configuration](configuration.md));
without one it is skipped and the other databases are searched.

## DFT Workbench

goldilocks-core's Workbench, built in. Give it a structure, attached in the
chat or uploaded in the panel, and pick a calculation:

- **Structure**: the structure it read.
- **Analysis**: what goldilocks-core found about it, such as whether it is a
  metal or magnetic. Magnetism comes from goldilocks-core's ML model where it
  can; for a magnetic structure, **Run mMACE** ranks its possible magnetic
  orderings.
- **Advisors**: the recommended parameters (functional, cutoffs, k-points,
  smearing, pseudopotentials, ...), each with where it came from.
- **Bundles**: the Quantum ESPRESSO input files, to download.

The chat can do the same steps for you: open a structure, review the
recommendation (shown as a card in the chat) and generate the input files.
The values always come from goldilocks-core, never from the chat model.

In the Tools panel the cards are tabs; full-page they sit side by side.

## Post Analysis

Plots results you have already calculated. For now that is phonon band
structures: upload a phonopy `band.yaml`. More analyses are coming.

## Local version only

These are in the [local version](getting-started.md), not on the website:

- **MLIP Playground** runs MACE machine-learned interatomic potentials on a
  structure: single-point energy, geometry optimisation, equation of state,
  NEB and phonons. When the chat runs one for you, it asks first.
- **AiiDAlab-QE** is listed but not built yet: it will run Quantum ESPRESSO
  through AiiDAlab's QE app.

## Coming soon

**Beyond DFT** (GW, BSE, QMC, TDDFT, DFT+DMFT and more) is shown in the
full-page Tools view but can't be opened yet.
