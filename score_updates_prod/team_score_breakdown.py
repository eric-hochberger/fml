"""
One-off script: print score breakdown for a single team from prod Firebase.
Shows per-artist: min/max listeners, % diff, loyalty, top-5 bonus, saw-live bonus, final contribution.

Usage (from repo root):
  FIREBASE_SERVICE_ACCOUNT='<prod-json>' python score_updates_prod/team_score_breakdown.py "Lexi's team but it's Gut's this time"
"""
import re
import os
import json
import sys
import pandas as pd
import numpy as np
from datetime import datetime

import firebase_admin
from firebase_admin import credentials, firestore

CRED_PATH = "/Users/Eric.Hochberger/Desktop/FML_New/html_test/fantasy-music-league-257a3-firebase-adminsdk-inqjy-2df7f3c2fa.json"

if not firebase_admin._apps:
    cred_json = os.getenv("FIREBASE_SERVICE_ACCOUNT")
    if cred_json:
        cred = credentials.Certificate(json.loads(cred_json))
    elif os.path.isfile(CRED_PATH):
        cred = credentials.Certificate(CRED_PATH)
    else:
        print("Set FIREBASE_SERVICE_ACCOUNT env or ensure CRED_PATH exists.")
        sys.exit(1)
    firebase_admin.initialize_app(cred, {"projectId": "fantasy-music-league-257a3"})

db = firestore.client()


def extract_artist_id(artist_identifier):
    if isinstance(artist_identifier, str) and artist_identifier.startswith("https://open.spotify.com/artist/"):
        m = re.search(r"artist/([a-zA-Z0-9]+)", artist_identifier)
        return m.group(1) if m else artist_identifier
    return artist_identifier


def update_weeks_retained(df):
    for artist_code in df["artist_code"].unique():
        artist_df = df[df["artist_code"] == artist_code]
        date_columns = [c for c in artist_df.columns if re.match(r"\d{4}-\d{2}-\d{2}", c)]
        date_columns = sorted(date_columns, key=lambda d: datetime.strptime(d, "%Y-%m-%d"))
        if not date_columns:
            df.loc[df["artist_code"] == artist_code, "weeks_retained"] = 0
        else:
            min_d = datetime.strptime(date_columns[0], "%Y-%m-%d")
            max_d = datetime.strptime(date_columns[-1], "%Y-%m-%d")
            weeks = (max_d - min_d).days // 7
            df.loc[df["artist_code"] == artist_code, "weeks_retained"] = weeks
    return df


def main():
    team_name = sys.argv[1] if len(sys.argv) > 1 else "Lexi's team but it's Gut's this time"
    teams_ref = db.collection("teams")
    doc = None
    for d in teams_ref.stream():
        if d.to_dict().get("teamname") == team_name:
            doc = d
            break
    if not doc:
        print(f"Team not found: {team_name!r}")
        sys.exit(1)

    team_data = doc.to_dict()
    league_id = team_data.get("leagueId", "")
    print(f"Team: {team_name}")
    print(f"League ID: {league_id}")
    print(f"sawLiveArtistIds: {team_data.get('sawLiveArtistIds', [])}")
    print()

    # Build artists_active_in_top_5 (same as prod)
    artists_active_in_top_5 = set()
    if "spotifyTopArtistHistory" in team_data:
        for spotify_date, top_artists in team_data["spotifyTopArtistHistory"].items():
            top_5_ids = {a["id"] for a in top_artists[:5]}
            for artist_code in top_5_ids:
                if artist_code in team_data.get("artists", {}):
                    info = team_data["artists"][artist_code]
                    if info.get("active_flg", 0) == 1 and spotify_date in info:
                        artists_active_in_top_5.add(artist_code)
    print(f"Artists that were both active and in top 5: {artists_active_in_top_5 or 'none'}")
    print()

    # Build df from existing artist data (no API calls)
    artists_data = []
    for artist_code, artist_info in (team_data.get("artists") or {}).items():
        row = {"artist_code": artist_code}
        for k, v in artist_info.items():
            if isinstance(v, (int, float)):
                row[k] = v
        artists_data.append(pd.DataFrame([row]))
    if not artists_data:
        print("No artist data.")
        sys.exit(0)
    all_cols = set().union(*(d.columns for d in artists_data))
    artists_data = [d.reindex(columns=list(all_cols)) for d in artists_data]
    df = pd.concat(artists_data, ignore_index=True)
    df = update_weeks_retained(df)

    saw_live_ids = set(team_data.get("sawLiveArtistIds") or [])
    adjusted_diffs = []

    print("Per-artist breakdown:")
    print("-" * 60)
    for artist_code in df["artist_code"].unique():
        artist_df = df[df["artist_code"] == artist_code]
        listener_dates = artist_df.filter(regex=r"\d{4}-\d{2}-\d{2}").columns
        relevant_dates = listener_dates[artist_df[listener_dates].notnull().any()]
        name = (team_data.get("artists") or {}).get(artist_code, {}).get("name", artist_code)

        if relevant_dates.empty:
            print(f"  {name}: no listener dates -> skipped")
            continue

        min_date = relevant_dates.min()
        max_date = relevant_dates.max()
        min_listeners = artist_df[min_date].values[0]
        max_listeners = artist_df[max_date].values[0]
        if min_listeners and max_listeners and min_listeners != 0:
            percentage_diff = (max_listeners - min_listeners) / min_listeners * 100
        else:
            percentage_diff = 0

        weeks_retained = artist_df["weeks_retained"].values[0] if "weeks_retained" in artist_df else 0
        loyalty_multiplier = 1 + 0.05 * weeks_retained

        if percentage_diff > 0:
            adjusted_diff = percentage_diff * loyalty_multiplier
            top5 = artist_code in artists_active_in_top_5
            if top5:
                adjusted_diff *= 1.4
        else:
            adjusted_diff = percentage_diff
            top5 = False

        artist_id_norm = extract_artist_id(artist_code)
        saw_live = artist_id_norm in saw_live_ids
        if saw_live:
            if adjusted_diff > 0:
                adjusted_diff *= 2
            else:
                adjusted_diff = 0

        adjusted_diffs.append(adjusted_diff)
        bonuses = []
        if top5:
            bonuses.append("top-5 +40%")
        if saw_live:
            bonuses.append("saw-live 2x or cap 0")
        bonus_str = " | " + ", ".join(bonuses) if bonuses else ""

        print(f"  {name}")
        print(f"    Min listeners: {min_listeners:,.0f}  Max: {max_listeners:,.0f}  -> % diff: {percentage_diff:.2f}%")
        print(f"    Weeks retained: {weeks_retained}  Loyalty mult: {loyalty_multiplier:.2f}{bonus_str}")
        print(f"    -> Contribution: {adjusted_diff:.2f}%")
        print()

    final = np.nanmean(adjusted_diffs) if adjusted_diffs else 0
    print("-" * 60)
    print(f"Final team score (average of {len(adjusted_diffs)} artists): {final:.2f}%")


if __name__ == "__main__":
    main()
