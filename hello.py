import streamlit as st, time, sys
st.set_page_config(page_title="Hello", layout="wide")
st.write("✅ App reached top of script")
for i in range(3):
    st.write(f"tick {i}")
    print(f"[server log] tick {i}", flush=True)
    time.sleep(0.5)
st.success("Done!")
