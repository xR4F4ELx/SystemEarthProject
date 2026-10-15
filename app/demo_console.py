"""
EUDR compact-store demo console (Streamlit): run the pipeline step by step and see what it does.

Each button calls the real pipeline (scripts/), nothing is mocked. The map draws the 2020 forest and the
disturbance held in the compact store as an image layer; the dark basemap around it needs internet, but
everything else works offline. Progress is saved to data/results/, so a page refresh mid-demo is safe.

Run from the repo root:
    streamlit run app/demo_console.py
"""

import base64
import io
import json
import sys
import time
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pydeck as pdk
import pyarrow.parquet as pq
import rasterio
import streamlit as st
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import baseline as bl  # noqa: E402
import compact  # noqa: E402
import engine  # noqa: E402
from encode_layers import build, publish_events  # noqa: E402
from index_plots import manifest_grid, write_index  # noqa: E402
from layers import PLOTS_DIR, RAW_DIR, plot_footprint  # noqa: E402
from verify_declarations import REASONS, verify  # noqa: E402

HELD_BACK_RADD, HELD_BACK_HANSEN = 26181, 24  # data state before the simulated release (30 Jun 2026, Hansen 2024)
BATCH = "2026-10-07"
BEFORE_CSV = bl.RESULTS_DIR / "compact_verdicts_before_update.csv"
AFTER_CSV = bl.RESULTS_DIR / "compact_verdicts.csv"
STATE_PATH = bl.RESULTS_DIR / "demo_console_state.json"  # survives a browser refresh mid-demo
PERSISTED_KEYS = ("log", "check_stats", "update_stats")
POOL = 4  # map image: grid cells per pixel along each axis
STATUS_RGB = {bl.COMPLIANT: [47, 111, 163], bl.NON_COMPLIANT: [194, 101, 42], bl.NEEDS_REVIEW: [107, 111, 118]}
BACKGROUND_RGBA, FOREST_RGBA = (247, 245, 239, 255), (156, 195, 160, 255)
KNOWN_RGBA, ADDED_RGBA = (75, 42, 107, 255), (224, 161, 0, 255)
DECISION_FIELDS = ["c2_deforestation_spatial_analysis_result", "d1_strict_protected_area_spatial_analysis_result",
                   "d2_restricted_use_area_spatial_analysis_result", "final_status"]


# ---------- data shown on the map (cached; recomputed only when the store changes) ----------

@st.cache_data(show_spinner=False)
def plot_shapes():
    plots = gpd.read_file(PLOTS_DIR / "plots.geojson")
    footprints = [plot_footprint(g, a) for g, a in zip(plots.geometry, plots["Area"])]
    return pd.DataFrame({
        "plot_id": plots["ProductionPlace"],
        "polygon": [[list(xy) for xy in f.exterior.coords] for f in footprints],
        "lon": [f.centroid.x for f in footprints],
        "lat": [f.centroid.y for f in footprints],
    })


def pooled_max(mask):
    h, w = mask.shape[0] // POOL * POOL, mask.shape[1] // POOL * POOL
    return mask[:h, :w].reshape(h // POOL, POOL, w // POOL, POOL).max(axis=(1, 3)), (h, w)


@st.cache_data(show_spinner=False)
def store_image(built_utc, batches):
    """Map image decoded from the compact store itself: forest, known disturbance, disturbance added by events."""
    del built_utc  # cache key only
    grid = manifest_grid(compact.load_manifest())
    with rasterio.open(compact.BASE_PATH) as src:
        forest, first_date, _ = compact.unpack(src.read(1))
    added = np.zeros(forest.size, dtype=bool)
    for batch in batches:
        added[pq.read_table(compact.STORE_DIR / batch, columns=["cell_id"]).column("cell_id").to_numpy()] = True
    added = added.reshape(forest.shape) & (first_date == 0)

    forest_px, (h, w) = pooled_max(forest)
    known_px, _ = pooled_max(first_date != 0)
    added_px, _ = pooled_max(added)
    rgba = np.empty(forest_px.shape + (4,), dtype=np.uint8)
    rgba[:] = BACKGROUND_RGBA
    rgba[forest_px] = FOREST_RGBA
    rgba[known_px] = KNOWN_RGBA
    rgba[added_px] = ADDED_RGBA
    buffer = io.BytesIO()
    Image.fromarray(rgba).save(buffer, format="PNG", optimize=True)
    t = grid.transform
    bounds = [t.c, t.f + h * t.e, t.c + w * t.a, t.f]  # west, south, east, north
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode(), bounds


# ---------- demo progress (kept on disk so a page refresh doesn't lose it) ----------

def save_state():
    state = {k: st.session_state[k] for k in PERSISTED_KEYS if k in st.session_state}
    state.update(built_utc=compact.load_manifest()["built_utc"], has_before="before" in st.session_state,
                 has_after="after" in st.session_state)
    STATE_PATH.write_text(json.dumps(state), encoding="utf-8")


def restore_state(manifest):
    """Reload the progress saved for this build of the store, once per browser session."""
    if st.session_state.get("restored"):
        return
    st.session_state["restored"] = True
    if not STATE_PATH.exists():
        return
    saved = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    if saved.get("built_utc") != manifest["built_utc"]:
        return
    st.session_state.update({k: saved[k] for k in PERSISTED_KEYS if k in saved})
    if saved.get("has_before") and BEFORE_CSV.exists():
        st.session_state["before"] = pd.read_csv(BEFORE_CSV, keep_default_na=False)
    if saved.get("has_after") and AFTER_CSV.exists():
        st.session_state["after"] = pd.read_csv(AFTER_CSV, keep_default_na=False)


# ---------- pipeline steps ----------

def log(message):
    st.session_state.setdefault("log", []).append(message)


def step_build():
    started = time.perf_counter()
    manifest = build(HELD_BACK_RADD, HELD_BACK_HANSEN)
    index = write_index()
    for key in ("before", "after", "verification", "check_stats", "update_stats", "raw_before", "raw_after", "log"):
        st.session_state.pop(key, None)
    base_kb = next(f["bytes"] for f in manifest["files"] if f["path"] == "base.tif") / 1e3
    log(f"Built store on data to 30 Jun 2026: base raster {base_kb:.1f} kB; indexed {index['plots']} plots "
        f"({index['runs']:,} cell runs) in {time.perf_counter() - started:.1f} s")


def step_check():
    verdicts, stats = engine.check()
    verdicts.to_csv(BEFORE_CSV, index=False)
    st.session_state.update(before=verdicts, check_stats=stats)
    log(f"Checked {stats['plots']} plots in {stats['total_s'] * 1000:.0f} ms, reading {stats['tiles_read']} tiles "
        f"({stats['bytes_read'] / 1e3:.0f} kB)")


def step_publish():
    record = publish_events(BATCH)
    log(f"Published release {BATCH}: {record['rows']:,} events, {record['bytes'] / 1e3:.1f} kB appended")


def step_update():
    verdicts, stats = engine.update(BATCH, st.session_state["before"])
    verdicts.to_csv(AFTER_CSV, index=False)
    st.session_state.update(after=verdicts, update_stats=stats)
    changed = (st.session_state["before"].set_index("plot_id")["final_status"]
               != verdicts.set_index("plot_id")["final_status"]).sum()
    log(f"Re-checked {stats['affected_plots']} of {stats['total_plots']} plots in {stats['total_s'] * 1000:.0f} ms; "
        f"{changed} changed final status")


def step_verify():
    declarations = pd.read_csv(PLOTS_DIR / "supplier_declarations.csv", keep_default_na=False)
    result = verify(declarations, current_verdicts())
    st.session_state["verification"] = result
    log(f"Supplier check: {result['matches_declaration'].mean():.1%} agreement; "
        f"{(~result['matches_declaration']).sum()} mismatches explained")


def current_verdicts():
    return st.session_state.get("after", st.session_state.get("before"))


# ---------- page ----------

def sidebar(manifest):
    published = bool(manifest and any(b["batch"] == BATCH for b in manifest["event_batches"]))
    has_before = "before" in st.session_state
    st.sidebar.header("Demo steps")
    steps = [
        ("1. Build store (data to 30 Jun 2026)", step_build, False),
        ("2. Check all plots", step_check, manifest is None or published),
        (f"3. Publish release {BATCH}", step_publish, not has_before or published),
        ("4. Re-check affected plots", step_update, not (published and has_before) or "after" in st.session_state),
        ("5. Verify supplier declarations", step_verify, current_verdicts() is None),
    ]
    for label, action, disabled in steps:
        if st.sidebar.button(label, disabled=disabled, use_container_width=True):
            with st.spinner(label):
                action()
            save_state()
            st.rerun()
    st.sidebar.divider()
    st.sidebar.caption("Step 1 resets the demo. Every button runs the real pipeline in `scripts/`.")
    for message in reversed(st.session_state.get("log", [])):
        st.sidebar.success(message)


def metrics(manifest):
    raw_bytes = sum(p.stat().st_size for p in RAW_DIR.glob("ci_tai_soubre_*"))
    layer_files = {"base.tif", "protected_areas.parquet"}
    store_bytes = sum(f["bytes"] for f in manifest["files"] if f["path"] in layer_files)
    store_bytes += sum(b["bytes"] for b in manifest["event_batches"])
    check, update = st.session_state.get("check_stats"), st.session_state.get("update_stats")
    cols = st.columns(4)
    cols[0].metric("Decision layers", f"{store_bytes / 1e3:.0f} kB", f"{raw_bytes / store_bytes:.0f}x smaller than raw "
                   f"({raw_bytes / 1e6:.2f} MB)", delta_color="off")
    cols[1].metric("Check all plots", f"{check['total_s'] * 1000:.0f} ms" if check else "–",
                   f"{check['bytes_read'] / 1e3:.0f} kB read" if check else None, delta_color="off")
    cols[2].metric("Update re-checks", f"{update['affected_plots']} of {update['total_plots']}" if update else "–",
                   f"{update['total_s'] * 1000:.0f} ms" if update else None, delta_color="off")
    fidelity = fidelity_vs_raw()
    cols[3].metric("Matches raw layers", f"{fidelity['decision']:.0%}" if fidelity else "–",
                   f"{fidelity['identical']} of {fidelity['plots']} plots identical on every field" if fidelity else None,
                   delta_color="off")


def verdict_table():
    verdicts = current_verdicts()
    if verdicts is None:
        return None
    table = verdicts.copy()
    before = st.session_state.get("before")
    table["changed"] = False
    if "after" in st.session_state:
        previous = before.set_index("plot_id")["final_status"]
        table["status_before"] = table["plot_id"].map(previous)
        table["changed"] = table["status_before"] != table["final_status"]
    return table


def to_hex(rgb):
    return "#" + "".join(f"{v:02x}" for v in rgb[:3])


CHANGED_RGB = [23, 51, 43]
LEGEND = [  # (shape, colour, label): squares are map areas, dots are plots, the ring marks a changed plot
    ("square", FOREST_RGBA, "Forest on 31 Dec 2020"),
    ("square", KNOWN_RGBA, "Disturbed after the cut-off"),
    ("square", ADDED_RGBA, "Disturbed: added by the release"),
    ("dot", STATUS_RGB[bl.COMPLIANT], "Compliant plot"),
    ("dot", STATUS_RGB[bl.NON_COMPLIANT], "Non-compliant plot"),
    ("dot", STATUS_RGB[bl.NEEDS_REVIEW], "Needs-review plot"),
    ("ring", CHANGED_RGB, "Status changed by the release"),
]


def legend():
    """Colour key on the map's own light background, so swatches look exactly as they do on the map."""
    shapes = {"square": "border-radius:3px;background:{c}", "dot": "border-radius:50%;background:{c}",
              "ring": "border-radius:50%;border:2px solid {c};box-sizing:border-box"}
    chips = "".join(
        f'<span style="display:inline-flex;align-items:center;gap:6px;background:#F7F5EF;color:#17332B;'
        f'padding:4px 10px;border-radius:12px;font-size:14px">'
        f'<span style="display:inline-block;width:14px;height:14px;{shapes[shape].format(c=to_hex(colour))}"></span>'
        f"{label}</span>"
        for shape, colour, label in LEGEND)
    st.markdown(f'<div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:8px">{chips}</div>',
                unsafe_allow_html=True)


def draw_map(manifest, table):
    legend()
    image, bounds = store_image(manifest["built_utc"], tuple(b["path"] for b in manifest["event_batches"]))
    # pdk.types.String keeps the data URI literal; a plain string would be parsed as an expression
    layers = [pdk.Layer("BitmapLayer", image=pdk.types.String(image), bounds=bounds)]
    shapes = plot_shapes()
    if table is not None:
        data = shapes.merge(table, on="plot_id")
        data["fill"] = data["final_status"].map(STATUS_RGB)
        data["tip"] = data.apply(lambda r: f"{r.forest_2020_ha:.2f} ha forest 2020 · {r.disturbed_after_cutoff_ha:.2f} ha "
                                           f"disturbed · first {r.first_disturbance_date or '–'} "
                                           f"{r.disturbance_sources}", axis=1)
        layers += [
            pdk.Layer("PolygonLayer", data=data, get_polygon="polygon", get_fill_color="fill", get_line_color=[23, 51, 43],
                      line_width_min_pixels=1, opacity=0.55, pickable=True),
            pdk.Layer("ScatterplotLayer", data=data, get_position=["lon", "lat"], get_fill_color="fill",
                      get_radius=60, radius_min_pixels=3, pickable=True),
            pdk.Layer("ScatterplotLayer", data=data[data["changed"]], get_position=["lon", "lat"], filled=False,
                      stroked=True, get_line_color=CHANGED_RGB, line_width_min_pixels=2, get_radius=400,
                      radius_min_pixels=10),
        ]
    view = pdk.ViewState(longitude=-6.85, latitude=5.825, zoom=10)
    # no scroll-zoom: scrolling the page over the map must not zoom it mid-demo (+/- and double-click still zoom)
    map_view = pdk.View(type="MapView", controller={"scrollZoom": False, "dragPan": True, "doubleClickZoom": True})
    tooltip = {"html": "<b>{plot_id}</b> · {final_status}<br/>{tip}<br/>{pa_overlap_names}"}
    st.pydeck_chart(pdk.Deck(layers=layers, initial_view_state=view, views=[map_view], map_style=None,
                             tooltip=tooltip), height=500)
    st.caption("Double-click or +/- to zoom in to plot outlines; hover over a plot for its evidence.")


def fidelity_vs_raw():
    """Compare the current compact verdicts with the raw-layer pipeline run on the same data state (cached)."""
    verdicts = current_verdicts()
    if verdicts is None:
        return None
    after = "after" in st.session_state
    key = "raw_after" if after else "raw_before"
    if key not in st.session_state:
        plots = gpd.read_file(PLOTS_DIR / "plots.geojson")
        raw, _ = bl.run(plots) if after else bl.run(plots, HELD_BACK_HANSEN, HELD_BACK_RADD)
        st.session_state[key] = raw
    raw = st.session_state[key].set_index("plot_id").sort_index()
    comp = verdicts.set_index("plot_id").sort_index()[raw.columns]
    identical = (raw.astype(str) == comp.astype(str)).all(axis=1)
    decision = (raw[DECISION_FIELDS] == comp[DECISION_FIELDS]).all(axis=1)
    return {"plots": len(raw), "identical": int(identical.sum()), "decision": float(decision.mean())}


def detail_tabs(table):
    changed_tab, supplier_tab, all_tab = st.tabs(["Changed by the release", "Supplier check", "All verdicts"])
    with changed_tab:
        if table is None or "status_before" not in table:
            st.info("Run steps 3 and 4 to see which plots the release changes.")
        else:
            changed = table[table["changed"]]
            st.dataframe(changed[["plot_id", "status_before", "final_status", "first_disturbance_date",
                                  "disturbance_sources", "disturbed_after_cutoff_ha"]], hide_index=True,
                         use_container_width=True)
    with supplier_tab:
        result = st.session_state.get("verification")
        if result is None:
            st.info("Run step 5 to compare the supplier's declarations with our verdicts.")
        else:
            reasons = result.loc[~result["matches_declaration"], "mismatch_reason"].value_counts()
            st.dataframe(pd.DataFrame({"plots": reasons, "meaning": reasons.index.map(REASONS)}),
                         use_container_width=True)
    with all_tab:
        if table is not None:
            st.dataframe(table.drop(columns=["manifest_id"]), hide_index=True, use_container_width=True)


def main():
    st.set_page_config(page_title="EUDR compact store demo", layout="wide")
    st.title("Keep the decision, not the data")
    st.caption("EUDR plot verification, Côte d'Ivoire cocoa · 2,756 km² around Taï National Park · 500 plots")
    manifest = compact.load_manifest() if compact.MANIFEST_PATH.exists() else None
    if manifest is not None:
        restore_state(manifest)
    sidebar(manifest)
    if manifest is None:
        st.info("Start with step 1: build the compact store.")
        return
    metrics(manifest)
    table = verdict_table()
    draw_map(manifest, table)
    detail_tabs(table)


main()
