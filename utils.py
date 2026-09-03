# utils.py - contains helper functions used in the optimizer and streamlit py files
# and functions to solve the optimizer model in the background of the Streamlit UI 
# and be able to retrieve results later:

import os
import pickle
import threading
import pandas as pd
from io import BytesIO

# --------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------

# Model v1 function to read demand and staff files
def read_table_file(uploaded_file, header=0):
    name = uploaded_file.name.lower()
    if name.endswith(".csv"):
        return pd.read_csv(uploaded_file, header=header)
    if name.endswith((".xlsx", ".xls")):
        return pd.read_excel(uploaded_file, header=header)  # requires openpyxl for xlsx
    raise ValueError("Unsupported file type")


# Read demand file
def read_table_file_demand(uploaded_file, header=0): 
    name = uploaded_file.name.lower()
    if name.endswith((".xlsx", ".xls")):
        x_file = pd.ExcelFile(uploaded_file)
        all_data = {}

        for location in x_file.sheet_names:
            df = pd.read_excel(x_file, header=header, sheet_name=location)

            all_data[location] = df

        return all_data
    
    raise ValueError("Unsupported file type")


# Read staff file
def read_table_file_staff(uploaded_file, header=0):
    name = uploaded_file.name.lower()
    if name.endswith((".xlsx", ".xls")):
        staff_file = pd.read_excel(uploaded_file, header=header, sheet_name=0) # only get 1st sheet
        
        cols = {"name", "min_week_hours", "max_week_hours", "contract_hours", # cols in sheet
                "weekend_only", "location", "role"}
        missing = cols - set(staff_file.columns)
        
        if missing:
            raise ValueError(f"Staff file is missing the required columns: {missing}")
        
        # types:
        staff_file["name"] = staff_file["name"].astype(str)
        staff_file["min_week_hours"] = staff_file["min_week_hours"].astype(int)
        staff_file["max_week_hours"] = staff_file["max_week_hours"].astype(int)
        staff_file["contract_hours"] = staff_file["contract_hours"].astype(int)
        staff_file["weekend_only"] = staff_file["weekend_only"].astype(bool)
        staff_file["location"] = staff_file["location"].astype(str)
        staff_file["role"] = staff_file["role"].astype(str)

        return staff_file

    raise ValueError("Unsupported file type")


# Create feasible shifts
def build_shift_set_fallback(T, min_len=4, max_len=8):
    return [(s, e) for s in T for e in T if min_len <= e - s + 1 <= max_len]


# Returns corresponding "k"; e.g., week_of(13) returns 2
def week_of(d):
    return (d - 1) // 7 + 1


# Returns corresponding number as 1=Monday,...,7=Sunday; e.g., weekday_of(13) returns 6
def weekday_of(d):
    return ((d - 1) % 7) + 1


# Create the tables, return Excel file as bytes
def create_excel_download(tables): 
    output = BytesIO() # Excel file as bytes

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        for sheet_name, df in tables.items():
            sheet_name = str(sheet_name)[:31] # limit excel sheet names to 31 characters

            for char in ["\\", "/", "*", "[", "]", ":", "?"]: # remove characters not allowed in Excel sheet names
                sheet_name = sheet_name.replace(char, "_")

            df.to_excel(writer, sheet_name=sheet_name, index=True)

    return output.getvalue()

# --------------------------------------------------------------------
# Solve Running in Background - Separate from Streamlit UI
# --------------------------------------------------------------------
    # The optimizer can initiate the solve in the Streamlit UI, then runs in the background 
    # and the status and results are written to disk rather than to the streamlit session. 
    # This means that if the browser disconnects/reconnects while solving, it doesn't lose 
    # anything, and closing the tab and returning later can still generate the result. Note 
    # that only one "solve" can happen at a time.


JOB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "job_store")
os.makedirs(JOB_DIR, exist_ok=True)

SOLVE_STATUS_PATH = os.path.join(JOB_DIR, "solve.status")
SOLVE_RESULT_PATH = os.path.join(JOB_DIR, "solve.result")

_solve_lock = threading.Lock()


# Start running the solve job, runs outside of Streamlit UI and set to the disk
def start_solve_job(job_fn, *args, **kwargs):

    with _solve_lock:
        with open(SOLVE_STATUS_PATH, "w") as f:
            f.write("running")
        if os.path.exists(SOLVE_RESULT_PATH):
            os.remove(SOLVE_RESULT_PATH)

    def _runner():
        try:
            result = job_fn(*args, **kwargs)
            with open(SOLVE_RESULT_PATH, "wb") as f:
                pickle.dump(result, f)
            with _solve_lock:
                with open(SOLVE_STATUS_PATH, "w") as f:
                    f.write("done")
        except Exception as e:
            with open(SOLVE_RESULT_PATH, "wb") as f:
                pickle.dump({"status": "ERROR", "errors": [str(e)]}, f)
            with _solve_lock:
                with open(SOLVE_STATUS_PATH, "w") as f:
                    f.write("error")

    t = threading.Thread(target=_runner, daemon=True)
    t.start()


# Get the status of the optimizer (i.e., idle, running, done, error)
def get_solve_status():
    if not os.path.exists(SOLVE_STATUS_PATH):
        return "idle"
    with open(SOLVE_STATUS_PATH) as f:
        return f.read().strip()


# Get the result (i.e., tables) of the optimizer, or say None if it's not yet available
def get_solve_result():
    if not os.path.exists(SOLVE_RESULT_PATH):
        return None
    with open(SOLVE_RESULT_PATH, "rb") as f:
        return pickle.load(f)
