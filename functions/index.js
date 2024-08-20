/**
 * Import function triggers from their respective submodules:
 *
 * const {onCall} = require("firebase-functions/v2/https");
 * const {onDocumentWritten} = require("firebase-functions/v2/firestore");
 *
 * See a full list of supported triggers at https://firebase.google.com/docs/functions
 */

const functions = require("firebase-functions");
const axios = require("axios");
const qs = require("querystring");
const cheerio = require("cheerio");
const admin = require("firebase-admin");
const cors = require("cors")({origin: true});

admin.initializeApp();

const db = admin.firestore();

const clientId = functions.config().spotify.client_id;
const clientSecret = functions.config().spotify.client_secret;

/**
 * Extracts the artist code from a Spotify artist URL.
 * @param {string} url - The Spotify artist URL.
 * @return {string|null} The artist code or null if not found.
 */
function extractArtistCode(url) {
  const parts = url.split("artist/");
  if (parts.length < 2) return null;
  return parts[1].split("?")[0];
}

/**
 * Fetches the monthly listeners for a given artist from Spotify.
 * Retries the request if the initial attempt fails or returns 0 listeners.
 *
 * @param {string} artistCode - The Spotify artist code.
 * @param {number} [retries=3] - The number of times to retry the request in
 * case of failure.
 * @return {Promise<{artistName: string, monthlyStreams: number}>} - An object
 * containing the artist's name and monthly listeners.
 */
async function getMonthlyListeners(artistCode, retries = 3) {
  const artistUrl = `https://open.spotify.com/artist/${artistCode}`;

  for (let attempt = 1; attempt <= retries; attempt++) {
    try {
      const response = await axios.get(artistUrl, {timeout: 10000});
      const $ = cheerio.load(response.data);
      const artistName = $("h1").first().text();
      let monthlyStreams = $("div").eq(9).text();

      if (monthlyStreams) {
        monthlyStreams = parseInt(
            monthlyStreams.split(" ")[0].replace(/,/g, ""),
            10,
        );
      } else {
        monthlyStreams = 0;
      }

      // Log if monthlyStreams is 0
      if (monthlyStreams === 0) {
        console.warn(
            `Attempt ${attempt}: Monthly listeners for artist ` +
            `${artistName || artistCode} returned as 0.`,
        );
      }

      // If successful or final attempt, return the result
      if (monthlyStreams > 0 || attempt === retries) {
        return {artistName: artistName || "Unknown", monthlyStreams};
      }
    } catch (error) {
      console.error(
          `Attempt ${attempt}: Error fetching monthly listeners for artist ` +
          `${artistCode}:`,
          error,
      );

      // If final attempt, return the result with a warning
      if (attempt === retries) {
        console.warn(
            `Final attempt failed for artist ${artistCode}. ` +
            `Returning 0 monthly streams.`,
        );
        return {artistName: "Unknown", monthlyStreams: 0};
      }
    }

    // Optional: Add a delay before retrying
    await new Promise((resolve) => setTimeout(resolve, 2000)); // Wait for 2 sec
  }
}

exports.getToken = functions.https.onRequest((req, res) => {
  cors(req, res, async () => {
    const tokenUrl = "https://accounts.spotify.com/api/token";
    const credentials = Buffer.from(`${clientId}:${clientSecret}`).toString(
        "base64",
    );

    try {
      const response = await axios.post(
          tokenUrl,
          qs.stringify({grant_type: "client_credentials"}),
          {
            headers: {
              "Authorization": `Basic ${credentials}`,
              "Content-Type": "application/x-www-form-urlencoded",
            },
          },
      );
      res.json(response.data);
    } catch (error) {
      res.status(500).json({error: "Failed to retrieve access token"});
    }
  });
});

exports.validate = functions.https.onRequest((req, res) => {
  cors(req, res, async () => {
    const {email, teamname, artists} = req.body;
    console.log("Received validation request:", req.body);

    const validationIssues = [];
    const currentDate = new Date().toISOString().split("T")[0];

    for (const [category, artist] of Object.entries(artists)) {
      if (!artist.link) continue;

      const artistCode = extractArtistCode(artist.link);
      console.log(
          `Validating artist: ${artist.link}, extracted code: ${artistCode}`,
      );
      if (!artistCode) {
        validationIssues.push({
          email,
          message:
            `The artist link ${artist.link} is not valid.` +
            ` Please provide a valid Spotify artist link.`,
        });
        continue;
      }

      const {artistName, monthlyStreams} = await getMonthlyListeners(
          artistCode,
      );
      console.log(`Artist: ${artistName}, Monthly Streams: ${monthlyStreams}`);

      let maxListeners;
      if (category === "artist_100k") maxListeners = 100000;
      else if (category.startsWith("artist_250k")) maxListeners = 250000;
      else if (category === "artist_1m") maxListeners = 1000000;

      if (monthlyStreams > maxListeners) {
        validationIssues.push({
          email,
          message:
            `The artist ${artistName} has ${monthlyStreams} monthly listeners` +
            ` ,which exceeds the limit for the ${category} category.`,
        });
      }

      if (monthlyStreams < 500) {
        validationIssues.push({
          email,
          message:
            `The artist ${artistName} has less than 500 monthly listeners,` +
            ` which does not meet the minimum requirement.`,
        });
      }
    }

    console.log("Validation issues:", validationIssues);

    if (validationIssues.length > 0) {
      res.json({status: "error", issues: validationIssues});
    } else {
      const teamData = {
        teamname,
        active_flg: 1,
        artists: {},
      };

      for (const [category, artist] of Object.entries(artists)) {
        const artistCode = extractArtistCode(artist.link);
        const {artistName, monthlyStreams} = await getMonthlyListeners(
            artistCode,
        );

        teamData.artists[artistCode] = {
          name: artistName,
          imageUrl: artist.imageUrl,
          link: artist.link,
          [currentDate]: monthlyStreams,
          active_flg: 1,
          slot: category, // Store the slot/category
        };
      }

      // Save to Firestore
      const teamDoc = db.collection("teams").doc(email);
      await teamDoc.set(teamData);

      res.json({
        status: "success",
        message: "Roster submitted successfully!",
      });
    }
  });
});

exports.validateAddDrop = functions.https.onRequest((req, res) => {
  cors(req, res, async () => {
    const {email, dropArtistCode, addArtistLink, addArtistImageUrl} =
      req.body;
    console.log("Received add/drop validation request:", req.body);

    const validationIssues = [];
    const currentDate = new Date().toISOString().split("T")[0];

    // Fetch the user's team data
    const teamDoc = db.collection("teams").doc(email);
    const teamData = (await teamDoc.get()).data();

    if (!teamData || !teamData.artists || !teamData.artists[dropArtistCode]) {
      res.json({
        status: "error",
        issues: [
          {
            message:
              "Could not determine the slot for the artist to be dropped",
          },
        ],
      });
      return;
    }

    const dropArtist = teamData.artists[dropArtistCode];
    const dropArtistSlot = dropArtist.slot; // Use the saved slot information

    if (!dropArtistSlot) {
      res.json({
        status: "error",
        issues: [
          {
            message:
              "Could not determine the slot for the artist to be dropped",
          },
        ],
      });
      return;
    }

    // Validate the add artist
    const addArtistCode = extractArtistCode(addArtistLink);
    const {artistName, monthlyStreams} = await getMonthlyListeners(
        addArtistCode,
    );
    console.log(
        `Add Artist: ${artistName}, Monthly Streams: ${monthlyStreams}`,
    );

    let maxListeners;
    if (dropArtistSlot === "artist_100k") maxListeners = 100000;
    else if (dropArtistSlot.startsWith("artist_250k")) maxListeners = 250000;
    else if (dropArtistSlot === "artist_1m") maxListeners = 1000000;

    if (monthlyStreams > maxListeners) {
      validationIssues.push({
        message:
          `The artist ${artistName} has ${monthlyStreams} monthly listeners,` +
          ` which exceeds the limit for the ${dropArtistSlot} category.`,
      });
    }

    if (monthlyStreams < 500) {
      validationIssues.push({
        message:
          `The artist ${artistName} has less than 500 monthly listeners,` +
          ` which does not meet the minimum requirement.`,
      });
    }

    if (
      Object.values(teamData.artists).some(
          (artist) => artist.link === addArtistLink && artist.active_flg === 1,
      )
    ) {
      validationIssues.push({
        message: `The artist ${artistName} is already on your team.`,
      });
    }

    if (validationIssues.length > 0) {
      res.json({status: "error", issues: validationIssues});
    } else {
      // Update the Firestore document
      teamData.artists[dropArtistCode].active_flg = 0;
      teamData.artists[addArtistCode] = {
        name: artistName,
        imageUrl: addArtistImageUrl,
        link: addArtistLink,
        slot: dropArtistSlot, // Assign the same slot as the dropped artist
        [currentDate]: monthlyStreams,
        active_flg: 1,
      };

      await teamDoc.set(teamData);
      res.json({status: "success", message: "Roster updated successfully!"});
    }
  });
});

exports.saveTeam = functions.https.onRequest((req, res) => {
  cors(req, res, async () => {
    const {email, teamname, artists} = req.body;
    console.log("Saving team name:", teamname, "for email:", email);

    const currentDate = new Date().toISOString().split("T")[0];
    const teamData = {
      teamname,
      active_flg: 1,
      artists: {},
    };

    for (const slot in artists) {
      if (Object.prototype.hasOwnProperty.call(artists, slot)) {
        const artist = artists[slot];
        const artistCode = extractArtistCode(artist.link);
        const {artistName, monthlyStreams} = await getMonthlyListeners(
            artistCode,
        );

        teamData.artists[artistCode] = {
          name: artistName,
          imageUrl: artist.imageUrl,
          link: artist.link,
          slot: slot, // Save the slot information
          [currentDate]: monthlyStreams,
          active_flg: 1,
        };
      }
    }

    // Save to Firestore
    const teamDoc = db.collection("teams").doc(email);
    await teamDoc.set(teamData);

    res.json({status: "success", message: "Team name saved successfully!"});
  });
});

exports.checkTeam = functions.https.onRequest((req, res) => {
  cors(req, res, async () => {
    const email = req.query.email;
    const teamDoc = db.collection("teams").doc(email);
    const doc = await teamDoc.get();

    if (doc.exists) {
      res.json({hasTeam: true, team: doc.data()});
    } else {
      res.json({hasTeam: false});
    }
  });
});

exports.getTeamByName = functions.https.onRequest((req, res) => {
  cors(req, res, async () => {
    const teamname = req.query.teamname;
    const teamsRef = db.collection("teams");
    const snapshot = await teamsRef.where("teamname", "==", teamname).get();

    if (snapshot.empty) {
      res.json({hasTeam: false});
      return;
    }

    let teamData = null;
    snapshot.forEach((doc) => {
      teamData = doc.data();
    });

    if (teamData) {
      res.json({hasTeam: true, team: teamData});
    } else {
      res.json({hasTeam: false});
    }
  });
});

exports.getStandings = functions.https.onRequest((req, res) => {
  cors(req, res, async () => {
    const standingsRef = db.collection("standings");
    const snapshot = await standingsRef.get();
    const standings = snapshot.docs.map((doc) => doc.data());
    res.json(standings);
  });
});

exports.checkTeamName = functions.https.onRequest((req, res) => {
  cors(req, res, async () => {
    const teamname = req.query.teamname;
    const snapshot = await db
        .collection("teams")
        .where("teamname", "==", teamname)
        .get();
    if (!snapshot.empty) {
      res.json({exists: true});
    } else {
      res.json({exists: false});
    }
  });
});

exports.getFirebaseConfig = functions.https.onRequest((req, res) => {
  // Enable CORS using the middleware
  cors(req, res, () => {
    res.json({
      apiKey: functions.config().app_config.api_key,
      authDomain: functions.config().app_config.auth_domain,
      projectId: functions.config().app_config.project_id,
      storageBucket: functions.config().app_config.storage_bucket,
      messagingSenderId: functions.config().app_config.messaging_sender_id,
      appId: functions.config().app_config.app_id,
      measurementId: functions.config().app_config.measurement_id,
    });
  });
});
