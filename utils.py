# utils.py - contains helper functions used in the optimizer and streamlit py files:

import pandas as pd
from io import BytesIO

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