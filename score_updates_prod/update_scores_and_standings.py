import firebase_admin
from firebase_admin import credentials, firestore
import requests
from bs4 import BeautifulSoup
import pandas as pd
import numpy as np
from datetime import datetime
import re
import time
import logging
import os
import json

# Set up logging
logging.basicConfig(filename='listener_errors.log', level=logging.INFO)

# Initialize Firebase Admin
if not firebase_admin._apps:
    firebase_credentials = os.getenv('FIREBASE_SERVICE_ACCOUNT')
    cred = credentials.Certificate(json.loads(firebase_credentials))
    firebase_admin.initialize_app(cred, {
        'projectId': 'fantasy-music-league-257a3'
    })

db = firestore.client()

def extract_artist_id(artist_identifier):
    # Check if it's a full Spotify URL
    if artist_identifier.startswith('https://open.spotify.com/artist/'):
        # Extract the ID from the URL
        match = re.search(r'artist/([a-zA-Z0-9]+)', artist_identifier)
        return match.group(1) if match else artist_identifier
    return artist_identifier

def get_monthly_listeners(artist_code, retries=3):
    # Clean the artist code first
    artist_code = extract_artist_id(artist_code)
    artist_url = f"https://open.spotify.com/artist/{artist_code}"
    
    for attempt in range(retries):
        response = requests.get(artist_url)
        web = BeautifulSoup(response.content, 'html.parser')
        div_content = [div.get_text() for div in web.find_all('div')]
        h1_content = [h1.get_text() for h1 in web.find_all('h1')]
        artist_name = h1_content[0] if h1_content else None

        monthly_streams = div_content[9] if len(div_content) > 9 else None
        if monthly_streams:
            try:
                monthly_streams = int(monthly_streams.split(" ")[0].replace(",", ""))
            except ValueError:
                monthly_streams = 0

        if monthly_streams != 0:
            return artist_name, monthly_streams
        else:
            logging.info(f"Attempt {attempt + 1}: Monthly listeners for artist {artist_name or artist_code} returned as 0. Retrying...")
            time.sleep(2)  # Wait before retrying

    logging.error(f"Failed to get valid monthly listeners for artist {artist_name or artist_code} after {retries} attempts.")
    return artist_name, monthly_streams

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

def calculate_percentage_difference_with_loyalty(df):
    df = update_weeks_retained(df)
    
    # Initialize a list to store each artist's adjusted percentage difference
    adjusted_diffs = []
    
    for artist_code in df['artist_code'].unique():
        artist_df = df[df['artist_code'] == artist_code]
        
        # Get all the dates that have listener counts for this artist
        listener_dates = artist_df.filter(regex=r'\d{4}-\d{2}-\d{2}').columns
        relevant_dates = listener_dates[artist_df[listener_dates].notnull().any()]
        
        if not relevant_dates.empty:
            min_date = relevant_dates.min()  # Earliest date with data
            max_date = relevant_dates.max()  # Latest date with data
            
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
            else:
                adjusted_diff = percentage_diff
            
            adjusted_diffs.append(adjusted_diff)
    
    # Return the mean of all adjusted percentage differences
    return np.nanmean(adjusted_diffs)

def update_firestore():
    teams_ref = db.collection('teams')
    standings_ref = db.collection('standings')
    docs = teams_ref.stream()

    current_date = datetime.now().strftime('%Y-%m-%d')
    
    # Dictionary to hold league data
    leagues = {}

    for doc in docs:
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
            if artist_info.get('active_flg', 0) == 1:  # Only update active artists
                artist_name, monthly_streams = get_monthly_listeners(artist_code)
                team_data['artists'][artist_code][current_date] = monthly_streams
                print(f"Updated {artist_name} ({artist_code}) with {monthly_streams} listeners.")

                artist_df = pd.DataFrame([team_data['artists'][artist_code]])
                artist_df['artist_code'] = artist_code
                artists_data.append(artist_df)
        
        if artists_data:
            df = pd.concat(artists_data)
            df = update_weeks_retained(df)
            leagues[team_league_id][team_data['teamname']] = df
        
        # Update team data in Firestore
        teams_ref.document(doc.id).set(team_data)
    
    # Now calculate standings per league
    for league_id, league_teams in leagues.items():
        standings = []
        for teamname, df in league_teams.items():
            score = calculate_percentage_difference_with_loyalty(df)
            standings.append({'teamname': teamname, 'score': score})

        standings_df = pd.DataFrame(standings)
        standings_df = standings_df.sort_values(by='score', ascending=False).reset_index(drop=True)

        # Update standings in Firestore under the leagueId
        standings_doc_ref = standings_ref.document(league_id)
        standings_data = {
            'leagueId': league_id,
            'standings': standings_df.to_dict(orient='records'),
            'lastUpdated': current_date
        }
        standings_doc_ref.set(standings_data)
        print(f"Updated standings for league {league_id}")

    print("Firestore update complete with standings per league")

if __name__ == '__main__':
    update_firestore()
