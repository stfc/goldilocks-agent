# Goldilocks Web

> ⚠️ **Stale clone instructions (2026-09-17)**: this file is a vendored copy of the old
> `goldilocks-web` prototype's own README, kept here for reference since its code now lives
> in this directory (`goldilocks-agent/app/`), not as a live remote. The GitHub repos
> `stfc/goldilocks-web` / `junwen94/goldilocks-web` referenced below have since been
> repurposed as the Goldilocks **ecosystem landing page** (see
> [`goldilocks-ecosystem-design.md`](../docs/goldilocks-ecosystem-design.md), section 一) —
> cloning them today gets you that static site, not this chat-first prototype. To work on
> this code, use `goldilocks-agent`'s own repository instead.

Goldilocks Web is a chat-first frontend prototype for computational materials research.

It explores a ChatGPT-like interaction model with domain-specific workflows such as:

- `Structure Match`
- `DFT Workbench`
- `MLIP Playground`

This repository currently contains a React + Vite prototype intended for UI and product exploration.

## Current Status

This is a frontend-only prototype.

- The assistant responses are currently mocked in the browser
- No backend service is required to run the current version
- The app is intended for local development, demos, and design iteration

## Installation

### Requirements

Please make sure you have the following installed:

- Node.js 20 or newer
- npm 10 or newer

You can check your versions with:

```bash
node -v
npm -v
```

### 1. Clone the repository

Using SSH:

```bash
git clone git@github.com:junwen94/goldilocks-web.git
cd goldilocks-web
```

Using HTTPS:

```bash
git clone https://github.com/stfc/goldilocks-web.git
cd goldilocks-web
```

If you want to run the prototype branch specifically:

```bash
git switch prototype-ui
```

### 2. Install dependencies

```bash
npm install
```

### 3. Start the development server

```bash
npm run dev
```

After this, Vite will print a local URL in the terminal, usually:

```text
http://localhost:5173/
```

Open that address in your browser.

## Alternative: Production Preview

If you want to run the built version locally instead of the development server:

### 1. Build the app

```bash
npm run build
```

### 2. Preview the production build

```bash
npm run preview
```

Then open the local preview URL shown in the terminal.
- Beginner and expert onboarding flows

## Available Scripts

- `npm run dev`
  Start the development server

- `npm run build`
  Build the app for production

- `npm run preview`
  Preview the production build locally

- `npm run lint`
  Run ESLint

## Features in the Current Prototype

- Chat-first interface
- Project-based conversation organisation
- Onboarding for different experience levels
- Light and dark theme toggle
- Persistent workflow modes
- Periodic table helper
- Structure viewer for uploaded files

## Project Structure

```text
goldilocks-web/
├── public/
├── src/
│   ├── assets/
│   ├── App.css
│   ├── App.jsx
│   ├── index.css
│   └── main.jsx
├── design.md
├── index.html
├── package.json
└── vite.config.js
```

## Notes

- Some UI preferences are stored in browser local storage
- The current prototype does not require any API keys
- The current prototype does not yet connect to a real model backend

## Design Notes

High-level design decisions are tracked in [`design.md`](./design.md).
