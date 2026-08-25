# utils.py - contains helper functions used in the optimizer and streamlit py files:

import os
import pickle
import threading
import time
import uuid
import pandas as pd
from io import BytesIO

# ------------------ Background job store ------------------
# A job's status/result is written to disk (not just st.session_state), so that:
#   - a browser disconnect/reconnect mid-solve doesn't lose anything
#   - closing the tab and coming back hours later still finds the result
#   - a completely different browser session/tab can look up the same job by ID
# This survives as long as the underlying app process/container stays alive,
# which is the same durability the background thread itself depends on.

JOB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "job_store")
os.makedirs(JOB_DIR, exist_ok=True)

_job_lock = threading.Lock()


def _job_paths(job_id):
    base = os.path.join(JOB_DIR, job_id)
    return base + ".status", base + ".result"


def start_background_job(job_fn, *args, **kwargs):
    """
    Runs job_fn(*args, **kwargs) in a background thread and returns a job_id
    immediately (non-blocking). job_fn must NOT call any Streamlit (st.*)
    commands - it runs outside any Streamlit script context.

    Status/result are persisted to disk under JOB_DIR, keyed by job_id.
    """
    job_id = time.strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:8]
    status_path, result_path = _job_paths(job_id)

    with _job_lock:
        with open(status_path, "w") as f:
            f.write("running")

    def _runner():
        try:
            result = job_fn(*args, **kwargs)
            with open(result_path, "wb") as f:
                pickle.dump(result, f)
            with _job_lock:
                with open(status_path, "w") as f:
                    f.write("done")
        except Exception as e:
            with open(result_path, "wb") as f:
                pickle.dump({"status": "ERROR", "errors": [str(e)]}, f)
            with _job_lock:
                with open(status_path, "w") as f:
                    f.write("error")

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    return job_id


def get_job_status(job_id):
    """Returns 'running' | 'done' | 'error' | 'unknown'."""
    status_path, _ = _job_paths(job_id)
    if not os.path.exists(status_path):
        return "unknown"
    with open(status_path) as f:
        return f.read().strip()


def get_job_result(job_id):
    """Returns the unpickled result dict, or None if not yet available."""
    _, result_path = _job_paths(job_id)
    if not os.path.exists(result_path):
        return None
    with open(result_path, "rb") as f:
        return pickle.load(f)


def list_jobs(limit=20):
    """Returns [(job_id, status), ...] newest first, for browsing without needing session_state."""
    job_ids = sorted(
        {fname.rsplit(".", 1)[0] for fname in os.listdir(JOB_DIR)},
        reverse=True
    )[:limit]
    return [(job_id, get_job_status(job_id)) for job_id in job_ids]


# ------------------ Helpers ------------------
def read_table_file(uploaded_file, header=0): # original model: reading the files
    name = uploaded_file.name.lower()
    if name.endswith(".csv"):
        return pd.read_csv(uploaded_file, header=header)
    if name.endswith((".xlsx", ".xls")):
        return pd.read_excel(uploaded_file, header=header)  # requires openpyxl for xlsx
    raise ValueError("Unsupported file type")

def read_table_file_demand(uploaded_file, header=0): # read in the demand file
    name = uploaded_file.name.lower()
    if name.endswith((".xlsx", ".xls")):
        x_file = pd.ExcelFile(uploaded_file)
        all_data = {}

        for location in x_file.sheet_names:
            df = pd.read_excel(x_file, header=header, sheet_name=location)

            all_data[location] = df

        return all_data
    
    raise ValueError("Unsupported file type")

def read_table_file_staff(uploaded_file, header=0): # read in the demand file
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


def build_shift_set_fallback(T, min_len=4, max_len=8): # creating feasible shifts
    return [(s, e) for s in T for e in T if min_len <= e - s + 1 <= max_len]

def week_of(d): # returns corresponding k; e.g., week_of(13) returns 2
    return (d - 1) // 7 + 1

def weekday_of(d): # returns corresponding number as 1=Monday,...,7=Sunday; e.g., weekday_of(13) returns 6
    return ((d - 1) % 7) + 1


def create_excel_download(tables):  
    """
    tables: dictionary of {sheet_name: dataframe}
    Returns an Excel file as bytes.
    """
    output = BytesIO()

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        for sheet_name, df in tables.items():
            # Excel sheet names cannot exceed 31 characters
            sheet_name = str(sheet_name)[:31]

            # Remove characters that Excel doesn't allow in sheet names
            for char in ["\\", "/", "*", "[", "]", ":", "?"]:
                sheet_name = sheet_name.replace(char, "_")

            df.to_excel(writer, sheet_name=sheet_name, index=True)

    return output.getvalue()