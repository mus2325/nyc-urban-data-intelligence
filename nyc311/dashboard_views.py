"""Streamlit views and chart styling for the NYC 311 dashboard."""

import altair as alt
import pandas as pd
import streamlit as st

from nyc311.analytics import compare_weeks, daily_totals, freshness, rankings, select_records
from nyc311.dashboard_data import DashboardData

TEAL = "#55E6D1"
BLUE = "#66A9FF"
MUTED = "#A6B7CC"
KNOWN_BOROUGHS = {"BRONX", "BROOKLYN", "MANHATTAN", "QUEENS", "STATEN ISLAND"}
QUALITY_METRICS = [
    ("fetched", "Fetched"),
    ("accepted", "Accepted"),
    ("rejected", "Rejected"),
    ("new", "New"),
    ("updated", "Updated"),
    ("unchanged", "Unchanged"),
]


def stamp(value: pd.Timestamp | None) -> str:
    return value.strftime("%b %d, %Y · %H:%M UTC") if value is not None else "Unavailable"


def date_span(records: pd.DataFrame) -> str:
    dates = records["created_date"].dropna()
    if dates.empty:
        return "Unavailable"
    start, end = dates.min().date(), dates.max().date()
    return str(start) if start == end else f"{start} to {end}"


def header(now: pd.Timestamp, data: DashboardData | None = None) -> None:
    latest = data.source_latest if data else None
    status = freshness(latest, now)
    tone = {"Recent": "recent", "Stale": "caution", "Check clock": "caution"}.get(status, "neutral")
    st.html(f"""
        <div class="nyc-hero">
          <div class="nyc-hero-copy">
            <p class="nyc-eyebrow">NYC OPEN DATA / 311</p>
            <h1>NYC Urban Data Intelligence</h1>
            <p>Explore the service requests collected in your local database.</p>
          </div>
          <div class="nyc-source">
            <span class="nyc-status {tone}">{status}</span>
            <strong>Latest stored source update</strong>
            <span>{stamp(latest)}</span>
          </div>
        </div>
    """)


def chart_style(chart: alt.TopLevelMixin) -> alt.TopLevelMixin:
    return (
        chart.configure(background="transparent")
        .configure_view(stroke=None)
        .configure_axis(
            labelColor=MUTED, titleColor="#E7EFFC", gridColor="#27364B",
            domain=False, tickColor="#405470", labelFontSize=12, titleFontSize=12,
            titlePadding=12,
        )
    )


def trend(records: pd.DataFrame, key: str) -> None:
    data = daily_totals(records)
    # This dataset is already aggregated by day. Inline values avoid Altair's
    # default row limit for a collection spanning many years.
    plot_data = data.assign(day=data["day"].dt.strftime("%Y-%m-%d"))
    base = alt.Chart(alt.Data(values=plot_data.to_dict("records"))).encode(
        x=alt.X("day:T", title=None, scale=alt.Scale(type="utc"), axis=alt.Axis(format="%b %d", tickCount=7)),
        y=alt.Y("requests:Q", title="Stored requests", axis=alt.Axis(tickMinStep=1)),
        tooltip=[alt.Tooltip("day:N", title="Created date"),
                 alt.Tooltip("requests:Q", title="Requests", format=",")],
    )
    line = base.mark_line(color=TEAL, strokeWidth=2).encode(detail="segment:N")
    points = base.mark_circle(color=TEAL, opacity=0.95, size=52)
    st.altair_chart(chart_style((line + points).properties(height=230)), key=key, theme=None)
    with st.expander("Daily counts table"):
        st.dataframe(data[["day", "requests"]], hide_index=True)


def comparison(records: pd.DataFrame, field: str, label: str, color: str, key: str) -> None:
    data = rankings(records, field).head(8)
    chart = alt.Chart(data).mark_bar(color=color, cornerRadiusEnd=4).encode(
        x=alt.X("requests:Q", title="Stored requests", axis=alt.Axis(tickMinStep=1)),
        y=alt.Y("label:N", title=None, sort=data["label"].tolist(),
                axis=alt.Axis(labelLimit=155)),
        tooltip=[alt.Tooltip("label:N", title=label),
                 alt.Tooltip("requests:Q", title="Requests", format=","),
                 alt.Tooltip("share:Q", title="Share of selection", format=".1%")],
    ).properties(height=225)
    st.altair_chart(chart_style(chart), key=key, theme=None)
    with st.expander(f"{label} counts table"):
        st.dataframe(data, hide_index=True, column_config={
            "label": label, "requests": "Requests",
            "share": st.column_config.NumberColumn("Share", format="percent"),
        })


def charts(records: pd.DataFrame, section: str) -> None:
    with st.container(border=True):
        st.subheader("Request activity")
        st.caption("By NYC creation date. Gaps indicate dates without stored requests in this selection.")
        trend(records, f"{section}-trend")
    left, right = st.columns(2, gap="medium")
    with left, st.container(border=True):
        st.subheader("Top complaint categories")
        st.caption("Up to eight categories ranked by stored requests.")
        comparison(records, "complaint_type", "Category", TEAL, f"{section}-categories")
    with right, st.container(border=True):
        st.subheader("Borough comparison")
        st.caption("Unknown covers requests without a recognized borough.")
        comparison(records, "borough", "Borough", BLUE, f"{section}-boroughs")


def overview(data: DashboardData) -> None:
    records = data.records
    with st.container(key="overview-kpis"):
        columns = st.columns(4, gap="medium")
        columns[0].metric("Collected requests", f"{len(records):,}", border=True)
        columns[1].metric("Complaint categories", str(records["complaint_type"].nunique()), border=True)
        borough_count = records["borough"].isin(KNOWN_BOROUGHS)
        columns[2].metric("Boroughs represented", f"{records.loc[borough_count, 'borough'].nunique()} of 5",
                          border=True)
        columns[3].metric("Creation dates observed", str(records["created_date"].dt.normalize().nunique()),
                          border=True)
    st.caption(f"Stored creation-date range · {date_span(records)} (NYC local time)")
    charts(records, "overview")


def explorer(records: pd.DataFrame) -> None:
    dates = records["created_date"].dropna()
    if dates.empty:
        st.info("No valid creation dates are available for filtering.")
        return
    with st.container(border=True):
        left, middle, right = st.columns([1, 1, 1.3], gap="medium")
        first, last = dates.min().date(), dates.max().date()
        selected_days = left.date_input("Created date", value=(first, last),
                                        min_value=first, max_value=last)
        boroughs = middle.multiselect("Boroughs", sorted(records["borough"].dropna().unique()),
                                     placeholder="All boroughs")
        categories = right.multiselect("Complaint categories",
                                       sorted(records["complaint_type"].dropna().unique()),
                                       placeholder="All categories")
    if len(selected_days) != 2:
        st.info("Choose an end date to apply the date filter.")
        return
    visible = select_records(records, selected_days, boroughs, categories)
    st.metric("Requests in selection", f"{len(visible):,}", border=True)
    if visible.empty:
        st.info("No stored requests match these filters. Broaden the date range or clear a filter.")
        return
    charts(visible, "explorer")
    with st.container(border=True):
        st.subheader("Request records")
        st.caption(f"Showing the latest {min(len(visible), 250):,} of {len(visible):,} matching records.")
        table = visible.sort_values(["created_date", "unique_key"], ascending=[False, True]).head(250)
        st.dataframe(table, hide_index=True, column_config={
            "unique_key": "Request ID", "created_date": "Created (NYC local)",
            "complaint_type": "Complaint category", "borough": "Borough",
        })


def pipeline(data: DashboardData, now: pd.Timestamp) -> None:
    st.subheader("Ingestion status")
    if data.last_success is None:
        st.info("No successful V2 ingestion has been recorded. Existing V1 records can still be explored.")
    status = freshness(data.last_success, now)
    if status == "Stale":
        st.warning("The last successful ingestion was over 24 hours ago.")
    elif status == "Check clock":
        st.warning("The recorded run time is ahead of the current clock.")
    source_status = freshness(data.source_latest, now)
    if source_status == "Stale":
        st.warning("The latest stored source update was over 24 hours ago.")
    elif source_status == "Check clock":
        st.warning("The stored source timestamp is ahead of the current clock.")
    with st.container(border=True):
        left, right = st.columns(2)
        left.markdown("**Last successful run**")
        left.write(stamp(data.last_success))
        right.markdown("**Checkpoint · scanned through**")
        right.write(stamp(data.watermark))
        st.caption("V2 stores the scan end. The start of the last scan is unavailable in SQLite. "
                   "A recent run can scan an older or empty window.")
    st.subheader("Data quality · last successful run")
    if data.counts is None:
        st.info("No committed quality counts are available.")
        return
    for start in (0, 3):
        columns = st.columns(3, gap="medium")
        for column, (key, title) in zip(columns, QUALITY_METRICS[start:start + 3]):
            count = data.counts.get(key)
            column.metric(title, f"{count:,}" if count is not None else "Unavailable", border=True)
    st.caption("Rejected includes invalid rows and duplicate versions discarded within the scan.")


def insights(records: pd.DataFrame) -> None:
    st.subheader("Patterns in the stored sample")
    left, right = st.columns(2, gap="medium")
    for column, field, title in (
        (left, "complaint_type", "Most recorded complaint"),
        (right, "borough", "Most recorded borough"),
    ):
        with column, st.container(border=True):
            st.markdown(f"**{title}**")
            data = records if field == "complaint_type" else records[records["borough"].isin(KNOWN_BOROUGHS)]
            ranked = rankings(data, field)
            if ranked.empty:
                st.info("No values are available for this comparison.")
                continue
            leader = ranked.iloc[0]
            tied = int(ranked["requests"].eq(leader["requests"]).sum())
            st.write(leader["label"] if tied == 1 else f"{tied} tied for the lead")
            scope = "records" if field == "complaint_type" else "records with a known borough"
            st.caption(f"{leader['requests']:,} {scope} per leader · {leader['share']:.1%} of {len(data):,}.")
    with st.container(border=True):
        st.subheader("Seven-day comparison")
        result = compare_weeks(daily_totals(records))
        if result is None:
            st.info("A comparison needs two consecutive seven-day periods with records every day, "
                    "at least 20 requests per period, and a covered date outside each edge.")
        else:
            st.metric("Latest seven days vs. prior seven", f"{result.latest:,}",
                      delta=f"{result.change:+.1%}", delta_color="off")
            st.caption(f"Prior period: {result.previous:,} · Combined dates: "
                       f"{result.start:%b %d, %Y} to {result.end:%b %d, %Y} (NYC local time).")
        st.caption("This comparison describes collected counts. Daily observations do not prove "
                   "full collection coverage, and no statistical significance test is applied.")
