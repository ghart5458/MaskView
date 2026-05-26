# MaskView  
Visualization tool for comparing and annotating CT segmentation masks. Written with Claude. 

## Overview

### Key Features
- PAR file compatibility
    - Automatically retrieves relevant mask files; pre-loads next
    - Generates mask note logs (pass/review/fail) corresponding to PAR file entries
- Interactive color overlay, histogram, threshold
- Synced multi-view - compare up to four files simultaneously 
- 3D tags - place 3D markers or notes at features of interest
- Anchor sync - synchronize files of different dimensions by setting anchor points
- Optional subsampled loading for large files or slow network transfer

### Setup

#### Running from source (bat file)

Requires [uv](https://docs.astral.sh/uv/getting-started/installation/).

1. Clone or download this repository
2. Open a terminal in the project folder and run:
   ```
   uv sync
   ```
3. Double-click `MaskView.bat`

`uv sync` creates a virtual environment and installs all dependencies automatically. You only need to do it once.

#### Building a standalone app (no Python required)

Requires [uv](https://docs.astral.sh/uv/getting-started/installation/).

1. Clone or download this repository
2. Open a terminal in the project folder and run:
   ```
   uv sync
   .venv\Scripts\activate
   pyinstaller MaskView.spec
   ```
3. The finished app will be in `dist/MaskView/` — run `MaskView.exe` inside that folder

> **Note:** The `.venv\Scripts\activate` step activates the virtual environment created by `uv sync`. PyInstaller must run inside the venv so it can find all dependencies.

The `dist/MaskView/` folder is self-contained and can be copied anywhere or shared with users who don't have Python installed.

## TODO
- Support custom directory structures
- Additional UI themes
