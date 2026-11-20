"""Read-only Streamlit dashboard for locally collected NYC 311 data."""

from pathlib import Path
import sqlite3

import pandas as pd
import streamlit as st

from nyc311.dashboard_data import load_dashboard
from nyc311.dashboard_views import explorer, header, insights, overview, pipeline
from nyc311.storage import DEFAULT_DB

st.set_page_config(page_title="NYC Urban Data Intelligence", page_icon="🗽", layout="wide")
st.html(Path(__file__).with_name("nyc311") / "dashboard.css")

now = pd.Timestamp.now(tz="UTC")
if not DEFAULT_DB.is_file():
    header(now)
    st.info("Load your first sample with `python -m nyc311.etl`, then reload this page.")
    st.stop()

try:
    with st.spinner("Reading the local NYC 311 database…", show_time=True):
        data = load_dashboard(DEFAULT_DB)
except (sqlite3.Error, pd.errors.DatabaseError, OSError, ValueError):
    header(now)
    st.error("Could not read the local NYC 311 database. Check that it is a readable SQLite "
             "file with a requests table, then reload the page.")
    st.stop()

header(now, data)
st.caption("This local collection is a sample and may not represent all NYC service requests. "
           "Source: [NYC Open Data · 311 Service Requests](https://data.cityofnewyork.us/d/erm2-nwe9).")
section = st.radio(
    "Dashboard section",
    ["Executive overview", "Request explorer", "Pipeline health", "Insights"],
    horizontal=True,
    key="dashboard-section",
)

if section == "Pipeline health":
    pipeline(data, now)
elif data.records.empty:
    st.info("The database is empty. Run `python -m nyc311.etl` to collect requests.")
elif section == "Executive overview":
    overview(data)
elif section == "Request explorer":
    explorer(data.records)
else:
    insights(data.records)
