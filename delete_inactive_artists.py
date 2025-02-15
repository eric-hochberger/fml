import firebase_admin
from firebase_admin import credentials, firestore

def update_inactive_artists():
    # Initialize Firebase Admin SDK
    try:
        firebase_admin.get_app()
    except ValueError:
        cred = credentials.Certificate('/Users/Eric.Hochberger/Desktop/FML_New/html_test/fantasy-music-league-257a3-firebase-adminsdk-inqjy-2df7f3c2fa.json')
        firebase_admin.initialize_app(cred)

    db = firestore.client()

    try:
        # Get all team documents
        teams_ref = db.collection('teams')
        team_docs = teams_ref.stream()
        
        updates_count = 0
        teams_updated = 0

        for team in team_docs:
            team_data = team.to_dict()
            artists = team_data.get('artists', {})
            team_updated = False
            
            # Check each artist in the team
            for artist_id, artist_data in artists.items():
                if artist_data.get('active_flg') == 0:
                    print(f"Found inactive artist: {artist_data.get('name')} in team: {team_data.get('teamname')}")
                    updates_count += 1
                    team_updated = True

            if team_updated:
                teams_updated += 1

        # Show summary before proceeding
        print(f"\nFound {updates_count} inactive artists across {teams_updated} teams")
        
        if input("\nDo you want to proceed with deletion? (yes/no): ").lower() != 'yes':
            return {
                'status': 'cancelled',
                'message': 'Operation cancelled by user'
            }

        # Proceed with updates
        updates_made = 0
        teams_modified = 0

        for team in teams_ref.stream():
            team_data = team.to_dict()
            artists = team_data.get('artists', {})
            original_artist_count = len(artists)
            
            # Create new artists dict without inactive artists
            active_artists = {
                artist_id: artist_data 
                for artist_id, artist_data in artists.items() 
                if artist_data.get('active_flg') != 0
            }
            
            if len(active_artists) < original_artist_count:
                # Update the team document with only active artists
                team.reference.update({'artists': active_artists})
                updates_made += (original_artist_count - len(active_artists))
                teams_modified += 1
                print(f"Updated team: {team_data.get('teamname')}")

        return {
            'status': 'success',
            'message': f'Successfully removed {updates_made} inactive artists from {teams_modified} teams',
            'updates_made': updates_made,
            'teams_modified': teams_modified
        }

    except Exception as e:
        return {
            'status': 'error',
            'message': str(e)
        }

# Run the script
if __name__ == "__main__":
    result = update_inactive_artists()
    print(f"\n{result['message']}")