# Quick start (Windows)


Everything runs offline: no API key, no network, no database. Python 3.11+.

```powershell
cd D:\codeFiles\PycharmProjects\arbiter-bank
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Then:

```powershell
pytest tests/ -q                   # 406 passed
python -m arbiter.cli bench        # attacks blocked 20/20, benign 9/9, false positives 0
python -m arbiter.cli conv         # 41/41
python -m arbiter.cli audit        # 563/563, 0 violations
python -m arbiter.cli serve        # open http://127.0.0.1:8000  (中 / EN switch at the top right)
```

Next time only the `cd`, `Set-ExecutionPolicy` and `Activate.ps1` lines are needed.
