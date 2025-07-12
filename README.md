# BookReviver

**BookReviver** is an AI-powered pipeline for restoring old scanned books into high-quality, searchable, and typeset-ready PDF editions using advanced OCR and text reconstruction.

## 🚀 Installation and Setup

### Prerequisites

- Python 3.12+
- uv (modern Python package manager)

### Install uv

If you don't have `uv` installed yet, run:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Activate Virtual Environment

```bash
# Activate virtual environment
source .venv/bin/activate

# Or if using uv
source $HOME/.local/bin/env && uv venv
```

### Install Dependencies

```bash
# Install production dependencies
uv pip install -r requirements.txt

# Install development dependencies
uv pip install -r requirements-dev.txt
```

### Run Application

```bash
# Run main file
python main.py
```

## 📁 Project Structure

```
BookReviver/
├── main.py                 # Main application file
├── src/                    # Source code
├── tests/                  # Tests
├── requirements.txt        # Production dependencies
├── requirements-dev.txt    # Development dependencies
```

