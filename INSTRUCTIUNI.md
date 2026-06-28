# Cum rulezi testul termic (de la zero, Windows)

## 0. Instalează (o singură dată)
- **Python**: https://www.python.org/downloads/ — la instalare bifează **„Add python.exe to PATH"**.
- **Git**: https://git-scm.com/download/win — Next la tot.

## 1. Deschide PowerShell ca Administrator
Tasta Windows → scrie `powershell` → click dreapta → **Run as administrator**.
(Obligatoriu — fără admin nu poate controla ventilatoarele.)

## 2. Descarcă + pregătește (copiază liniile, Enter după fiecare)
```
cd $HOME\Desktop
git clone -b validare https://github.com/Sadgeq/ThermalAPP_Control.git
cd ThermalAPP_Control\pc-agent
python -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
.venv\Scripts\Activate.ps1
pip install -r requirements-experiment.txt
cd Backend\scripts
```

## 3. Găsește sarcina (`--procs`) — rulează, ajustează până vezi „OK"
```
python v2_calibrate.py --plateau --procs 2 --fan 50 --secs 120
```
- „OK" → ține numărul (N). | „PREA CALD" → încearcă cu unul mai mic. | „PREA RECE" → cu unul mai mare.

## 4. O singură comandă face tot (înlocuiește N)
```
python v2_all.py --procs N
```
Lasă calculatorul în pace ~30–40 min. La final scrie „GATA" și „fans returned to automatic".

## 5. Trimite rezultatele
Din `ThermalAPP_Control\pc-agent\Backend\scripts\` trimite folderele **`data`** și **`figures_v2`**
(selectează ambele → click dreapta → Send to → Compressed (zipped) folder).

## Dacă ceva nu merge
- „python is not recognized" → reinstalează Python cu „Add to PATH" bifat, redeschide PowerShell.
- eroare la `Activate.ps1` → rulează întâi linia `Set-ExecutionPolicy ...`.
- „no fan detected" → nu e deschis ca Administrator.
- se oprește cu „>90 C" → rulează din nou `v2_all.py --procs N` cu N mai mic.
