# Shift Scheduler App (Streamlit + PuLP)

A lightweight weekly shift scheduling tool. Upload a **15x7 demand XLSX or XLS** (15 hourly slots x Mon..Sun),
edit your **staff list** directly in the app (add/remove rows), set a **max deviation per slot**, and solve.

## Folder structure
```text
shift_scheduler_app/
├── streamlit_app_v2.py             # Streamlit UI (file upload, staff editor, parameters, results & downloads)
├── optimizer_v2.py                 # PuLP model (variables, constraints, objective, CBC solve)
├── utils.py                        # Help functions utilized in streamlit_app_v2.py and optimizer_v2.py
├── requirements.txt                # Dependencies
├── README.md                       # This guide
└── Demand_AllStores_Template.xlsx  # 1 sheet per location, 18x7 template per sheet
└── Staff_AllStores_Template.xlsx   # table of staff across all locations and their info
```

## Quick start
```bash
pip install -r requirements.txt
streamlit run streamlit_app_v2.py
```

## Demand tables (Demand_AllStores_Template.xlsx)
Each sheet is for a different location, as well as one for Management (i.e., not tied to a specific location). Each sheet has the following:
- **Shape**: 18 rows × 7 columns (no header).
- **Rows**: 18 hourly slots (08-09, 09-10, 10-11, ..., 23-00, 00-01, 01-02).
- **Columns**: 1=Monday, ..., 7=Sunday.
- **Values**: headcount demand per slot (you can use sales/100 as an approximation).
> *Note*: Each sheet must have the name of the location as the sheet name (i.e., CATEDRAL for Catedral's demand). Additionally, the format must remain the same for each sheet - only update the demand values within the table.

## Staff table (Demand_AllStores_Template.xlsx)
Columns:
- `name` (string, unique)
- `min_week_hours` (float/int)
- `max_week_hours` (float/int)
- `contract_hours` (float/int, info only)
- `weekend_only` (bool) — if True, worker can only be scheduled on Fri/Sat/Sun
- `location` (character)
- `role` (character)
> *Note*: The template contains data validation to prevent misspellings. If new values are added (e.g., locations, roles, etc.), update the mapping in the "Options" tab and the corresponding data validation for the column.
You can **Upload** an existing staff xlsx/xls or **Download** the current table for reuse.

## Model (high level)
- Decision: assign at most one shift per worker per day; each shift is 4–8 hours.
- Coverage: minimize total deviation (under + over) with a **cap** per slot.
- Intra-location coverage: At least one shift adult (i.e., Supervisor, Shop Manager, Team Leader, Shift Manager) is staffed for each time slot in a location, with some leeway provided there is a manager elsewhere who can respond to emergencies.
- Cross-location coverge: At least one on-duty adult (i.e., General Manager, Supervisor, Shop Manager, Team Leader) is staffed for each time slot across locations, with a small margin for edge time slots (e.g., 01-02).
- Per-worker weekly hours in [min_week_hours, max_week_hours].
- Weekend-only workers: disallow Mon–Thu assignments.

> Note: Consecutive rest days, late-to-early gap, or other business rules can be added in `optimizer_v2.py` in the same style.

## Troubleshooting
- If Highs or your chosen solver (e.g., CBC) is missing, upgrade PuLP (`pip install -U pulp`) or install the solver in your system.
- To add a new solver, in your terminal:
    - Enter: python -m pip install "pulp[highs]" (if on a Mac, include quotations, else remove quotations)
        - Input your chosen solver within the [] brackets (e.g., cbc)
    - Enter: brew install highs
        - Or your chosen solver instead of highs
- The solver time limit is set at 24 hours. Adjust it in the backend if you want to decrease or increase it.
