from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from src.config import load_config
from src.storage import InventoryStore

st.set_page_config(
    page_title="Ooty Hostel Inventory Dashboard",
    page_icon="🏨",
    layout="wide",
    initial_sidebar_state="expanded",
)

config = load_config()
store = InventoryStore(config.database_path)


@st.cache_data(ttl=60)
def load_data() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    property_df = store.load_property_inventory()
    room_df = store.load_room_inventory()
    runs_df = store.load_collection_runs()
    return property_df, room_df, runs_df


def empty_state() -> None:
    st.title("Ooty Hostel Inventory Dashboard")
    st.warning("No inventory data yet.")
    st.markdown(
        """
        Run the collector to populate the database:

        ```bash
        cd hostel-parsing
        pip install -r requirements.txt
        python -m src.collector --days 3 --property-id 292129
        python -m src.collector
        streamlit run dashboard/app.py
        ```
        """
    )


def apply_property_filters(df: pd.DataFrame) -> pd.DataFrame:
    filtered = df.copy()
    hostels = st.sidebar.multiselect(
        "Hostels",
        options=sorted(filtered["property_name"].unique()),
        default=sorted(filtered["property_name"].unique()),
    )
    if hostels:
        filtered = filtered[filtered["property_name"].isin(hostels)]

    scrape_dates = sorted(filtered["scrape_date"].dropna().unique())
    if len(scrape_dates) > 1:
        scrape_range = st.sidebar.select_slider(
            "Scrape snapshot range",
            options=scrape_dates,
            value=(scrape_dates[0], scrape_dates[-1]),
        )
        filtered = filtered[
            (filtered["scrape_date"] >= scrape_range[0])
            & (filtered["scrape_date"] <= scrape_range[1])
        ]
    elif len(scrape_dates) == 1:
        st.sidebar.caption(f"Scrape snapshot: {scrape_dates[0].date()}")

    stay_dates = sorted(filtered["stay_date"].dropna().unique())
    if len(stay_dates) > 1:
        stay_range = st.sidebar.select_slider(
            "Stay date range",
            options=stay_dates,
            value=(stay_dates[0], stay_dates[-1]),
        )
        filtered = filtered[
            (filtered["stay_date"] >= stay_range[0]) & (filtered["stay_date"] <= stay_range[1])
        ]
    elif len(stay_dates) == 1:
        st.sidebar.caption(f"Stay date: {stay_dates[0].date()}")

    if st.sidebar.checkbox("Available inventory only", value=False):
        filtered = filtered[filtered["has_availability"] == 1]

    return filtered


def apply_room_filters(df: pd.DataFrame) -> pd.DataFrame:
    filtered = df.copy()
    categories = st.sidebar.multiselect(
        "Room category",
        options=sorted(filtered["room_category"].dropna().unique()),
        default=sorted(filtered["room_category"].dropna().unique()),
    )
    if categories:
        filtered = filtered[filtered["room_category"].isin(categories)]

    room_types = st.sidebar.multiselect(
        "Room basic type",
        options=sorted(filtered["basic_type"].dropna().unique()),
        default=sorted(filtered["basic_type"].dropna().unique()),
    )
    if room_types:
        filtered = filtered[filtered["basic_type"].isin(room_types)]
    return filtered


def render_kpis(df: pd.DataFrame) -> None:
    latest = df.sort_values("scraped_at", ascending=False).drop_duplicates(
        subset=["property_id", "stay_date"]
    )
    sold = int(latest.get("sold_inventory", pd.Series(dtype=int)).sum())
    avail = int(latest.get("available_inventory", pd.Series(dtype=int)).sum())
    total = int(latest.get("total_inventory", pd.Series(dtype=int)).sum())
    occ = f"{(sold / total * 100):.1f}%" if total > 0 else "0%"
    col1, col2, col3, col4, col5, col6 = st.columns(6)
    col1.metric("Hostels tracked", df["property_id"].nunique())
    col2.metric("Stay dates covered", df["stay_date"].nunique())
    col3.metric("Snapshots", df["scraped_at"].nunique())
    col4.metric("Sold beds (latest)", sold)
    col5.metric("Available beds (latest)", avail)
    col6.metric("Occupancy (latest)", occ)


def render_inventory_trend(df: pd.DataFrame) -> None:
    st.subheader("Inventory trend by scrape snapshot")
    trend = (
        df.groupby(["scrape_date", "property_name"], as_index=False)
        .agg(
            total_dorm_beds=("total_dorm_beds", "sum"),
            total_private_rooms=("total_private_rooms", "sum"),
            available_share=("has_availability", "mean"),
        )
        .sort_values("scrape_date")
    )
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    for hostel in trend["property_name"].unique():
        subset = trend[trend["property_name"] == hostel]
        fig.add_trace(
            go.Scatter(
                x=subset["scrape_date"],
                y=subset["total_dorm_beds"],
                mode="lines+markers",
                name=f"{hostel} dorm beds",
            ),
            secondary_y=False,
        )
    fig.update_layout(
        height=420,
        legend=dict(orientation="h"),
        xaxis_title="Scrape date",
        yaxis_title="Total dorm beds available",
    )
    st.plotly_chart(fig, use_container_width=True)


def render_stay_date_heatmap(df: pd.DataFrame) -> None:
    st.subheader("Stay-date inventory heatmap")
    latest_scrape = df["scrape_date"].max()
    heat = df[df["scrape_date"] == latest_scrape].copy()
    if heat.empty:
        st.info("No data for heatmap.")
        return

    if "sold_inventory" in heat.columns:
        heat["inventory_units"] = (
            heat["sold_inventory"].fillna(0) + heat["available_inventory"].fillna(0)
        )
    else:
        heat["inventory_units"] = heat["total_dorm_beds"] + heat["total_private_rooms"]
    pivot = heat.pivot_table(
        index="property_name",
        columns="stay_date",
        values="inventory_units",
        aggfunc="sum",
        fill_value=0,
    )
    fig = px.imshow(
        pivot,
        aspect="auto",
        color_continuous_scale="YlGnBu",
        labels={"x": "Stay date", "y": "Hostel", "color": "Inventory units"},
    )
    fig.update_layout(height=max(320, 40 * len(pivot.index)))
    st.plotly_chart(fig, use_container_width=True)


def render_price_trends(df: pd.DataFrame) -> None:
    st.subheader("Price trends (lowest dorm / private)")
    price_df = df.dropna(subset=["lowest_dorm_price"]).copy()
    if price_df.empty:
        st.info("No price data available for selected filters.")
        return

    selected_hostel = st.selectbox(
        "Hostel for price drill-down",
        options=sorted(price_df["property_name"].unique()),
    )
    subset = price_df[price_df["property_name"] == selected_hostel]
    fig = px.line(
        subset,
        x="stay_date",
        y="lowest_dorm_price",
        color="scrape_date",
        markers=True,
        title=f"Lowest dorm price by stay date — {selected_hostel}",
        labels={"lowest_dorm_price": "Price (INR)", "stay_date": "Stay date"},
    )
    st.plotly_chart(fig, use_container_width=True)


def render_room_breakdown(room_df: pd.DataFrame) -> None:
    st.subheader("Room-level breakdown")
    if room_df.empty:
        st.info("No room-level records for selected filters.")
        return

    latest = room_df.sort_values("scraped_at", ascending=False).drop_duplicates(
        subset=["property_id", "stay_date", "room_id"]
    )
    chart_df = (
        latest.groupby(["property_name", "room_category"], as_index=False)["beds_available"]
        .sum()
        .sort_values("beds_available", ascending=False)
    )
    fig = px.bar(
        chart_df,
        x="property_name",
        y="beds_available",
        color="room_category",
        barmode="stack",
        title="Latest beds available by room category",
    )
    st.plotly_chart(fig, use_container_width=True)

    display_cols = [
        "property_name",
        "stay_date",
        "room_name",
        "room_category",
        "basic_type",
        "beds_available",
        "rooms_available",
        "lowest_price",
        "currency",
        "scrape_date",
    ]
    st.dataframe(
        latest[display_cols].sort_values(["property_name", "stay_date", "room_name"]),
        use_container_width=True,
        hide_index=True,
    )


def render_runs(runs_df: pd.DataFrame) -> None:
    st.subheader("Collection runs")
    if runs_df.empty:
        st.info("No collection runs recorded yet.")
        return
    st.dataframe(runs_df, use_container_width=True, hide_index=True)


def render_live_dashboard() -> None:
    property_df, room_df, runs_df = load_data()
    if property_df.empty:
        empty_state()
        return

    city_names = ", ".join(f"{city.name}, {city.country}" for city in config.cities)
    st.title("Hill Station Hostel Inventory Dashboard")
    st.caption(
        f"Tracking {city_names} · "
        f"{config.collector.horizon_days}-day forward visibility · auto-refreshes every 2 min"
    )

    st.sidebar.header("Filters")
    filtered_properties = apply_property_filters(property_df)

    tab_overview, tab_trends, tab_rooms, tab_runs = st.tabs(
        ["Overview", "Trends", "Room detail", "Collection runs"]
    )

    with tab_overview:
        render_kpis(filtered_properties)
        render_stay_date_heatmap(filtered_properties)

    with tab_trends:
        render_inventory_trend(filtered_properties)
        render_price_trends(filtered_properties)

    with tab_rooms:
        filtered_rooms = room_df.merge(
            filtered_properties[["property_id", "stay_date", "scrape_date"]].drop_duplicates(),
            on=["property_id", "stay_date", "scrape_date"],
            how="inner",
        )
        filtered_rooms = apply_room_filters(filtered_rooms)
        render_room_breakdown(filtered_rooms)

    with tab_runs:
        render_runs(runs_df)


render_live_dashboard = st.fragment(run_every=120)(render_live_dashboard)


def main() -> None:
    render_live_dashboard()


if __name__ == "__main__":
    main()
