import firebase_admin
from firebase_admin import credentials, firestore
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
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

# Initialize Firestore client (remove the incorrect _client_options configuration)
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
    
    # Create a session with retry strategy
    session = requests.Session()
    retry_strategy = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    
    # More realistic headers
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'DNT': '1',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-User': '?1',
        'Cache-Control': 'max-age=0'
    }
    
    print(f"Attempting to get monthly listeners for {artist_code} from {artist_url}")
    
    for attempt in range(retries):
        try:
            print(f"Attempt {attempt + 1} for {artist_code}")
            
            # Add some delay between requests
            if attempt > 0:
                time.sleep(5 + (attempt * 2))  # Progressive delay
            
            response = session.get(artist_url, headers=headers, timeout=30)
            
            # Check if the request was successful
            if response.status_code != 200:
                print(f"HTTP {response.status_code} error for {artist_code}")
                continue
                
            web = BeautifulSoup(response.content, 'html.parser')
            
            # Debug: Print some basic info about the page
            title = web.find('title')
            print(f"Page title for {artist_code}: {title.get_text() if title else 'No title found'}")
            
            # Check if we got the "Unsupported browser" page
            if title and "Unsupported browser" in title.get_text():
                print(f"Got 'Unsupported browser' page for {artist_code}")
                continue
            
            # Method 1: Try meta description first
            meta_description = web.find('meta', {'name': 'description'})
            if meta_description:
                description = meta_description.get('content', '')
                print(f"Meta description for {artist_code}: {description[:100]}...")
                match = re.search(r'(\d+(?:\.\d+)?(?:,\d+)?[KMB]?) monthly listeners', description)
                if match:
                    listeners_str = match.group(1)
                    print(f"Found listeners in meta description: {listeners_str}")
                    # Convert K/M/B to actual numbers
                    multiplier = {
                        'K': 1000,
                        'M': 1000000,
                        'B': 1000000000
                    }
                    
                    # Remove commas and get the numeric part
                    base_num = float(listeners_str.replace(',', '').rstrip('KMB'))
                    
                    # Apply multiplier if K/M/B is present
                    if listeners_str[-1] in multiplier:
                        monthly_streams = int(base_num * multiplier[listeners_str[-1]])
                    else:
                        monthly_streams = int(base_num)
                    
                    # Get artist name
                    meta_title = web.find('meta', {'property': 'og:title'})
                    artist_name = meta_title.get('content', '').split('|')[0].strip() if meta_title else None
                    
                    if monthly_streams > 0:
                        print(f"Successfully found {monthly_streams} monthly listeners for {artist_name}")
                        return artist_name, monthly_streams

            # Method 2: Try finding all divs and look for listener count pattern
            div_content = [div.get_text() for div in web.find_all('div')]
            h1_content = [h1.get_text() for h1 in web.find_all('h1')]
            artist_name = h1_content[0] if h1_content else None
            print(f"Artist name from H1: {artist_name}")

            # Look for monthly listeners in div content
            for text in div_content:
                # Pattern 1: Direct number with "monthly listeners"
                match = re.search(r'(\d+(?:,\d+)*) monthly listeners', text)
                if match:
                    try:
                        monthly_streams = int(match.group(1).replace(',', ''))
                        if monthly_streams > 0:
                            print(f"Found {monthly_streams} monthly listeners in div content")
                            return artist_name, monthly_streams
                    except ValueError:
                        continue

                # Pattern 2: Number with K/M/B suffix
                match = re.search(r'(\d+(?:\.\d+)?[KMB]) monthly listeners', text)
                if match:
                    try:
                        num_str = match.group(1)
                        multiplier = {'K': 1000, 'M': 1000000, 'B': 1000000000}
                        base = float(num_str[:-1])
                        suffix = num_str[-1]
                        monthly_streams = int(base * multiplier[suffix])
                        if monthly_streams > 0:
                            print(f"Found {monthly_streams} monthly listeners in div content (with suffix)")
                            return artist_name, monthly_streams
                    except (ValueError, KeyError):
                        continue

            # Method 2.5: Try finding spans and look for listener count pattern
            span_content = [span.get_text() for span in web.find_all('span')]
            for text in span_content:
                match = re.search(r'(\d+(?:,\d+)*) monthly listeners', text)
                if match:
                    try:
                        monthly_streams = int(match.group(1).replace(',', ''))
                        if monthly_streams > 0:
                            print(f"Found {monthly_streams} monthly listeners in span content")
                            return artist_name, monthly_streams
                    except ValueError:
                        continue

            # Method 3: Look for structured data in script tags
            for script in web.find_all('script', type='application/ld+json'):
                try:
                    data = json.loads(script.string)
                    if isinstance(data, dict) and 'description' in data:
                        match = re.search(r'(\d+(?:,\d+)?[KMB]?) monthly listeners', data['description'])
                        if match:
                            listeners_str = match.group(1)
                            base_num = float(listeners_str.replace(',', '').rstrip('KMB'))
                            multiplier = {'K': 1000, 'M': 1000000, 'B': 1000000000}
                            if listeners_str[-1] in multiplier:
                                monthly_streams = int(base_num * multiplier[listeners_str[-1]])
                            else:
                                monthly_streams = int(base_num)
                            if monthly_streams > 0:
                                print(f"Found {monthly_streams} monthly listeners in structured data")
                                return artist_name, monthly_streams
                except (json.JSONDecodeError, AttributeError):
                    continue

            print(f"Attempt {attempt + 1}: Could not find monthly listeners for {artist_code}")
            time.sleep(2)  # Wait before retrying
            
        except requests.exceptions.RequestException as e:
            print(f"Request error for {artist_code}: {str(e)}")
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
            
            adjusted_diffs.append(adjusted_diff)
    
    # Return the mean of all adjusted percentage differences
    return np.nanmean(adjusted_diffs) if adjusted_diffs else 0

def update_firestore():
    teams_ref = db.collection('teams')
    standings_ref = db.collection('standings')
    
    # Add retry logic and timeout handling
    max_retries = 3
    for attempt in range(max_retries):
        try:
            # Use get() instead of stream() for better timeout control
            # Add timeout parameter to the get() call
            docs = teams_ref.get(timeout=300)  # 5 minute timeout
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
                leagues[team_league_id][team_data['teamname']] = (df, team_data)  # Store both df and team_data
            
            # Update team data in Firestore
            teams_ref.document(doc.id).set(team_data)
        
        except Exception as e:
            print(f"Error processing team {doc.id}: {e}")
            continue
    
    # Calculate standings per league with historical tracking
    for league_id, league_teams in leagues.items():
        standings = []
        for teamname, (df, team_data) in league_teams.items():
            score = calculate_percentage_difference_with_loyalty(df, team_data)
            standings.append({'teamname': teamname, 'score': score})

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
