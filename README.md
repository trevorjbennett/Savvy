# Savvy Desktop App

This application demonstrates a semantic search desktop UI built with [Flet](https://flet.dev/).

## Running

Install dependencies and run the app:

```bash
pip install -r requirements.txt
python main.py
```

The application expects `data.json` and `vector_index.pkl` in the same directory.
If they are missing the app will start but display an error screen.

## Code Structure

- `data_loader.py` – loads the semantic model and data files.
- `search.py` – implements search utilities.
- `ipc.py` – launches a background process for search operations.
- `ui.py` – UI logic using Flet.
- `main.py` – entry point which starts the UI and search worker.
