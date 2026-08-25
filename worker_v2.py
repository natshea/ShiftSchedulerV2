# ------------------ Imports ------------------

import streamlit as st
from utils import read_table_file_demand, read_table_file_staff, build_shift_set_fallback, create_excel_download
from collections import defaultdict

try:
    import pandas as pd
except Exception:
    st.error("Missing pandas")
    st.stop()

try:
    import numpy as np
except Exception:
    st.error("Missing numpy")
    st.stop()


# ------------------ Defaults ------------------
DEFAULT_STAFF = [
    {"name":"Ana","min_week_hours":30},
    {"name":"Vanessa_M","min_week_hours":25},
    {"name":"Ines","min_week_hours":30},
    {"name":"Yuliia","min_week_hours":20},
    {"name":"Giulia","min_week_hours":25},
]

for r in DEFAULT_STAFF:
    r["max_week_hours"] = min(40, float(r["min_week_hours"]) * 1.3)
    r["contract_hours"] = float(r["min_week_hours"])
    r["weekend_only"] = False
    r["location"] = "CATEDRAL"
    r["role"] = "Regular Employee"

DEFAULT_DEMAND = {
    "CATEDRAL": pd.DataFrame(
        [[0]*7 for _ in range(18)],
        columns=[
            "MONDAY",
            "TUESDAY",
            "WEDNESDAY",
            "THURSDAY",
            "FRIDAY",
            "SATURDAY",
            "SUNDAY"
        ]
    )
}


# ------------------ Mappings ------------------
day_map = {
    1: "MONDAY",
    2: "TUESDAY",
    3: "WEDNESDAY",
    4: "THURSDAY",
    5: "FRIDAY",
    6: "SATURDAY",
    7: "SUNDAY"
}

time_map = {
    1: "08:00 - 09:00",
    2: "09:00 - 10:00",
    3: "10:00 - 11:00",
    4: "11:00 - 12:00",
    5: "12:00 - 13:00",
    6: "13:00 - 14:00",
    7: "14:00 - 15:00",
    8: "15:00 - 16:00",
    9: "16:00 - 17:00",
    10: "17:00 - 18:00",
    11: "18:00 - 19:00",
    12: "19:00 - 20:00",
    13: "20:00 - 21:00",
    14: "21:00 - 22:00",
    15: "22:00 - 23:00",
    16: "23:00 - 00:00",
    17: "00:00 - 01:00",
    18: "01:00 - 02:00"
}

time_map_hour = {
    1: "08:00",
    2: "09:00",
    3: "10:00",
    4: "11:00",
    5: "12:00",
    6: "13:00",
    7: "14:00",
    8: "15:00",
    9: "16:00",
    10: "17:00",
    11: "18:00",
    12: "19:00",
    13: "20:00",
    14: "21:00",
    15: "22:00",
    16: "23:00",
    17: "00:00",
    18: "01:00", 
    19: "02:00"
}

# Label mappings for the UI
SLOT_LABELS = ["08-09","09-10","10-11","11-12","12-13","13-14","14-15","15-16","16-17",
               "17-18","18-19","19-20","20-21","21-22","22-23","23-00","00-01","01-02"]
DAY_LABELS = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]
DAY_LABELS_FULL = ["Mon (1)","Tue (1)","Wed (1)","Thu (1)","Fri (1)","Sat (1)","Sun (1)", 
                   "Mon (2)","Tue (2)","Wed (2)","Thu (2)","Fri (2)","Sat (2)","Sun (2)", 
                   "Mon (3)","Tue (3)","Wed (3)","Thu (3)","Fri (3)","Sat (3)","Sun (3)", 
                   "Mon (4)","Tue (4)","Wed (4)","Thu (4)","Fri (4)","Sat (4)","Sun (4)"]

# ------------------ Optimizer import ------------------
opt_mod = None
opt_import_error = None
try:
    import optimizer_v2 as opt_mod
except Exception as e:
    opt_import_error = e

# ------------------ Adapter ------------------
def adapt_to_user_optimizer(demand_df, staff_df, max_dev, unavailability, priority_slots): 
    if opt_mod is None or not hasattr(opt_mod, "build_and_solve_shift_model"):
        return None

    # --------------------------------------------------------------------
    # Sets
    # --------------------------------------------------------------------

    W = list(staff_df["name"].astype(str))
    D = list(range(1, 29)) # all days of 4 weeks
    T = list(range(1, 19)) # 18 time slots
    S = build_shift_set_fallback(T)
    U = list(staff_df["location"].unique().astype(str)) 


    # --------------------------------------------------------------------
    # Parameters
    # --------------------------------------------------------------------
    MinHw = {row["name"]: float(row["min_week_hours"]) for _, row in staff_df.iterrows()}
    MaxHw = {row["name"]: float(row["max_week_hours"]) for _, row in staff_df.iterrows()}
    WeekendOnly = {row["name"]: bool(row["weekend_only"]) for _, row in staff_df.iterrows()}
    Rolew = {row["name"]: row["role"] for _, row in staff_df.iterrows()}
    Locw = {row["name"]: row["location"] for _, row in staff_df.iterrows()}

    Demand = {} # create new Dem_{u,d,t} instead of Dem_{d,t}
    for u, df in demand_df.items():

        # Check if individual dataframes within the dict are empty
        if df is None or df.empty:
            continue

        # time slots
        df = df.set_index(df.columns[0])

        for t_idx, t_raw in enumerate(df.index):
            t = t_idx + 1 # convert rows (time slots) to index (1, 2, ..., 18)

            for week in range(4):
                for d_idx, d_raw in enumerate(df.columns):
                    d = week * 7 + d_idx + 1 # day index across 4 weeks

                    Demand[(u, d, t)] = float(df.loc[t_raw, d_raw]) # get ("CATEDRAL", 1, 1) format for ("CATEDRAL", "MONDAY", "10:00 - 11:00")


    # --------------------------------------------------------------------
    # Validate demand and staff files - need total MaxHw ≥ total Demand
    # --------------------------------------------------------------------

    # Note: It is not mandatory that total MinHw ≤ total Demand because overstaffing is permitted and better
    # than understaffing. Therefore, that check is commented out but can be added back in if required.
    # Check if MinHw ≤ Demand ≤ MaxHw for each location
    errors = []

    for u in U:
        total_demand = sum(Demand[(u, d, t)] for d in D for t in T)
        # total_min_hr = staff_df.loc[staff_df["location"] == u, "min_week_hours"].sum() * 4
        total_max_hr = staff_df.loc[staff_df["location"] == u, "max_week_hours"].sum() * 4

        # if total_demand < total_min_hr:
        #     errors.append(
        #         f"{u}: Total demand ({total_demand:.0f}h) is less than "
        #         f"the minimum contracted hours ({total_min_hr:.0f}h)."
        #     )

        if total_demand > total_max_hr:
                    errors.append(
                        f"{u}: Total demand ({total_demand:.0f}h) is greater than "
                        f"the maximum contracted hours ({total_max_hr:.0f}h)."
                    )

        if errors:
            st.error("The staffing and demand files are inconsistent:\n\n"
                     + "\n".join(errors))
            st.stop()


    # --------------------------------------------------------------------
    # Create schedule
    # --------------------------------------------------------------------

    fn = opt_mod.build_and_solve_shift_model
    res = fn(W, D, T, S, MinHw, MaxHw, WeekendOnly, Demand, Rolew, Locw, 
             priority_slots=priority_slots, 
             M=int(M_choice), 
             N=int(N_choice),
             Max_Deviation=float(max_dev), 
             unavailability=unavailability, 
             constraints_flag=constraints_flag)


    # Check for error
    if res.get("status") == "NO FEASIBLE SOLUTION WAS FOUND":
        st.error("NO FEASIBLE SOLUTION WAS FOUND")
        st.stop()

    # Pull in tables from optimizer
    shift_schedule = res.get("shift_schedule", [])
    shift_adult = res.get("shift_adult", [])
    onduty_adult = res.get("onduty_adult", [])
    mgmt_shift = res.get("mgmt_shift", {})
    mgmt_cov = res.get("mgmt_cov", {})

    staffed_lookup = defaultdict(int)
    for uu, ww, dd, s, e in shift_schedule:
        for t in range(s, e + 1):
            staffed_lookup[(uu, dd, t)] += 1

    shift_adult_lookup = defaultdict(int)
    for uu, dd, tt in shift_adult:
        shift_adult_lookup[(uu, dd, tt)] = 1

    onduty_adult_lookup = defaultdict(int)
    for dd, tt in onduty_adult:
        onduty_adult_lookup[(dd, tt)] = 1

    shift_schedule_lookup = defaultdict(int)
    w_list = []
    for u, w, d, s, e in shift_schedule:
        shift_schedule_lookup[(w, d)] = f"{time_map[s].split(' - ')[0]} - {time_map[e].split(' - ')[1]}"
        if w not in w_list:
            w_list.append(w)


    ### Coverage by location ###
    cov = {} 
    for u in U:

        rows_cov = []

        for d in D:
        
            weekday = ((d - 1) % 7) + 1
            week_num = (d - 1) // 7 + 1
            d_name = f"{day_map[weekday]} ({week_num})" # get day mapping to index number d and list week number

            for t in T:

                if u == "MANAGEMENT":
                    staffed = mgmt_cov.get((d, t), 0)
                    shift = "NA" # don't need to meet shift adult requirement for MANAGEMENT location
                else:
                    staffed = staffed_lookup.get((u, d, t), 0)

                shift = shift_adult_lookup.get((u, d, t), 0)
                onduty = onduty_adult_lookup.get((d, t), 0)

                t_name = time_map[t] # get time slot mapping to index number t
                dem = Demand[(u, d, t)]
                
                rows_cov.append({
                    "location": u, "day": d_name, "slot": t_name,
                    "demand": dem,
                    "staffed": staffed,
                    "under": max(0.0, dem - staffed),
                    "over": max(0.0, staffed - dem), 
                    "no shift manager": shift, 
                    "no on-duty manager": onduty
                })
        
        cov[u] = pd.DataFrame(rows_cov)


    ### Weekly hours per worker ###
    hours = []
    for w in W:
        if w not in w_list:
            continue
        h = sum(e - s + 1 for uu, ww, d, s, e in shift_schedule if ww == w)
        hours.append({
            "name": w,
            "total_hours": h,
            "min_week_hours": MinHw[w],
            "max_week_hours": MaxHw[w]
        })

    ### Worker assignments ###
    assigns = [{
        "name": w, 
        "location": u, 
        "day": d, 
        "start_slot": s, 
        "end_slot": e} 
        for (u, w, d, s, e) in shift_schedule]

    # General Manager "shift"
    if res.get("status") == "Optimal" and mgmt_shift: # only if solution is feasible
        for d in D:
            if mgmt_shift[d] is None:
                continue
            assigns.append({
                "name": mgmt_shift[d]["name"],
                "location": mgmt_shift[d]["location"],
                "day": mgmt_shift[d]["day"],
                "start_slot": mgmt_shift[d]["start_slot"],
                "end_slot": mgmt_shift[d]["end_slot"]
            })


    ### All worker schedules ###
    W_u = {} # W_location - workers in location u
    for w in W:
        if w not in w_list:
            continue
        u = Locw[w]
        W_u.setdefault(u, []).append(w)

    all_cov = [] # Coverage by location
    for u in U:
        if u == "MANAGEMENT":
            continue

        for w in W_u[u]:
            row = {
                "Worker": w, 
                "Location": u,
            }

            for d in D:     
                weekday = ((d - 1) % 7) + 1
                week_num = (d - 1) // 7 + 1
                d_name = f"{day_map[weekday]} ({week_num})" # get day mapping to index number d and list week number
                row[d_name] = shift_schedule_lookup.get((w, d), "OFF")
            all_cov.append(row)
        

    # All UI info to return
    return {
        "status": res.get("status", "N/A"),
        "objective": res.get("objective", float("nan")),
        "coverage_df": cov, # dictionary of dfs
        "hours_df": pd.DataFrame(hours),
        "assignments_df": pd.DataFrame(assigns), 
        "all_worker_coverage_df": pd.DataFrame(all_cov)
    }