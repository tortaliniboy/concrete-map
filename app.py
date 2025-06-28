from flask import Flask
import requests
import pdfplumber
import re
import folium
import time
import traceback
from collections import defaultdict
from geopy.geocoders import ArcGIS
import io

app = Flask(__name__)

PDF_URL = "https://www.nyc.gov/html/dot/downloads/pdf/concretesch.pdf"
NYC_CENTRE = [40.7128, -74.0060]

def download_latest_pdf():
    try:
        r = requests.get(PDF_URL, stream=True, timeout=30)
        if r.status_code == 200:
            return io.BytesIO(r.content)
        else:
            print("Failed to download PDF. Status code:", r.status_code)
    except Exception as e:
        print("Exception during PDF download:", e)
    return None

def extract_rows(pdf_filelike):
    boro_tokens = ["Bronx", "Brooklyn", "Manhattan", "Queens", "STATEN"]
    rows, boro = [], None
    with pdfplumber.open(pdf_filelike) as pdf:
        for page in pdf.pages:
            for raw in page.extract_text().splitlines():
                ln = raw.strip()
                if not ln or "Schedule for" in ln or ln.startswith("Borough"):
                    continue
                if any(ln.startswith(b) for b in boro_tokens):
                    parts = ln.split(maxsplit=2)
                    boro = "Staten Island" if "STATEN" in parts[0] else parts[0]
                    ln = parts[2] if len(parts) > 2 else ""
                ln = ln.lstrip("SIP ").lstrip("IFA ")
                if not ln.endswith("Concrete"):
                    continue
                rows.append((boro, ln[:-8].strip()))
    return rows

def split_streets(block):
    street_types = r"( ST| STREET| AVE| AVENUE| RD| ROAD| BLVD| BOULEVARD| PKWY| PARKWAY| PL| PLACE| DR| DRIVE| CT| COURT| HWY| HIGHWAY| WAY| LANE| LN| EXPWY| EXPRESSWAY)$"
    tokens = re.split(r"\s{2,}", block)
    tokens = [t.strip() for t in tokens if t.strip()]
    if len(tokens) == 3:
        return tokens
    streets, buf = [], []
    for word in block.split():
        buf.append(word)
        joined = " ".join(buf)
        if re.search(street_types, joined, re.I):
            streets.append(joined)
            buf = []
        if len(streets) == 3:
            break
    if buf and len(streets) < 3:
        streets.append(" ".join(buf))
    while len(streets) < 3:
        streets.append("")
    return streets[:3]

def generate_map():
    pdf_filelike = download_latest_pdf()
    if not pdf_filelike:
        return None

    rows = extract_rows(pdf_filelike)
    borough_colours = defaultdict(
        lambda: "gray",
        {
            "Bronx": "red",
            "Brooklyn": "blue",
            "Manhattan": "green",
            "Queens": "orange",
            "Staten Island": "purple"
        }
    )
    m = folium.Map(location=NYC_CENTRE, zoom_start=11, tiles="CartoDB positron")
    geocoder = ArcGIS(timeout=10)
    for idx, (boro, block) in enumerate(rows):
        on_st, from_st, to_st = split_streets(block)
        if not (on_st and from_st):
            continue
        # Geocode start (On Street & From Street)
        start_addr = f"{on_st} & {from_st}, {boro}, NY"
        start_location = None
        tries = 0
        while start_location is None and tries < 3:
            try:
                start_location = geocoder.geocode(start_addr)
            except Exception:
                time.sleep(1)
            tries += 1
        if not start_location:
            continue
        # Geocode end (On Street & To Street), if To Street exists
        end_location = None
        if to_st:
            end_addr = f"{on_st} & {to_st}, {boro}, NY"
            tries = 0
            while end_location is None and tries < 3:
                try:
                    end_location = geocoder.geocode(end_addr)
                except Exception:
                    time.sleep(1)
                tries += 1
        # Draw line if both endpoints exist, otherwise just mark the start
        if end_location:
            folium.PolyLine(
                locations=[
                    [start_location.latitude, start_location.longitude],
                    [end_location.latitude, end_location.longitude]
                ],
                color=borough_colours[boro],
                weight=6,
                opacity=0.35,
                popup=f"{boro}: {on_st} from {from_st} to {to_st}"
            ).add_to(m)
            folium.CircleMarker(
                location=[start_location.latitude, start_location.longitude],
                radius=4,
                color=borough_colours[boro],
                fill=True,
                fill_color=borough_colours[boro],
                fill_opacity=0.9,
                popup=f"START: {on_st} & {from_st}"
            ).add_to(m)
            folium.CircleMarker(
                location=[end_location.latitude, end_location.longitude],
                radius=4,
                color=borough_colours[boro],
                fill=True,
                fill_color=borough_colours[boro],
                fill_opacity=0.9,
                popup=f"END: {on_st} & {to_st}"
            ).add_to(m)
        else:
            folium.Marker(
                location=[start_location.latitude, start_location.longitude],
                popup=f"{boro}: {on_st} at {from_st} (no end point found)",
                icon=folium.Icon(color=borough_colours[boro])
            ).add_to(m)
    return m

@app.route('/')
def home():
    try:
        m = generate_map()
        if m is None:
            return "Failed to generate map. Please try again later."
        return m.get_root().render()
    except Exception as e:
        traceback.print_exc()
        return f"An error occurred: {str(e)}"

