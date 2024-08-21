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
    firebase_credentials = os.getenv('FIREBASE_TEST_SERVICE_ACCOUNT')
    cred = credentials.Certificate(json.loads(firebase_credentials))
    firebase_admin.initialize_app(cred, {
        'projectId': 'fantasy-musicpleague-test'
    })

db = firestore.client()

def get_monthly_listeners(artist_code, retries=3):
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
    date_columns = [col for col in df.columns if re.match(r'\d{4}-\d{2}-\d{2}', col)]
    date_columns = sorted(date_columns, key=lambda date: datetime.strptime(date, '%Y-%m-%d'))
    
    if not date_columns:
        df['weeks_retained'] = 0
        return df

    min_date = datetime.strptime(date_columns[0], '%Y-%m-%d')
    max_date = datetime.strptime(date_columns[-1], '%Y-%m-%d')
    df['weeks_retained'] = (max_date - min_date).days // 7
    
    return df

def calculate_percentage_difference_with_loyalty(df):
    date_columns = [col for col in df.columns if re.match(r'\d{4}-\d{2}-\d{2}', col)]
    date_columns = sorted(date_columns, key=lambda date: datetime.strptime(date, '%Y-%m-%d'))
    
    if not date_columns:
        return 0
    
    min_date, max_date = date_columns[0], date_columns[-1]
    min_listeners = df[min_date]
    max_listeners = df[max_date]
    percentage_diffs = np.where(min_listeners == 0, 0, (max_listeners - min_listeners) / min_listeners * 100)
    
    loyalty_multiplier = 1 + 0.05 * df['weeks_retained']
    # Apply loyalty bonus only if the percentage difference is positive
    adjusted_percentage_diffs = np.where(percentage_diffs > 0, percentage_diffs * loyalty_multiplier, percentage_diffs)
    
    return np.mean(adjusted_percentage_diffs)

def update_firestore():
    teams_ref = db.collection('teams')
    standings_ref = db.collection('standings')
    docs = teams_ref.stream()

    current_date = datetime.now().strftime('%Y-%m-%d')
    league = {}

    for doc in docs:
        team_data = doc.to_dict()
        print(f"Updating team: {team_data['teamname']}")

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
            league[team_data['teamname']] = df
        
        teams_ref.document(doc.id).set(team_data)

    standings = []
    for teamname, df in league.items():
        score = calculate_percentage_difference_with_loyalty(df)
        standings.append({'teamname': teamname, 'score': score})
    
    standings_df = pd.DataFrame(standings)
    standings_df = standings_df.sort_values(by='score', ascending=False).reset_index(drop=True)
    
    for index, row in standings_df.iterrows():
        standings_ref.document(row['teamname']).set({
            'teamname': row['teamname'],
            'score': row['score']
        })
    
    print("Firestore update complete with standings")

if __name__ == '__main__':
    update_firestore()
