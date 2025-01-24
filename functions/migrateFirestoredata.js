// Initialize Firebase Admin SDK
const serviceAccount = require("/Users/Eric.Hochberger/Downloads/fantasy-musicpleague-test-firebase-adminsdk-wbxlo-d976899cc1.json");

const admin = require("firebase-admin");
const path = require("path");

admin.initializeApp({
  credential: admin.credential.cert(serviceAccount),
});

const db = admin.firestore();

async function migrateData() {
  try {
    // Step 1: Create the default public league
    const defaultLeagueId = "default_public_league";

    // Define default listener thresholds
    const defaultListenerThresholds = {
      artist_100k: 100000,
      artist_250k: 250000,
      artist_1m: 1000000,
    };

    // Check if the default league already exists
    const leagueDoc = await db.collection("leagues").doc(defaultLeagueId).get();

    if (!leagueDoc.exists) {
      console.log("Creating default public league...");
      await db.collection("leagues").doc(defaultLeagueId).set({
        leagueName: "Global League",
        creatorEmail: "admin@example.com", // Replace with an appropriate email
        isPublic: true,
        password: null,
        listenerThresholds: defaultListenerThresholds,
        members: [],
      });
      console.log("Default public league created.");
    } else {
      console.log("Default public league already exists.");
    }

    // Step 2: Migrate existing teams
    console.log("Migrating existing teams with new document IDs...");
    const teamsSnapshot = await db.collection("teams").get();

    let count = 0;

    for (const teamDoc of teamsSnapshot.docs) {
      const teamData = teamDoc.data();

      // Generate a new unique team ID
      const newTeamId = db.collection("teams").doc().id;

      // Prepare new team data
      const newTeamData = {
        ...teamData,
        leagueId: defaultLeagueId,
        userEmail: teamDoc.id, // Assuming the old document ID is the user's email
      };

      // Write new team document with the new ID
      await db.collection("teams").doc(newTeamId).set(newTeamData);

      // Delete the old team document
      await db.collection("teams").doc(teamDoc.id).delete();

      // Add the user to the default league's members array
      await db
          .collection("leagues")
          .doc(defaultLeagueId)
          .update({
            members: admin.firestore.FieldValue.arrayUnion(teamDoc.id),
          });

      count++;
    }

    console.log(`Migrated ${count} teams to the default league with new IDs.`);
  } catch (error) {
    console.error("Error during migration:", error);
  }
}

migrateData();
