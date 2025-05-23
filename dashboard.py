# dashboard.py
import streamlit as st
import pandas as pd
import os

# Dummy data for demonstration. In a real app, you'd load from a database.
# For now, let's just create a list of dictionaries.
if 'call_summaries' not in st.session_state:
    st.session_state.call_summaries = []

st.set_page_config(layout="wide", page_title="Phone Chatbot Dashboard")

st.title("📞 Your Smart Phone Chatbot Dashboard")

st.markdown("""
This dashboard provides an overview of calls handled by your virtual receptionist.
""")

# --- Add a placeholder for a new summary (simulate receiving one) ---
st.header("Receive New Summary (Simulation)")
new_summary_text = st.text_area("Paste a new call summary here:", height=150)
if st.button("Add Summary"):
    if new_summary_text:
        st.session_state.call_summaries.append({"timestamp": pd.Timestamp.now(), "summary": new_summary_text})
        st.success("Summary added!")
    else:
        st.warning("Please paste some text into the summary area.")

st.markdown("---")

# --- Display existing summaries ---
st.header("Recent Call Summaries")

if not st.session_state.call_summaries:
    st.info("No call summaries to display yet.")
else:
    # Convert list of dicts to DataFrame for easy display
    df_summaries = pd.DataFrame(st.session_state.call_summaries)
    df_summaries['timestamp'] = df_summaries['timestamp'].dt.strftime('%Y-%m-%d %H:%M:%S')
    df_summaries = df_summaries.sort_values(by="timestamp", ascending=False)
    st.dataframe(df_summaries, hide_index=True, use_container_width=True)

st.markdown("---")
st.caption("Dashboard developed using Streamlit.")
