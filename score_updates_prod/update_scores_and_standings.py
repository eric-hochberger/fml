import firebase_admin
from firebase_admin import credentials, firestore
import requests
import pandas as pd
import numpy as np
from datetime import datetime
import re
import time
import logging
import os
import json

from google.oauth2 import service_account
from googleapiclient.discovery import build

# Saw-live sheet (share with service account client_email, enable Sheets API in project)
SPREADSHEET_ID = "1TvNsjSmj1fj6PxK5DPP8Q6OHYJ1pb5YwGhGVWFhsrWA"
SHEET_GID = 366933681

# Set up logging
logging.basicConfig(filename='listener_errors.log', level=logging.INFO)

# Initialize Firebase Admin
if not firebase_admin._apps:
    firebase_credentials = os.getenv('FIREBASE_SERVICE_ACCOUNT')
    cred = credentials.Certificate(json.loads(firebase_credentials))
    firebase_admin.initialize_app(cred, {
        'projectId': 'fantasy-music-league-257a3'
    })

# Initialize Firestore client (remove the incorrect _client_options configuration)
db = firestore.client()

def extract_artist_id(artist_identifier):
    # Check if it's a full Spotify URL
    if artist_identifier.startswith('https://open.spotify.com/artist/'):
        # Extract the ID from the URL
        match = re.search(r'artist/([a-zA-Z0-9]+)', artist_identifier)
        return match.group(1) if match else artist_identifier
    return artist_identifier


def _extract_artist_id_from_url(url):
    """Parse Spotify artist ID from a URL string; returns None if not found."""
    if not url or not isinstance(url, str):
        return None
    url = url.strip()
    match = re.search(r"artist/([a-zA-Z0-9]+)", url)
    return match.group(1) if match else None


def _fetch_saw_live_sheet_rows(credentials_dict):
    """Read saw-live sheet via Google Sheets API; return list of dicts (header -> cell value)."""
    creds = service_account.Credentials.from_service_account_info(
        credentials_dict,
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
    )
    service = build("sheets", "v4", credentials=creds)
    meta = (
        service.spreadsheets()
        .get(spreadsheetId=SPREADSHEET_ID, fields="sheets(properties(sheetId,title))")
        .execute()
    )
    sheet_title = None
    for sheet in meta.get("sheets", []):
        props = sheet.get("properties", {})
        if props.get("sheetId") == SHEET_GID:
            sheet_title = props.get("title", "Sheet1")
            break
    if not sheet_title:
        raise RuntimeError(f"Sheet with gid={SHEET_GID} not found in spreadsheet")
    range_str = f"'{sheet_title}'!A:Z"
    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=SPREADSHEET_ID, range=range_str)
        .execute()
    )
    rows = result.get("values", [])
    if not rows:
        return []
    headers = [str(h).strip() for h in rows[0]]
    out = []
    for r in rows[1:]:
        padded = list(r) + [""] * (len(headers) - len(r))
        out.append(dict(zip(headers, padded)))
    return out


def _collect_email_artist_pairs(rows):
    """From sheet rows, find (email, artist_id) pairs. Column detection by header keywords."""
    if not rows:
        return set()
    keys = list(rows[0].keys()) if rows[0] else []
    spotify_key = email_key = None
    for k in keys:
        k_lower = (k or "").lower()
        if "spotify" in k_lower and "artist" in k_lower:
            spotify_key = k
        if "email" in k_lower and "address" in k_lower:
            email_key = k
    if not spotify_key or not email_key:
        spotify_key = keys[2] if len(keys) > 2 else None
        email_key = keys[-1] if keys else None
    pairs = set()
    for row in rows:
        link = row.get(spotify_key, "").strip() if spotify_key else ""
        email = row.get(email_key, "").strip().lower() if email_key else ""
        aid = _extract_artist_id_from_url(link)
        if email and aid:
            pairs.add((email, aid))
    return pairs


def _roster_artist_ids(artists_dict):
    """Normalize team artist keys (URL or ID) to a set of artist IDs."""
    ids = set()
    for key in artists_dict:
        aid = _extract_artist_id_from_url(key) if (key and str(key).startswith("http")) else key
        if aid:
            ids.add(aid)
    return ids


def refresh_saw_live_from_sheet(db, credentials_dict=None):
    """Fetch saw-live sheet, compute (email, artist_id) pairs, update each team's sawLiveArtistIds."""
    if not credentials_dict:
        print("Skipping saw-live sheet refresh (no credentials).")
        return
    print("Fetching sheet via Google Sheets API...")
    rows = _fetch_saw_live_sheet_rows(credentials_dict)
    if not rows:
        print("No rows in sheet; skipping saw-live sync.")
        return
    pairs = _collect_email_artist_pairs(rows)
    print(f"Found {len(pairs)} (email, artist_id) submissions")
    teams_ref = db.collection("teams")
    all_teams = list(teams_ref.stream())
    updates = 0
    for doc in all_teams:
        team_data = doc.to_dict()
        user_email = (team_data.get("userEmail") or "").strip().lower()
        if not user_email:
            continue
        artists = team_data.get("artists") or {}
        roster_ids = _roster_artist_ids(artists)
        saw_live = [aid for (em, aid) in pairs if em == user_email and aid in roster_ids]
        saw_live_dedup = list(dict.fromkeys(saw_live))
        if saw_live_dedup != team_data.get("sawLiveArtistIds", []):
            teams_ref.document(doc.id).update({"sawLiveArtistIds": saw_live_dedup})
            print(f"Updated team {doc.id} ({team_data.get('teamname', '')}): sawLiveArtistIds = {saw_live_dedup}")
            updates += 1
    print(f"Saw-live sync done. Updated {updates} team(s).")


def get_monthly_listeners(artist_code, retries=3):
    # Clean the artist code first
    artist_code = extract_artist_id(artist_code)
    
    # Use the Cloud Function instead of scraping directly to avoid bot detection
    cloud_function_url = f"https://us-central1-fantasy-music-league-257a3.cloudfunctions.net/getArtistMonthlyListeners?artistCode={artist_code}"
    
    print(f"Attempting to get monthly listeners for {artist_code} via Cloud Function")
    
    for attempt in range(retries):
        try:
            print(f"Attempt {attempt + 1} for {artist_code}")
            
            # Add some delay between requests
            if attempt > 0:
                time.sleep(2 + (attempt * 1))  # Progressive delay
            
            response = requests.get(cloud_function_url, timeout=30)
            
            # Check if the request was successful
            if response.status_code != 200:
                print(f"HTTP {response.status_code} error for {artist_code}: {response.text}")
                continue
            
            data = response.json()
            
            # Check if we got an error response
            if 'error' in data:
                print(f"Error from Cloud Function for {artist_code}: {data['error']}")
                continue
            
            # Extract artist name and monthly streams
            artist_name = data.get('artistName')
            monthly_streams = data.get('monthlyStreams', 0)
            
            if monthly_streams > 0:
                print(f"Successfully found {monthly_streams} monthly listeners for {artist_name}")
                return artist_name, monthly_streams
            else:
                print(f"Cloud Function returned 0 monthly listeners for {artist_code}")
                continue
            
        except requests.exceptions.RequestException as e:
            print(f"Request error for {artist_code}: {str(e)}")
            time.sleep(2)
        except json.JSONDecodeError as e:
            print(f"JSON decode error for {artist_code}: {str(e)}")
            time.sleep(2)
        except Exception as e:
            print(f"Unexpected error for {artist_code}: {str(e)}")
            time.sleep(2)
    
    print(f"Failed to get monthly listeners for {artist_code} after {retries} attempts")
    return None, None

def update_weeks_retained(df):
    # Calculate weeks retained for each artist individually
    for artist_code in df['artist_code'].unique():
        artist_df = df[df['artist_code'] == artist_code]
        date_columns = [col for col in artist_df.columns if re.match(r'\d{4}-\d{2}-\d{2}', col)]
        date_columns = sorted(date_columns, key=lambda date: datetime.strptime(date, '%Y-%m-%d'))
        
        if not date_columns:
            df.loc[df['artist_code'] == artist_code, 'weeks_retained'] = 0
        else:
            min_date = datetime.strptime(date_columns[0], '%Y-%m-%d')
            max_date = datetime.strptime(date_columns[-1], '%Y-%m-%d')
            weeks_retained = (max_date - min_date).days // 7
            df.loc[df['artist_code'] == artist_code, 'weeks_retained'] = weeks_retained
            
    return df

def calculate_percentage_difference_with_loyalty(df, team_data):
    df = update_weeks_retained(df)
    
    # Initialize a list to store each artist's adjusted percentage difference
    adjusted_diffs = []
    
    # Get all artists that have been both active and in top 5 at the same time
    artists_active_in_top_5 = set()
    if 'spotifyTopArtistHistory' in team_data:
        # For each date in Spotify history
        for spotify_date, top_artists in team_data['spotifyTopArtistHistory'].items():
            top_5_ids = {artist['id'] for artist in top_artists[:5]}
            
            # Check if any of these artists were active on that date
            for artist_code in top_5_ids:
                if artist_code in team_data['artists']:
                    artist_info = team_data['artists'][artist_code]
                    # Check if artist was active on this date
                    if artist_info.get('active_flg', 0) == 1:
                        # Check if we have listener data for this date
                        if spotify_date in artist_info:
                            artists_active_in_top_5.add(artist_code)
                            print(f"Found artist {artist_code} that was both active and in top 5 on {spotify_date}")
    
    if artists_active_in_top_5:
        print(f"Artists that were both active and in top 5: {artists_active_in_top_5}")
    
    for artist_code in df['artist_code'].unique():
        artist_df = df[df['artist_code'] == artist_code]
        
        # Get all the dates that have listener counts for this artist
        listener_dates = artist_df.filter(regex=r'\d{4}-\d{2}-\d{2}').columns
        relevant_dates = listener_dates[artist_df[listener_dates].notnull().any()]
        
        if not relevant_dates.empty:
            min_date = relevant_dates.min()
            max_date = relevant_dates.max()
            
            min_listeners = artist_df[min_date].values[0]
            max_listeners = artist_df[max_date].values[0]
            
            # Ensure both min and max listeners are valid numbers
            if min_listeners and max_listeners and min_listeners != 0:
                percentage_diff = (max_listeners - min_listeners) / min_listeners * 100
            else:
                percentage_diff = 0
            
            weeks_retained = artist_df['weeks_retained'].values[0] if 'weeks_retained' in artist_df else 0
            loyalty_multiplier = 1 + 0.05 * weeks_retained
            
            if percentage_diff > 0:
                adjusted_diff = percentage_diff * loyalty_multiplier
                # Apply 40% bonus if artist was both active and in top 5 at any point
                if artist_code in artists_active_in_top_5:
                    print(f"Applying 40% bonus to {artist_code} - was both active and in top 5!")
                    adjusted_diff *= 1.4
            else:
                adjusted_diff = percentage_diff

            # I Saw My Artist Perform bonus: 100% if positive, cap at 0 if negative
            saw_live_ids = set(team_data.get("sawLiveArtistIds") or [])
            artist_id_normalized = extract_artist_id(artist_code)
            if artist_id_normalized in saw_live_ids:
                if adjusted_diff > 0:
                    adjusted_diff *= 2
                    print(f"Applying 100% saw-live bonus to {artist_code}")
                else:
                    adjusted_diff = 0
                    print(f"Saw-live cap at 0 for {artist_code} (was non-positive)")
            
            adjusted_diffs.append(adjusted_diff)
    
    # Return the mean of all adjusted percentage differences
    return np.nanmean(adjusted_diffs) if adjusted_diffs else 0


def calculate_score_with_breakdown(df, team_data):
    """Same as calculate_percentage_difference_with_loyalty but also returns a list of {name, contribution} for UI."""
    df = update_weeks_retained(df)
    adjusted_diffs = []
    breakdown = []

    artists_active_in_top_5 = set()
    if 'spotifyTopArtistHistory' in team_data:
        for spotify_date, top_artists in team_data['spotifyTopArtistHistory'].items():
            top_5_ids = {artist['id'] for artist in top_artists[:5]}
            for artist_code in top_5_ids:
                if artist_code in team_data.get('artists', {}):
                    artist_info = team_data['artists'][artist_code]
                    if artist_info.get('active_flg', 0) == 1 and spotify_date in artist_info:
                        artists_active_in_top_5.add(artist_code)

    saw_live_ids = set(team_data.get("sawLiveArtistIds") or [])
    inactive_artists = []

    for artist_code in df['artist_code'].unique():
        artist_df = df[df['artist_code'] == artist_code]
        listener_dates = artist_df.filter(regex=r'\d{4}-\d{2}-\d{2}').columns
        relevant_dates = listener_dates[artist_df[listener_dates].notnull().any()]
        name = (team_data.get('artists') or {}).get(artist_code, {}).get('name', artist_code)

        if relevant_dates.empty:
            inactive_artists.append({'name': name})
            continue

        min_date, max_date = relevant_dates.min(), relevant_dates.max()
        min_listeners = artist_df[min_date].values[0]
        max_listeners = artist_df[max_date].values[0]
        if min_listeners and max_listeners and min_listeners != 0:
            percentage_diff = (max_listeners - min_listeners) / min_listeners * 100
        else:
            percentage_diff = 0

        weeks_retained = artist_df['weeks_retained'].values[0] if 'weeks_retained' in artist_df else 0
        loyalty_multiplier = 1 + 0.05 * weeks_retained

        if percentage_diff > 0:
            adjusted_diff = percentage_diff * loyalty_multiplier
            if artist_code in artists_active_in_top_5:
                adjusted_diff *= 1.4
        else:
            adjusted_diff = percentage_diff

        artist_id_norm = extract_artist_id(artist_code)
        saw_live = artist_id_norm in saw_live_ids
        if saw_live:
            adjusted_diff = adjusted_diff * 2 if adjusted_diff > 0 else 0
        top5 = artist_code in artists_active_in_top_5

        adjusted_diffs.append(adjusted_diff)
        breakdown.append({
            'name': name,
            'contribution': round(float(adjusted_diff), 2),
            'sawLive': saw_live,
            'top5': top5,
        })

    score = np.nanmean(adjusted_diffs) if adjusted_diffs else 0
    return score, breakdown, inactive_artists


def update_firestore():
    # Refresh "saw live" from Google Sheet before computing standings
    try:
        cred_json = os.environ.get('FIREBASE_SERVICE_ACCOUNT')
        if cred_json:
            refresh_saw_live_from_sheet(db, credentials_dict=json.loads(cred_json))
        else:
            print("Skipping saw-live sheet refresh (no FIREBASE_SERVICE_ACCOUNT).")
    except Exception as e:
        print(f"Saw-live sheet refresh failed (continuing with scores): {e}")

    teams_ref = db.collection('teams')
    standings_ref = db.collection('standings')

    # Add retry logic and timeout handling
    max_retries = 3
    for attempt in range(max_retries):
        try:
            docs = teams_ref.get()
            break
        except Exception as e:
            if attempt == max_retries - 1:
                print(f"Failed to fetch teams after {max_retries} attempts: {e}")
                return
            print(f"Attempt {attempt + 1} failed, retrying... Error: {e}")
            time.sleep(2 ** attempt)  # Exponential backoff
    
    current_date = datetime.now().strftime('%Y-%m-%d')
    
    # Dictionary to hold league data
    leagues = {}

    # Process documents with error handling
    for doc in docs:
        try:
            team_data = doc.to_dict()
            team_league_id = team_data.get('leagueId')
            if not team_league_id:
                continue  # Skip teams without a leagueId

            # Fetch league data
            league_doc = db.collection('leagues').document(team_league_id).get()
            if not league_doc.exists:
                print(f"League {team_league_id} not found.")
                continue

            league_data = league_doc.to_dict()
            start_date = league_data.get('startDate')
            end_date = league_data.get('endDate')

            # Check if startDate and endDate are present
            if not start_date or not end_date:
                print(f"Skipping league {team_league_id} due to missing start or end date.")
                continue

            # Check if current date is within the league's active period
            if not (start_date <= current_date <= end_date):
                print(f"Current date {current_date} is not within the league period {start_date} to {end_date}.")
                continue

            print(f"Updating team: {team_data['teamname']} in league {team_league_id}")

            # Initialize league in leagues dict if not present
            if team_league_id not in leagues:
                leagues[team_league_id] = {}

            artists_data = []
            for artist_code, artist_info in team_data['artists'].items():
                # Only update monthly listeners for active artists
                if artist_info.get('active_flg', 0) == 1:
                    artist_name, monthly_streams = get_monthly_listeners(artist_code)
                    if monthly_streams is not None:
                        team_data['artists'][artist_code][current_date] = monthly_streams
                        print(f"Updated {artist_name} ({artist_code}) with {monthly_streams} listeners.")
                    else:
                        print(f"Could not get monthly listeners for {artist_code}")

                # Always add artist data to DataFrame for scoring, regardless of active_flg
                artist_df = pd.DataFrame({
                    'artist_code': [artist_code],
                    **{k: [v] for k, v in team_data['artists'][artist_code].items() if isinstance(v, (int, float))}
                })
                artists_data.append(artist_df)
            
            if artists_data:
                # Ensure all DataFrames have the same columns before concatenation
                all_columns = set().union(*(df.columns for df in artists_data))
                artists_data = [df.reindex(columns=list(all_columns)) for df in artists_data]
                df = pd.concat(artists_data, ignore_index=True)
                df = update_weeks_retained(df)
                # Key by doc.id so duplicate team names in same league don't overwrite
                leagues[team_league_id][doc.id] = (df, team_data)
            else:
                # Team has no artists (or none with numeric data); include in standings with score 0
                leagues[team_league_id][doc.id] = (pd.DataFrame(), team_data)

            # Update team data in Firestore
            teams_ref.document(doc.id).set(team_data)
        
        except Exception as e:
            print(f"Error processing team {doc.id}: {e}")
            continue
    
    # Calculate standings per league with historical tracking
    for league_id, league_teams in leagues.items():
        standings = []
        for _team_id, (df, team_data) in league_teams.items():
            score, breakdown, inactive_artists = calculate_score_with_breakdown(df, team_data)
            standings.append({'teamname': team_data['teamname'], 'score': score})
            # Save score breakdown for team views (roster / view_team popup)
            try:
                payload = {
                    'updatedAt': current_date,
                    'score': round(float(score), 2),
                    'artists': breakdown,
                }
                if inactive_artists:
                    payload['inactiveArtists'] = inactive_artists
                teams_ref.document(_team_id).update({'lastScoreBreakdown': payload})
            except Exception as e:
                print(f"Warning: could not save lastScoreBreakdown for {_team_id}: {e}")

        standings_df = pd.DataFrame(standings)
        standings_df = standings_df.sort_values(by='score', ascending=False).reset_index(drop=True)

        # Get existing standings doc or create new one
        standings_doc_ref = standings_ref.document(league_id)
        standings_doc = standings_doc_ref.get()
        
        if standings_doc.exists:
            standings_data = standings_doc.to_dict()
            # Initialize history if it doesn't exist
            if 'history' not in standings_data:
                standings_data['history'] = {}
        else:
            standings_data = {
                'leagueId': league_id,
                'history': {}
            }
        
        # Update the standings history with today's standings
        standings_data['history'][current_date] = standings_df.to_dict(orient='records')
        
        # Keep current standings at top level for backward compatibility
        standings_data['standings'] = standings_df.to_dict(orient='records')
        standings_data['lastUpdated'] = current_date
        
        # Store the updated standings
        standings_doc_ref.set(standings_data)
        print(f"Updated standings and history for league {league_id}")

    print("Firestore update complete with standings per league")

if __name__ == '__main__':
    update_firestore()
