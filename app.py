"""Streamlit dashboard for the locally stored NYC 311 sample."""

import sqlite3

import pandas as pd
import streamlit as st

from nyc311.storage import DEFAULT_DB, load_requests

st.set_page_config(page_title="NYC 311 Explorer", page_icon="🗽", layout="wide")
st.title("NYC 311 Explorer")
st.caption("Explore the locally collected sample of NYC service requests.")

if not DEFAULT_DB.is_file():
    st.info("Load your first sample with `python -m nyc311.etl`, then reload this page.")
    st.stop()

try:
    records = load_requests(DEFAULT_DB)
except (sqlite3.Error, pd.errors.DatabaseError) as error:
    st.error(f"Could not read the local NYC 311 database. {error}")
    st.stop()

if records.empty:
    st.info("The database is empty. Run `python -m nyc311.etl` to load a sample.")
    st.stop()

records["day"] = records["created_date"].dt.normalize()
first_day = records["day"].min().date()
last_day = records["day"].max().date()

with st.sidebar:
    st.header("Filters")
    selected_days = st.date_input(
        "Created date", value=(first_day, last_day), min_value=first_day, max_value=last_day
    )
    boroughs = st.multiselect("Boroughs", sorted(records["borough"].unique()),
                             placeholder="All boroughs")
    categories = st.multiselect("Complaint categories", sorted(records["complaint_type"].unique()),
                                placeholder="All categories")

if len(selected_days) != 2:
    st.info("Choose an end date to apply the date filter.")
    st.stop()

start, end = map(pd.Timestamp, selected_days)
visible = records.loc[records["day"].between(start, end)]
if boroughs:
    visible = visible.loc[visible["borough"].isin(boroughs)]
if categories:
    visible = visible.loc[visible["complaint_type"].isin(categories)]

st.metric("Requests in selection", f"{len(visible):,}")
if visible.empty:
    st.info("No stored requests match these filters.")
    st.stop()

left, right = st.columns(2)
with left:
    st.subheader("Top 10 complaint categories")
    top_categories = visible["complaint_type"].value_counts().head(10).reset_index(name="Requests")
    st.bar_chart(
        top_categories,
        x="complaint_type",
        y="Requests",
        x_label="Complaint category",
        horizontal=True,
        sort="-Requests",
        color="#147D80",
    )
with right:
    st.subheader("Requests by borough")
    by_borough = visible["borough"].value_counts().reset_index(name="Requests")
    st.bar_chart(by_borough, x="borough", y="Requests", x_label="Borough",
                 sort="-Requests", color="#305B9A")

st.subheader("Daily requests in the sample")
daily = visible.groupby("day").size().rename("Requests")
st.line_chart(daily, color="#305B9A")
st.caption("Dates with no matching stored records are omitted. Counts describe this sample.")
