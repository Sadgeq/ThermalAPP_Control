# Runbook — validarea v2 (STANDALONE, fără agent/cloud/app)

Campanie de validare **cap-coadă** pe desktop, exact ca data trecută cu
`desktop_step_response.py`: **acces direct la hardware**, fără agent, fără
cloud, fără aplicație, fără pairing. PID-ul rulează în-proces folosind aceeași
clasă `PidController` din `profiles.py`.

> Toate comenzile se rulează dintr-un terminal **ca Administrator**
> (LibreHardwareMonitor are nevoie de admin pentru senzori + scriere PWM).

## Pregătire (o singură dată)
```powershell
git clone -b Quality-of-life-changes https://github.com/Sadgeq/ThermalAPP_Control.git
cd ThermalAPP_Control\pc-agent
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-experiment.txt
cd Backend\scripts
```
Atât — **nu** trebuie `requirements.txt`, nici agentul, nici cont/cloud.

Plasă de siguranță: orice rulare se oprește la CPU > 90 °C. Între rulări lasă
~60 s de răcire.

---

## ⭐ Calea simplă — DOAR 2 comenzi (recomandat)

Prietenul face manual un singur lucru: **găsește `--procs`** (sarcina), apoi rulează
**o singură comandă** care face automat tot restul (bandă → model → acordare → 3
scenarii → grafice), fără să copieze niciun număr.

```powershell
# 1. Găsește N: rulează de 1-3 ori, ajustând pana cand plateau ~80 °C
python v2_calibrate.py --plateau --procs 3 --fan 50 --secs 120

# 2. Totul automat (înlocuiește N cu numărul găsit):
python v2_all.py --procs N
```
La final, în `Backend\scripts\` apar folderele **`data\`** (toate `v2_*.csv` +
`v2_summary.txt`) și **`figures_v2\`** (grafice + `v2_metrics.csv`). **Trimite-le.**

> Opțional: `--setpoint 75` dacă vrei o țintă anume (altfel ia mijlocul benzii).
> Durează ~30–40 min, nesupravegheat. Dacă se oprește la >90 °C → reia cu `--procs` mai mic.

Atât. Restul documentului (pașii manuali) e doar pentru control fin / depanare.

---

## Manual, pas cu pas (opțional — orchestratorul `v2_all.py` le face pe toate)

## Pas 1 — Calibrează sarcina (alege `--procs`)
Țintă: plateau **78–82 °C** la ventilatoare moderate (50 %).
```powershell
python v2_calibrate.py --plateau --procs 3 --fan 50 --secs 180
```
prea cald (>85) → scade `--procs`; prea rece (<70) → crește. **Reține N.**

## Pas 2 — Banda realizabilă + câștigul
```powershell
python v2_calibrate.py --band --procs N
```
Notează **GAIN** (câștigul de proces) și **setpoint-ul sugerat** (mijlocul benzii).

## Pas 3 — Modelul termic (τ)
```powershell
python v2_model_id.py --procs 6 --output data/v2_model.csv
```
Notează **TAU** (vrei R² ≥ 0,90).

## Pas 4 — Acordează gains
```powershell
python v2_tune_gains.py --tau TAU --gain GAIN
```
Notează **KP, KI, KD** afișate (le dai direct la Pas 5 cu `--kp/--ki/--kd` —
nu trebuie să editezi niciun fișier, nu există agent de repornit).

---

## Pas 5 — Cele 3 scenarii (sarcina N, setpoint-uri în bandă)
Exemplu cu setpoint 75 și treaptă 80→72 (înlocuiește N, KP, KI, KD):
```powershell
python v2_run.py --mode curve --procs N --output data/v2_curve.csv

python v2_run.py --mode pid --setpoint 75 --kp KP --ki KI --kd KD ^
    --procs N --output data/v2_pid75.csv

python v2_run.py --mode pid-step --t1 80 --t2 72 --switch 150 ^
    --kp KP --ki KI --kd KD --procs N --output data/v2_pid_step.csv
```
(`^` = continuare de linie în PowerShell/cmd; sau scrie comanda pe un rând.)

## Pas 6 — Grafice + metrici
```powershell
python v2_plot.py --curve data/v2_curve.csv --pid data/v2_pid75.csv:75 ^
    --step data/v2_pid_step.csv:72 --outdir figures_v2
```
Produce `figures_v2\v2_temp_overlay.png`, `v2_fan_overlay.png`, `v2_metrics.csv`.

## Trimite înapoi
Din `Backend\scripts\`: folderul **`data\`** (toate `v2_*.csv`) și **`figures_v2\`**.

---

## Cum arată un rezultat BUN
- **Temperatură:** liniile PID se așază plat lângă setpoint (ripple ±1–2 °C);
  scenariul cu treaptă scade curat 80→72 și se stabilizează. Fără căderi bruște.
- **Ventilator:** PWM modulează la mijloc (40–70 %), **nu** lipit de 0/100 %.
- **Metrici:** RMS PID mic (țintă < ~2 °C). Curba se așază la alt punct decât
  setpoint-ul → doar PID-ul ține valoarea aleasă de utilizator.

## Iterare (repari SETUP-ul, nu datele)
- Oprire la >90 °C → scade `--procs`, reia.
- Oscilații/overshoot → în Pas 4 rulează `v2_tune_gains.py --tau TAU --gain GAIN --lam <1.5*TAU>`,
  ia noii KP/KI/KD, reia Pas 5.
- Răspuns prea lent → `--lam <TAU/2>`, idem.
- Setpoint neatins (PWM lipit de 100 %) → ridică setpoint-ul în bandă.
