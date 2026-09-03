# streamlit_app_v2.py - to run locally, in the terminal, run the following command 
# to start the application: streamlit run streamlit_app_v2.py

# ------------------ Imports ------------------

import streamlit as st
import copy
from utils import (
    read_table_file_demand, read_table_file_staff, build_shift_set_fallback,
    create_excel_download, start_solve_job, get_solve_status, get_solve_result
)
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

# ------------------------------------------------------------------------------------- 

# ------------------ Adapter ------------------
def adapt_to_user_optimizer(demand_df, staff_df, max_dev, unavailability, priority_slots,
                             M_choice, N_choice, constraints_flag):

    if opt_mod is None or not hasattr(opt_mod, "build_and_solve_shift_model"):
        return {
            "status": "OPTIMIZER_NOT_FOUND", 
            "errors": ["Optimizer module not found or missing the optimizer model."]
            }

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
        return {"status": "VALIDATION_ERROR", "errors": errors}


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
        return {"status": "NO FEASIBLE SOLUTION WAS FOUND", "errors": []}

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
        "elapsed_time": res.get("elapsed_time", float("nan")),
        "coverage_df": cov, # dictionary of dfs
        "hours_df": pd.DataFrame(hours),
        "assignments_df": pd.DataFrame(assigns), 
        "all_worker_coverage_df": pd.DataFrame(all_cov)
    }

# --------------------------------------------------------------------
# UI
# --------------------------------------------------------------------
st.set_page_config(layout="wide")
st.title("Shift Scheduler")

with st.sidebar:
    
    # Adjust Max Deviation
    st.markdown("##### Max Deviation")
    max_dev = st.number_input(
        label="Max Deviation", 
        value=3,
        key="max_deviation_key_1", 
        label_visibility="collapsed"
        )

    # Adjust Max hours allowed without a shift manager
    st.markdown("##### Max hours Allowed without a Shift Adult")
    M_choice = st.number_input(
        label="Max hours Allowed without a Shift Adult", 
        value=3, 
        key="shift_manager_max_hrs", 
        label_visibility="collapsed")

    # Adjust Max hours allowed without an on-duty manager
    st.markdown("##### Max hours Allowed without an On-Duty Adult")
    N_choice = st.number_input(
        label="Max hours Allowed without an On-Duty Adult", 
        value=1, 
        key="onduty_manager_max_hrs", 
        label_visibility="collapsed")


    # -------- Read in Demand file --------
    st.subheader("Demand")
    st.markdown("##### Upload Demand")
    demand_file = st.file_uploader(
            label="Upload Demand", 
            type=["xlsx","xls"], 
            key="demand_uploader", 
            label_visibility="collapsed"
            )

    if "demand_df" not in st.session_state:
        st.session_state["demand_df"] = DEFAULT_DEMAND.copy()

    if demand_file:
        st.session_state["demand_df"] = read_table_file_demand(demand_file, header=0)
    
    # Separate by location
    each_demand = {}
    for location, df in st.session_state["demand_df"].items():
        st.markdown(f"### {location}")
        each_demand[location] = st.data_editor(
                    df,
                    key=f"demand_editor_{location}", 
                    disabled=True
                )
    st.session_state["demand_df"] = each_demand


    # -------- Read in Staff file --------
    st.subheader("Staff")
    st.markdown("##### Upload Staff")
    staff_file = st.file_uploader(
                label="Upload Staff", 
                type=["xlsx","xls"], 
                key="staff_uploader", 
                label_visibility="collapsed"
                )

    if "staff_df" not in st.session_state:
        st.session_state["staff_df"] = pd.DataFrame(DEFAULT_STAFF)

    if staff_file:
        st.session_state["staff_df"] = read_table_file_staff(staff_file, header=0)

    staff_df = st.data_editor(st.session_state["staff_df"], 
                              num_rows="dynamic", key="staff_editor", disabled=True)
    st.session_state["staff_df"] = staff_df


    # -------- Turn Constraints on/off --------
    st.subheader("Constraints")

    constraints_names = {
            "C1": "Weekly Contract Hours",
            "C2": "Closing Shift Limits",
            "C3": "Minimum Staffing per Slot",
            "C4": "Demand Coverage within Deviation Bounds",
            "C5": "One Shift per Worker per Day",
            "C6": "Late-to-Early Shift Avoidance",
            "C7": "Weekend-only Rule for 15-Hour Workers",
            "C8": "Two Consecutive Rest Days",
            "C9": "Intra-Location Management",
            "C10": "Cross-Location Management",
            "C11": "Priority Shift Management",
            "C12": "Supervisor Shifts",
            "C13": "Rest Weekend per 4 Weeks",
            "C14": "No Friday and Saturday Closing Shifts",
            "C15": "Less Than 10 Consecutive Days Limit",
        }

    constraints_flag = {}

    for key, name in constraints_names.items(): # adjust range if have different # constraints
        constraints_flag[key] = st.checkbox(
            f"{key}: {name}",
            value=True
        )


    # -------- Unavailability --------
    st.subheader("Unavailability")
    if "unavailability" not in st.session_state:
        st.session_state["unavailability"] = {}

    st.markdown("##### Worker")
    if not staff_df.empty:
        w = st.selectbox(
                    label="Worker", 
                    options=staff_df["name"].astype(str).tolist(), 
                    key="ua_worker_select", 
                    label_visibility="collapsed")
        st.session_state["unavailability"].setdefault(w, {"days": set(), "slots": set()})
        u = st.session_state["unavailability"][w]

        st.markdown("##### Unavailable Days")
        u_days = st.multiselect(
                    label="Unavailable Days",
                    options=list(range(1, 8)),
                    default=sorted(list(u["days"])),
                    format_func=lambda d: DAY_LABELS[d-1],
                    key=f"ua_days_{w}", 
                    label_visibility="collapsed"
                )
        u["days"] = set(u_days)

        st.markdown("##### Unavailable Time Slots")
        for d in range(1, 8):
            with st.expander(DAY_LABELS[d-1]):
                cur = sorted([t for (dd, t) in u["slots"] if dd == d])
                sel = st.multiselect(
                                    label="Unavailability Slots",
                                    options=list(range(1, 16)),
                                    default=cur,
                                    format_func=lambda t: SLOT_LABELS[t-1],
                                    key=f"ua_slots_{w}_{d}", 
                                    label_visibility="collapsed"
                                )
                u["slots"] = {x for x in u["slots"] if x[0] != d}
                u["slots"].update({(d, t) for t in sel})


    # ------- Priority Slots --------
    demand_df = st.session_state["demand_df"]

    st.subheader("Priority Slots")

    if demand_file is not None: # clears out priority_slots selection if different demand file loaded
        if st.session_state.get("last_demand_file") != demand_file.name:
            st.session_state["priority_slots"] = {}
            st.session_state["last_demand_file"] = demand_file.name
        st.session_state["demand_df"] = read_table_file_demand(demand_file, header=0)

    if "priority_slots" not in st.session_state:
        st.session_state["priority_slots"] = {}

    st.markdown("##### Location")
    loc_select = st.selectbox(
            label="Location", 
            options=list(demand_df.keys()), 
            key="priority_loc_select", 
            label_visibility="collapsed"
            )
    st.session_state["priority_slots"].setdefault(loc_select, set())

    st.markdown("##### Priority Time Slots")
    for d in range(1,8): # Only show 1 week in the UI instead of all 28 days (D)
        with st.expander(DAY_LABELS[d - 1]):
            cur = sorted({ # current selections for a given weekday across all weeks
                t for (dd, t) in st.session_state["priority_slots"][loc_select] 
                if ((dd - 1) % 7) + 1 == d
                })
            
            widget_key = f"priority_{loc_select}_{d}"

            if widget_key not in st.session_state: # initialize widget memory if it doesn't already exist
                st.session_state[widget_key] = cur
            
            sel = st.multiselect(
                            "Priority Slots", 
                            options=list(range(1, 19)), 
                            default=cur, 
                            format_func = lambda t: SLOT_LABELS[t - 1], 
                            key=f"priority_{loc_select}_{d}", 
                            label_visibility="collapsed"
                        )

            # clear old selection
            st.session_state["priority_slots"][loc_select] = {
                (dd, t)
                for (dd, t) in st.session_state["priority_slots"][loc_select]
                if ((dd - 1) % 7) + 1 != d
            }

            # Add selected slots directly for all 4 weeks
            st.session_state["priority_slots"][loc_select].update(
                {
                    (d + 7 * week, t)
                    for week in range(4)
                    for t in sel
                }
            )


# --------------------------------------------------------------------
# Demand load
# --------------------------------------------------------------------
if demand_file:
    raw = read_table_file_demand(demand_file, header=0)

    # split by location
    demand = {}
    expected_shapes = {}

    for loc, df in raw.items():

        # Number of time slots (rows)
        n_time = len(df)

        # Number of days (columns)
        n_days = len(df.columns) - 1

        # One week: # time slots x # days
        core = df.iloc[:, 1:]

        # Convert to 7 days x 18 time slots
        one_week = core.T.reset_index(drop=True)

        # Repeat to 28 days (4 weeks)
        four_weeks = pd.concat([one_week] * 4, ignore_index=True)

        demand[loc] = four_weeks
        expected_shapes[loc] = (one_week.shape[0] * 4, one_week.shape[1]) # days, time slots
        
        # Validate shape of input file for each location
        if df.empty: # if demand sheet is missing
            st.error(f"{loc}: Demand sheet is empty.")
            st.stop()
        
        if four_weeks.shape != expected_shapes[loc]:
            expected_rows, expected_cols = expected_shapes[loc]
            st.error(
                f"Demand for {loc} must resolve to {expected_cols} time slots x {expected_rows} days. "
                f"Got: {four_weeks.shape}"
            )
            st.stop()

else:
    demand = {"DEFAULT": pd.DataFrame(np.zeros((28, 18), dtype=float))}


# ------------------ Solve ------------------ 
current_status = get_solve_status()

col1, col2 = st.columns([1, 1])

with col1: # Solve button
    solve_clicked = st.button(
        "Solve",
        key="solve_button",
        disabled=(current_status == "running"),
        help="Disabled while a solve is already running." if current_status == "running" else None,
    )

with col2: # Check Results button
    check_clicked = st.button("Check Results", key="check_results_button")

if solve_clicked:
    if opt_mod is None or not hasattr(opt_mod, "build_and_solve_shift_model"):
        st.error("Optimizer model not found.")
        if opt_import_error:
            st.caption(f"Import error: {opt_import_error}")
        st.stop()


    # Start running the solve job, runs outside of Streamlit UI and set to the disk
        # Note: Taking deepcopy of the UI sidebar's inputs/selections in case they get
        # adjusted after the "Solve" button is clicked.
    start_solve_job(
        adapt_to_user_optimizer, # job_fn
        copy.deepcopy(st.session_state["demand_df"]), # demand_df
        copy.deepcopy(st.session_state["staff_df"]), # staff_df
        float(max_dev), # max_dev
        copy.deepcopy(st.session_state["unavailability"]), # unavailability
        copy.deepcopy(st.session_state["priority_slots"]), # priority_slots
        int(M_choice), # M_choice
        int(N_choice), # N_choice
        copy.deepcopy(constraints_flag), # constraints_flag
    )
    st.success("The optimizer has started solving in the background. It'll keep running "
               "even if you close this tab - come back anytime and click 'Check Results'.")
    st.stop()


# ------------------ Results (when "Check Results" is clicked) ------------------ 

if check_clicked:
    status = get_solve_status() # Get the status of the optimizer (i.e., idle, running, done, error)

    # Text to display for different solve job statuses
    if status == "idle":
        st.info("No solve has been run yet. Click 'Solve' to start one.")
    elif status == "running":
        st.info("Still running. This can take a while for larger inputs - check back later.")
    elif status == "error":
        err_res = get_solve_result()
        st.error("The last solve failed with an error.")
        if err_res:
            for e in err_res.get("errors", []):
                st.caption(e)
    elif status == "done":
        res = get_solve_result()

        if res.get("status") in ("VALIDATION_ERROR", "OPTIMIZER_NOT_FOUND"): # Error from optimizer model
            st.error("The staffing and demand files are inconsistent, or the optimizer couldn't be reached:")
            for e in res.get("errors", []):
                st.write(f"- {e}")
        elif res.get("status") == "NO FEASIBLE SOLUTION WAS FOUND": # Optimizer ran fully, no feasible solution
            st.error("NO FEASIBLE SOLUTION WAS FOUND")
        else: # If successful, outputs the status (i.e., Optimal) and the objective value
            solve_status = res.get("status", "N/A")
            obj = res.get("objective", None)
            timing = res.get("elapsed_time", None)
            obj_str = "N/A" if obj is None else f"{float(obj):.4f}"
            st.success(f"Status: {solve_status} | Objective: {obj_str} | Time: {timing:.2f} seconds")

            # --------------------------------------------------------------------
            # Output Tables
            # --------------------------------------------------------------------

            ### Weekly hours per worker table
            # Display table
            st.write("#### Weekly Hours per Worker")
            hours_df = res["hours_df"]
            st.dataframe(res["hours_df"], width='stretch') # display in UI

            # Download button
            hours_excel = create_excel_download({
                "Weekly Hours": hours_df
            })

            st.download_button(
                label="Download Weekly Hours",
                data=hours_excel,
                file_name="weekly_hours.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="download_weekly_hours",
                on_click="ignore"
            )


            ### Per Location Coverage tables
            # Display table
            st.write("#### Coverage")
            coverage = res["coverage_df"]
            for loc, df in coverage.items():
                st.write(f"##### {loc}")
                st.dataframe(df, width="stretch")

            # Download button
            coverage_tables = { # to do in 1 Excel file
                str(loc): df
                for loc, df in coverage.items()
            }

            coverage_excel = create_excel_download(coverage_tables)

            st.download_button(
                label="Download All Coverage Tables",
                data=coverage_excel,
                file_name="coverage.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="download_coverage",
                on_click="ignore"
            )


            ### Per Worker Schedule
            # Display table
            st.write("#### Per-Worker Schedule (hourly over 4 weeks)")

            assignments_df = res.get("assignments_df", pd.DataFrame(columns=["name","day","start_slot","end_slot"]))

            worker_schedule_tables = {}
            if assignments_df.empty:
                st.write("No assignments have been made.")
            else:
                for w in assignments_df["name"].astype(str).unique().tolist():
                    mat = np.zeros((28, 18), dtype=int)
                    sub = assignments_df[assignments_df["name"] == w]
                    for _, r in sub.iterrows(): 
                        day = int(r["day"]) - 1
                        start = int(r["start_slot"])
                        end = int(r["end_slot"])
                        for t in range(start, end + 1):
                            mat[day, t - 1] = 1
                    worker_df = pd.DataFrame(
                                    mat,
                                    columns=SLOT_LABELS,
                                    index=DAY_LABELS_FULL
                                )

                    # Display table
                    with st.expander(w):
                        st.dataframe(worker_df, width='stretch')

                    # Store for Excel download
                    worker_schedule_tables[w] = worker_df

            # Download button
            if worker_schedule_tables:
                worker_excel = create_excel_download(worker_schedule_tables)

                st.download_button(
                    label="Download All Per-Worker Schedules",
                    data=worker_excel,
                    file_name="per_worker_schedules.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key="download_worker_schedules",
                    on_click="ignore"
                )

            ### All Worker Schedule (Worker, Location, Assignments)
            # Display table
            st.write("#### All Worker Schedule (Worker, Location, Assignments)")

            all_worker_coverage_df = res.get("all_worker_coverage_df", pd.DataFrame())

            if all_worker_coverage_df.empty:
                st.write("No works have been assigned.")
            else:
                st.dataframe(all_worker_coverage_df, width="stretch")

                # Download button
                all_workers_excel = create_excel_download({
                            "All Worker Schedules": all_worker_coverage_df
                        })

                st.download_button(
                    label="Download All Worker Schedule",
                    data=all_workers_excel,
                    file_name="all_worker_schedules.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key="download_all_worker_schedules",
                    on_click="ignore"
                )
