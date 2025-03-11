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
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept-Language': 'en-US,en;q=0.9',
            }
            response = requests.get(artist_url, headers=headers)
            
            if response.status_code != 200:
                logging.error(f"Failed to get page for artist {artist_code}, status code: {response.status_code}")
                time.sleep(2)
                continue
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Try to find the artist name from title
            title_tag = soup.find('title')
            artist_name = title_tag.get_text().split('|')[0].strip() if title_tag else None
            
            # Method 1: Look for the specific span with monthly listeners
            # Note: Spotify might use different class names, so we'll try a few patterns
            monthly_listeners_span = None
            
            # Try to find spans containing "monthly listeners" text
            for span in soup.find_all('span'):
                if span.text and 'monthly listeners' in span.text:
                    monthly_listeners_span = span
                    break
            
            if monthly_listeners_span:
                # Extract the number from the text
                match = re.search(r'([\d,]+)', monthly_listeners_span.text)
                if match:
                    monthly_streams = int(match.group(1).replace(',', ''))
                    return artist_name, monthly_streams
            
            # Method 2: Check for the specific class name you mentioned
            span_with_class = soup.find('span', class_='Ydwa1P5GkCggtLlSvphs')
            if span_with_class and 'monthly listeners' in span_with_class.text:
                match = re.search(r'([\d,]+)', span_with_class.text)
                if match:
                    monthly_streams = int(match.group(1).replace(',', ''))
                    return artist_name, monthly_streams
            
            # Method 3: Look for any span containing digits followed by "monthly listeners"
            for span in soup.find_all('span'):
                if span.text and re.search(r'[\d,]+ monthly listeners', span.text):
                    match = re.search(r'([\d,]+)', span.text)
                    if match:
                        monthly_streams = int(match.group(1).replace(',', ''))
                        return artist_name, monthly_streams
            
            # Method 4: As a fallback, check meta description for approximate number
            meta_desc = soup.find('meta', attrs={'name': 'description'})
            if meta_desc and 'content' in meta_desc.attrs:
                desc_text = meta_desc.attrs['content']
                match = re.search(r'Artist · (\d+(?:\.\d+)?[KMB]?) monthly listeners', desc_text)
                if match:
                    listener_text = match.group(1)
                    # Convert K, M, B to actual numbers
                    if 'K' in listener_text:
                        monthly_streams = int(float(listener_text.replace('K', '')) * 1000)
                    elif 'M' in listener_text:
                        monthly_streams = int(float(listener_text.replace('M', '')) * 1000000)
                    elif 'B' in listener_text:
                        monthly_streams = int(float(listener_text.replace('B', '')) * 1000000000)
                    else:
                        monthly_streams = int(listener_text)
                    
                    return artist_name, monthly_streams
            
            logging.info(f"Attempt {attempt + 1}: Could not find monthly listeners for artist {artist_name or artist_code}. Retrying...")
            time.sleep(2)  # Wait before retrying
                
        except Exception as e:
            logging.error(f"Error getting monthly listeners for {artist_code}: {str(e)}")
            time.sleep(2)
    
    logging.error(f"Failed to get valid monthly listeners for artist {artist_name or artist_code} after {retries} attempts.")
    return artist_name, 0

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
