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
const cors = require("cors")({ origin: true });
const bcrypt = require("bcrypt");
const fetch = require("node-fetch");

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
// async function getMonthlyListeners(artistCode, retries = 3) {
//   const artistUrl = `https://open.spotify.com/artist/${artistCode}`;

//   for (let attempt = 1; attempt <= retries; attempt++) {
//     try {
//       const response = await axios.get(artistUrl, { timeout: 10000 });
//       const $ = cheerio.load(response.data);
//       const artistName = $("h1").first().text();
//       let monthlyStreams = $("div").eq(9).text();

//       if (monthlyStreams) {
//         monthlyStreams = parseInt(
//           monthlyStreams.split(" ")[0].replace(/,/g, ""),
//           10,
//         );
//       } else {
//         monthlyStreams = 0;
//       }

//       // Log if monthlyStreams is 0
//       if (monthlyStreams === 0) {
//         console.warn(
//           `Attempt ${attempt}: Monthly listeners for artist ` +
//             `${artistName || artistCode} returned as 0.`,
//         );
//       }

//       // If successful or final attempt, return the result
//       if (monthlyStreams > 0 || attempt === retries) {
//         return { artistName: artistName || "Unknown", monthlyStreams };
//       }
//     } catch (error) {
//       console.error(
//         `Attempt ${attempt}: Error fetching monthly listeners for artist ` +
//           `${artistCode}:`,
//         error,
//       );

//       // If final attempt, return the result with a warning
//       if (attempt === retries) {
//         console.warn(
//           `Final attempt failed for artist ${artistCode}. ` +
//             `Returning 0 monthly streams.`,
//         );
//         return { artistName: "Unknown", monthlyStreams: 0 };
//       }
//     }

//     // Optional: Add a delay before retrying
//     await new Promise((resolve) => setTimeout(resolve, 2000)); // Wait for 2 sec
//   }
// }

exports.getArtistMonthlyListeners = functions.https.onRequest((req, res) => {
  cors(req, res, async () => {
    const artistCode = req.query.artistCode;

    try {
      const artistUrl = `https://open.spotify.com/artist/${artistCode}`;

      const headers = {
        "User-Agent":
          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
      };

      const response = await axios.get(artistUrl, {
        headers: headers,
        timeout: 10000,
      });

      const $ = cheerio.load(response.data);
      const artistName = $("title").text().split("|")[0].trim();
      let monthlyStreams = 0;

      // Method 1: Look for spans containing "monthly listeners" text
      $("span").each((i, el) => {
        const text = $(el).text();
        if (text && text.includes("monthly listeners")) {
          const match = text.match(/([\d,]+)/);
          if (match) {
            monthlyStreams = parseInt(match[1].replace(/,/g, ""), 10);
            return false; // Break the loop once found
          }
        }
      });

      // Method 2: Look for the specific class that contains monthly listeners
      if (monthlyStreams === 0) {
        const spanWithClass = $("span.Ydwa1P5GkCggtLlSvphs");
        if (
          spanWithClass.length > 0 &&
          spanWithClass.text().includes("monthly listeners")
        ) {
          const match = spanWithClass.text().match(/([\d,]+)/);
          if (match) {
            monthlyStreams = parseInt(match[1].replace(/,/g, ""), 10);
          }
        }
      }

      // Method 3: Look for any element containing digits followed by "monthly listeners"
      if (monthlyStreams === 0) {
        $("*").each((i, el) => {
          const text = $(el).text();
          if (text && /[\d,]+ monthly listeners/.test(text)) {
            const match = text.match(/([\d,]+)/);
            if (match) {
              monthlyStreams = parseInt(match[1].replace(/,/g, ""), 10);
              return false; // Break the loop once found
            }
          }
        });
      }

      // Method 4: As a fallback, check meta description for approximate number
      if (monthlyStreams === 0) {
        const metaDesc = $("meta[name='description']").attr("content");
        if (metaDesc) {
          const match = metaDesc.match(
            /Artist · (\d+(?:\.\d+)?[KMB]?) monthly listeners/,
          );
          if (match) {
            const listenerText = match[1];
            if (listenerText.includes("K")) {
              monthlyStreams = Math.round(
                parseFloat(listenerText.replace("K", "")) * 1000,
              );
            } else if (listenerText.includes("M")) {
              monthlyStreams = Math.round(
                parseFloat(listenerText.replace("M", "")) * 1000000,
              );
            } else if (listenerText.includes("B")) {
              monthlyStreams = Math.round(
                parseFloat(listenerText.replace("B", "")) * 1000000000,
              );
            } else {
              monthlyStreams = parseInt(listenerText, 10);
            }
          }
        }
      }

      if (monthlyStreams === 0) {
        console.warn(
          `Could not find monthly listeners for artist ${artistName || artistCode}`,
        );
      }

      res.json({ artistName, monthlyStreams });
    } catch (error) {
      console.error("Error fetching artist data:", error);
      res.status(500).json({ error: "Error fetching artist data." });
    }
  });
});

exports.getToken = functions.https.onRequest((req, res) => {
  cors(req, res, async () => {
    const tokenUrl = "https://accounts.spotify.com/api/token";
    const credentials = Buffer.from(`${clientId}:${clientSecret}`).toString(
      "base64",
    );

    try {
      const response = await axios.post(
        tokenUrl,
        qs.stringify({ grant_type: "client_credentials" }),
        {
          headers: {
            Authorization: `Basic ${credentials}`,
            "Content-Type": "application/x-www-form-urlencoded",
          },
        },
      );
      res.json(response.data);
    } catch (error) {
      res.status(500).json({ error: "Failed to retrieve access token" });
    }
  });
});

// index.js (Firebase Cloud Functions)
exports.validate = functions.https.onRequest((req, res) => {
  cors(req, res, async () => {
    const { email, teamname, leagueId, artists } = req.body;
    console.log("Received validation request:", req.body);

    const validationIssues = [];
    const currentDate = new Date().toISOString().split("T")[0];

    // Fetch league data to get listener thresholds
    let leagueData;
    try {
      const leagueDoc = await db.collection("leagues").doc(leagueId).get();
      if (leagueDoc.exists) {
        leagueData = leagueDoc.data();
      } else {
        res.json({
          status: "error",
          issues: [{ message: "League not found." }],
        });
        return;
      }
    } catch (error) {
      console.error("Error fetching league data:", error);
      res.json({
        status: "error",
        issues: [{ message: "Error fetching league data." }],
      });
      return;
    }

    // **Check if user is in pending_members**
    if (
      !leagueData.pending_members ||
      !leagueData.pending_members.includes(email)
    ) {
      res.json({
        status: "error",
        issues: [
          {
            message:
              "You have not initiated joining this league" +
              " or have already completed the process.",
          },
        ],
      });
      return;
    }

    // Collect thresholds from league data
    const listenerThresholds = leagueData.listenerThresholds;

    // Validate each artist
    for (const [slotKey, artist] of Object.entries(artists)) {
      if (!artist.link) continue;

      const artistCode = extractArtistCode(artist.link);
      console.log(
        `Validating artist: ${artist.link}, extracted code: ${artistCode}`,
      );

      if (!artistCode) {
        validationIssues.push({
          email,
          message:
            `The artist link ${artist.link} is not valid. ` +
            `Please provide a valid Spotify artist link.`,
        });
        continue;
      }

      // Use getArtistMonthlyListeners instead of getMonthlyListeners
      const response = await axios.get(
        `https://us-central1-fantasy-musicpleague-test.cloudfunctions.net/getArtistMonthlyListeners?artistCode=${artistCode}`,
      );
      const { artistName, monthlyStreams } = response.data;
      console.log(`Artist: ${artistName}, Monthly Streams: ${monthlyStreams}`);

      // Check minimum monthly listeners
      if (monthlyStreams < 500) {
        validationIssues.push({
          email,
          message:
            `The artist ${artistName} has less than 500 monthly listeners, ` +
            `which does not meet the minimum requirement.`,
        });
        continue;
      }

      // Get the slot number from the slotKey (e.g., "slot_1" -> 1)
      const slotNumber = parseInt(slotKey.split("_")[1], 10);
      const slotThreshold = listenerThresholds[`slot_${slotNumber}`];

      // Check if artist exceeds the slot's listener threshold
      if (monthlyStreams >= slotThreshold) {
        validationIssues.push({
          email,
          message:
            `The artist ${artistName} has ${monthlyStreams} ` +
            `monthly listeners, ` +
            `which exceeds the limit for slot ${slotNumber} ` +
            `(${slotThreshold} monthly listeners).`,
        });
      }
    }

    console.log("Validation issues:", validationIssues);

    if (validationIssues.length > 0) {
      res.json({ status: "error", issues: validationIssues });
    } else {
      const existingTeamQuery = db
        .collection("teams")
        .where("teamname", "==", teamname)
        .where("leagueId", "==", leagueId);
      const existingTeamSnapshot = await existingTeamQuery.get();

      if (!existingTeamSnapshot.empty) {
        res.json({
          status: "error",
          issues: [
            { message: "A team with this name already exists in the league." },
          ],
        });
        return;
      }

      const teamData = {
        userEmail: email, // Use 'userEmail' for consistency
        teamname,
        leagueId,
        active_flg: 1,
        artists: {},
        createdAt: currentDate,
      };

      // Populate the teamData with artist information
      for (const [slotKey, artist] of Object.entries(artists)) {
        const artistCode = extractArtistCode(artist.link);
        // Use getArtistMonthlyListeners instead of getMonthlyListeners
        const response = await axios.get(
          `https://us-central1-fantasy-musicpleague-test.cloudfunctions.net/getArtistMonthlyListeners?artistCode=${artistCode}`,
        );
        const { artistName, monthlyStreams } = response.data;

        teamData.artists[artistCode] = {
          name: artistName,
          imageUrl: artist.imageUrl,
          link: artist.link,
          [currentDate]: monthlyStreams,
          active_flg: 1,
          slot: slotKey, // Store the slot key (e.g., "slot_1")
        };
      }

      let newTeamId; // Declare newTeamId outside the transaction

      try {
        // **Begin Firestore Transaction**
        await db.runTransaction(async (transaction) => {
          // Save the team data
          const teamRef = db.collection("teams").doc(); // Auto-generated ID
          transaction.set(teamRef, teamData);
          newTeamId = teamRef.id; // Assign the ID to newTeamId
          console.log(`New team created with ID: ${newTeamId}`);

          // Update the league document
          const leagueRef = db.collection("leagues").doc(leagueId);
          transaction.update(leagueRef, {
            pending_members: admin.firestore.FieldValue.arrayRemove(email),
            members: admin.firestore.FieldValue.arrayUnion(email),
          });
        });

        res.json({
          status: "success",
          message: "Roster submitted and league joined successfully!",
          teamId: newTeamId, // Return the teamId
        });
      } catch (error) {
        console.error("Error during transaction:", error);
        res.json({
          status: "error",
          issues: [{ message: "Error saving team data." }],
        });
      }
    }
  });
});

exports.validateAddDrop = functions.https.onRequest((req, res) => {
  cors(req, res, async () => {
    const { dropArtistLink, addArtistLink, addArtistImageUrl, selectedTeamID } =
      req.body;
    console.log("Received add/drop validation request:", req.body);

    if (!selectedTeamID) {
      res.json({
        status: "error",
        issues: [{ message: "Team ID is missing." }],
      });
      return;
    }

    const teamDoc = db.collection("teams").doc(selectedTeamID);
    const teamData = (await teamDoc.get()).data();

    if (!teamData) {
      res.json({
        status: "error",
        issues: [{ message: "Could not find the team data." }],
      });
      return;
    }

    const validationIssues = [];
    const currentDate = new Date().toISOString().split("T")[0];

    // Check if artist was ever on the team (including inactive artists)
    const hasArtistBeenOnTeam = Object.values(teamData.artists).some(
      (artist) => artist.link === addArtistLink,
    );

    if (hasArtistBeenOnTeam) {
      validationIssues.push({
        message:
          "This artist has previously been on your team and cannot be added again.",
      });
      res.json({ status: "error", issues: validationIssues });
      return;
    }

    // Rest of the existing validation logic
    const dropArtistEntry = Object.entries(teamData.artists).find(
      ([, artist]) => artist.link === dropArtistLink && artist.active_flg === 1,
    );

    if (!dropArtistEntry) {
      res.json({
        status: "error",
        issues: [
          {
            message: "Could not find the artist to be dropped.",
          },
        ],
      });
      return;
    }

    const [dropArtistCode, dropArtist] = dropArtistEntry;
    const dropArtistSlot = dropArtist.slot;

    if (!dropArtistSlot) {
      res.json({
        status: "error",
        issues: [
          {
            message:
              "Could not determine the slot for the artist to be dropped.",
          },
        ],
      });
      return;
    }

    // Fetch league data to get listener thresholds
    const leagueDoc = await db
      .collection("leagues")
      .doc(teamData.leagueId)
      .get();
    const leagueData = leagueDoc.data();

    if (!leagueData || !leagueData.listenerThresholds) {
      res.json({
        status: "error",
        issues: [
          {
            message: "Listener thresholds are not defined for the league.",
          },
        ],
      });
      return;
    }

    const listenerThresholds = leagueData.listenerThresholds;

    // Validate the add artist
    const artistCode = extractArtistCode(addArtistLink);
    // Use getArtistMonthlyListeners instead of getMonthlyListeners
    const response = await axios.get(
      `https://us-central1-fantasy-musicpleague-test.cloudfunctions.net/getArtistMonthlyListeners?artistCode=${artistCode}`,
    );
    const { artistName, monthlyStreams } = response.data;

    console.log(
      `Add Artist: ${artistName}, Monthly Streams: ${monthlyStreams}`,
    );

    // Determine the appropriate slot threshold
    const slotNumber = parseInt(dropArtistSlot.split("_")[1], 10);
    const maxListeners = listenerThresholds[`slot_${slotNumber}`];

    if (monthlyStreams > maxListeners) {
      validationIssues.push({
        message:
          `The artist ${artistName} has ${monthlyStreams} ` +
          `monthly listeners, which exceeds the limit for` +
          ` the ${dropArtistSlot} category.`,
      });
    }

    if (monthlyStreams < 500) {
      validationIssues.push({
        message:
          `The artist ${artistName} has less than 500 monthly listeners,` +
          ` which does not meet the minimum requirement.`,
      });
    }

    if (validationIssues.length > 0) {
      res.json({ status: "error", issues: validationIssues });
    } else {
      // Update the Firestore document
      teamData.artists[dropArtistCode].active_flg = 0;
      teamData.artists[addArtistLink] = {
        name: artistName,
        imageUrl: addArtistImageUrl,
        link: addArtistLink,
        slot: dropArtistSlot, // Assign the same slot as the dropped artist
        [currentDate]: monthlyStreams,
        active_flg: 1,
      };

      await teamDoc.set(teamData);
      res.json({ status: "success", message: "Roster updated successfully!" });
    }
  });
});

exports.saveTeam = functions.https.onRequest((req, res) => {
  cors(req, res, async () => {
    const { email, teamname, artists } = req.body;
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
        // Use getArtistMonthlyListeners instead of getMonthlyListeners
        const response = await axios.get(
          `https://us-central1-fantasy-musicpleague-test.cloudfunctions.net/getArtistMonthlyListeners?artistCode=${artistCode}`,
        );
        const { artistName, monthlyStreams } = response.data;

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

    res.json({ status: "success", message: "Team name saved successfully!" });
  });
});

exports.checkTeam = functions.https.onRequest((req, res) => {
  cors(req, res, async () => {
    const email = req.query.email;
    const teamDoc = db.collection("teams").doc(email);
    const doc = await teamDoc.get();

    if (doc.exists) {
      res.json({ hasTeam: true, team: doc.data() });
    } else {
      res.json({ hasTeam: false });
    }
  });
});

exports.getTeamByName = functions.https.onRequest((req, res) => {
  cors(req, res, async () => {
    const teamname = req.query.teamname;
    const teamsRef = db.collection("teams");
    const snapshot = await teamsRef.where("teamname", "==", teamname).get();

    if (snapshot.empty) {
      res.json({ hasTeam: false });
      return;
    }

    let teamData = null;
    snapshot.forEach((doc) => {
      teamData = doc.data();
    });

    if (teamData) {
      res.json({ hasTeam: true, team: teamData });
    } else {
      res.json({ hasTeam: false });
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
      res.json({ exists: true });
    } else {
      res.json({ exists: false });
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

/**
 * Creates a new league with the provided details.
 *
 * @param {Object} req - The request object containing league details.
 * @param {Object} res - The response object to send the result.
 */
exports.createLeague = functions.https.onRequest((req, res) => {
  cors(req, res, async () => {
    const { leagueName, creatorEmail, isPublic, password, listenerThresholds } =
      req.body;

    try {
      // Input validation
      if (!leagueName || !creatorEmail || !listenerThresholds) {
        res.status(400).json({
          status: "error",
          message: "Missing required fields.",
        });
        return;
      }

      if (!isPublic && !password) {
        res.status(400).json({
          status: "error",
          message: "Password is required for private leagues.",
        });
        return;
      }

      // Check if league name already exists
      const existingLeagueSnapshot = await db
        .collection("leagues")
        .where("leagueName", "==", leagueName)
        .get();

      if (!existingLeagueSnapshot.empty) {
        res.status(400).json({
          status: "error",
          message: "League name already exists. Please choose another name.",
        });
        return;
      }

      // Generate a unique league ID
      const leagueRef = db.collection("leagues").doc();
      const leagueId = leagueRef.id;

      // Hash the password if the league is private
      let hashedPassword = null;
      if (!isPublic && password) {
        const saltRounds = 10;
        hashedPassword = await bcrypt.hash(password, saltRounds);
      }

      // Prepare the league data
      const leagueData = {
        leagueId,
        leagueName,
        creatorEmail,
        isPublic,
        password: hashedPassword,
        listenerThresholds: {
          slot_1: listenerThresholds.slot_1 || 0,
          slot_2: listenerThresholds.slot_2 || 0,
          slot_3: listenerThresholds.slot_3 || 0,
          slot_4: listenerThresholds.slot_4 || 0,
          slot_5: listenerThresholds.slot_5 || 0,
        },
        members: [creatorEmail],
        pending_members: [creatorEmail],
        startDate: req.body.startDate,
        endDate: req.body.endDate,
      };

      // Save the league to Firestore
      await leagueRef.set(leagueData);

      res.json({
        status: "success",
        leagueId,
      });
    } catch (error) {
      console.error("Error creating league:", error);
      res.status(500).json({
        status: "error",
        message: "Internal server error.",
      });
    }
  });
});

exports.getPublicLeagues = functions.https.onRequest((req, res) => {
  cors(req, res, async () => {
    const { email } = req.query;

    if (!email) {
      res.status(400).json({ message: "Email is required." });
      return;
    }

    try {
      const leaguesRef = db.collection("leagues");
      const snapshot = await leaguesRef.where("isPublic", "==", true).get();

      const leagues = [];
      for (const doc of snapshot.docs) {
        const leagueData = doc.data();
        if (!leagueData.members.includes(email)) {
          leagues.push({
            leagueId: doc.id,
            leagueName: leagueData.leagueName,
          });
        }
      }

      res.json(leagues);
    } catch (error) {
      console.error("Error fetching public leagues:", error);
      res.status(500).json({ message: "Internal server error." });
    }
  });
});

exports.joinLeague = functions.https.onRequest((req, res) => {
  cors(req, res, async () => {
    const { email, leagueId, password } = req.body;

    try {
      // Input validation
      if (!email || !leagueId) {
        res
          .status(400)
          .json({ status: "error", message: "Missing required fields." });
        return;
      }

      const leagueRef = db.collection("leagues").doc(leagueId);
      const leagueDoc = await leagueRef.get();

      if (!leagueDoc.exists) {
        res.status(400).json({ status: "error", message: "League not found." });
        return;
      }

      const leagueData = leagueDoc.data();

      // Check if user is already a member
      if (leagueData.members && leagueData.members.includes(email)) {
        res.status(400).json({
          status: "error",
          message: "You are already a member of this league.",
        });
        return;
      }

      // Check if user is already a pending member
      if (
        leagueData.pending_members &&
        leagueData.pending_members.includes(email)
      ) {
        res.status(400).json({
          status: "error",
          message:
            "You have already initiated joining this league. " +
            "Please submit your roster to complete the process.",
        });
        return;
      }

      // For private leagues, verify the password
      if (!leagueData.isPublic) {
        if (!password) {
          res.status(400).json({
            status: "error",
            message: "Password is required for private leagues.",
          });
          return;
        }

        const bcrypt = require("bcrypt");
        const passwordMatch = await bcrypt.compare(
          password,
          leagueData.password,
        );

        if (!passwordMatch) {
          res
            .status(400)
            .json({ status: "error", message: "Incorrect password." });
          return;
        }
      }

      // Add user to pending_members
      await leagueRef.update({
        pending_members: admin.firestore.FieldValue.arrayUnion(email),
      });

      res.json({
        status: "success",
        message: "Please submit your roster to complete joining the league.",
      });
    } catch (error) {
      console.error("Error joining league:", error);
      res
        .status(500)
        .json({ status: "error", message: "Internal server error." });
    }
  });
});

exports.getLeagues = functions.https.onRequest((req, res) => {
  cors(req, res, async () => {
    const { email } = req.query;

    if (!email) {
      res.status(400).json({ message: "Email is required." });
      return;
    }

    try {
      const leaguesRef = db.collection("leagues");
      const snapshot = await leaguesRef.get();

      const leagues = [];
      for (const doc of snapshot.docs) {
        const leagueData = doc.data();
        if (!leagueData.members.includes(email)) {
          leagues.push({
            leagueId: doc.id,
            leagueName: leagueData.leagueName,
            isPublic: leagueData.isPublic,
          });
        }
      }

      res.json(leagues);
    } catch (error) {
      console.error("Error fetching leagues:", error);
      res.status(500).json({ message: "Internal server error." });
    }
  });
});

exports.getSpotifyRecommendations = functions.https.onRequest(
  async (req, res) => {
    cors(req, res, async () => {
      try {
        console.log(
          "Starting getSpotifyRecommendations function with params:",
          {
            hasAccessToken: !!req.body.accessToken,
            leagueId: req.body.leagueId,
          },
        );

        const { accessToken, leagueId } = req.body;

        // Validate inputs
        if (!accessToken || !leagueId) {
          console.error("Missing required parameters:", {
            accessToken: !!accessToken,
            leagueId: !!leagueId,
          });
          return res.status(400).json({
            status: "error",
            message: "Missing required parameters",
          });
        }

        // Get league data for thresholds
        const leagueDoc = await admin
          .firestore()
          .collection("leagues")
          .doc(leagueId)
          .get();
        if (!leagueDoc.exists) {
          console.error("League not found:", leagueId);
          return res.status(404).json({
            status: "error",
            message: "League not found",
          });
        }

        const leagueData = leagueDoc.data();
        console.log("League data retrieved:", {
          leagueId,
          hasThresholds: Boolean(leagueData.listenerThresholds),
        });

        // Get user's top artists from Spotify
        const topArtistsResponse = await fetch(
          "https://api.spotify.com/v1/me/top/artists?limit=50",
          {
            headers: { Authorization: `Bearer ${accessToken}` },
          },
        );

        if (!topArtistsResponse.ok) {
          const errorData = await topArtistsResponse.text();
          console.error("Spotify API error:", {
            status: topArtistsResponse.status,
            response: errorData,
          });
          return res.status(500).json({
            status: "error",
            message: "Failed to fetch top artists from Spotify",
          });
        }

        const topArtistsData = await topArtistsResponse.json();
        console.log("Retrieved top artists from Spotify:", {
          count: topArtistsData.items && topArtistsData.items.length,
        });

        // Get monthly listeners for each artist
        const artistsWithListeners = await Promise.all(
          (topArtistsData.items || []).map(async (artist) => {
            try {
              const listenersResponse = await fetch(
                `https://us-central1-fantasy-musicpleague-test.cloudfunctions.net/getArtistMonthlyListeners?artistCode=${artist.id}`,
              );
              const listenersData = await listenersResponse.json();

              return Object.assign({}, artist, {
                monthlyStreams: listenersData.monthlyStreams || 0,
              });
            } catch (error) {
              console.error("Error getting monthly listeners for artist:", {
                artistId: artist.id,
                error: error.message,
              });
              return Object.assign({}, artist, {
                monthlyStreams: 0,
              });
            }
          }),
        );

        // Sort artists into slots based on thresholds
        const recommendations = {
          slot_1: [],
          slot_2: [],
          slot_3: [],
          slot_4: [],
          slot_5: [],
        };

        const thresholds = leagueData.listenerThresholds;

        artistsWithListeners.forEach((artist) => {
          const listeners = artist.monthlyStreams;

          // Find appropriate slot based on listener count
          Object.entries(thresholds).forEach(([slot, threshold]) => {
            if (listeners <= threshold && recommendations[slot].length < 10) {
              recommendations[slot].push(artist);
            }
          });
        });

        console.log("Successfully processed recommendations:", {
          totalArtists: artistsWithListeners.length,
          slotsWithCounts: Object.entries(recommendations).map(
            (entry) => `${entry[0]}: ${entry[1].length}`,
          ),
        });

        return res.status(200).json({
          status: "success",
          recommendations,
          thresholds,
        });
      } catch (error) {
        console.error("Function error:", error);
        return res.status(500).json({
          status: "error",
          message: error.message || "Failed to get recommendations",
        });
      }
    });
  },
);

exports.exchangeSpotifyCode = functions.https.onRequest(async (req, res) => {
  cors(req, res, async () => {
    try {
      const { code, redirectUri } = req.body;

      // Get credentials from Firebase config
      const clientId = functions.config().spotify.client_id;
      const clientSecret = functions.config().spotify.client_secret;

      // Create base64 encoded auth string
      const auth = Buffer.from(`${clientId}:${clientSecret}`).toString(
        "base64",
      );

      const params = new URLSearchParams({
        grant_type: "authorization_code",
        code: code,
        redirect_uri: redirectUri,
      });

      const response = await axios.post(
        "https://accounts.spotify.com/api/token",
        params.toString(),
        {
          headers: {
            Authorization: `Basic ${auth}`,
            "Content-Type": "application/x-www-form-urlencoded",
          },
        },
      );

      res.json(response.data);
    } catch (error) {
      console.error(
        "Token exchange error:",
        error.response && error.response.data ? error.response.data : error,
      );
      res.status(500).json({
        error:
          error.response && error.response.data
            ? error.response.data
            : error.message,
      });
    }
  });
});

exports.storeSpotifyTopArtists = functions.https.onRequest((req, res) => {
  // Move all logic inside the CORS callback
  return cors(req, res, async () => {
    try {
      const { teamId, artists } = req.body;

      if (!teamId || !artists) {
        res.status(400).json({ error: "Missing required parameters" });
        return;
      }

      // Store in Firestore
      await db.collection("teams").doc(teamId).update({
        spotifyTopArtists: artists,
        spotifyLastUpdated: admin.firestore.FieldValue.serverTimestamp(),
      });

      res.json({ status: "success" });
    } catch (error) {
      console.error("Error storing top artists:", error);
      // Always return JSON, even for errors
      res.status(500).json({
        error: error.message,
        status: "error",
      });
    }
  });
});

exports.updateSpotifyFavorites = functions.pubsub
  .schedule("every 24 hours")
  .onRun(async (context) => {
    try {
      // Get all users with Spotify tokens
      const usersSnapshot = await db
        .collection("users")
        .where("spotifyAccessToken", "!=", null)
        .get();

      for (const userDoc of usersSnapshot.docs) {
        const userData = userDoc.data();
        const spotifyToken = userData.spotifyAccessToken;

        // Get user's top artists
        const [mediumTerm] = await Promise.all([
          axios.get("https://api.spotify.com/v1/me/top/artists", {
            params: { time_range: "medium_term", limit: 50 },
            headers: { Authorization: `Bearer ${spotifyToken}` },
          }),
        ]);

        // Store top artists with their rank
        const topArtists = mediumTerm.data.items.map((artist, index) => ({
          id: artist.id,
          name: artist.name,
          rank: index + 1,
        }));

        // Update user document with favorites
        await userDoc.ref.update({
          spotifyTopArtists: topArtists,
          spotifyLastUpdated: admin.firestore.FieldValue.serverTimestamp(),
        });
      }
    } catch (error) {
      console.error("Error updating Spotify favorites:", error);
    }
  });

exports.refreshSpotifyToken = functions.https.onCall(async (data, context) => {
  const { teamId } = data;

  try {
    // Get the team document
    const teamDoc = await admin
      .firestore()
      .collection("teams")
      .doc(teamId)
      .get();

    if (!teamDoc.exists) {
      return { status: "error", message: "Team not found" };
    }

    const teamData = teamDoc.data();

    // Check if we have stored tokens
    if (!teamData.spotifyTokens || !teamData.spotifyTokens.refreshToken) {
      return { status: "error", message: "No refresh token available" };
    }

    // Get Spotify credentials
    const clientId = functions.config().spotify.client_id;
    const clientSecret = functions.config().spotify.client_secret;

    // Create auth string
    const auth = Buffer.from(`${clientId}:${clientSecret}`).toString("base64");

    // Request new access token using refresh token
    const response = await axios.post(
      "https://accounts.spotify.com/api/token",
      new URLSearchParams({
        grant_type: "refresh_token",
        refresh_token: teamData.spotifyTokens.refreshToken,
      }).toString(),
      {
        headers: {
          Authorization: `Basic ${auth}`,
          "Content-Type": "application/x-www-form-urlencoded",
        },
      },
    );

    // Update the stored tokens
    await admin
      .firestore()
      .collection("teams")
      .doc(teamId)
      .update({
        "spotifyTokens.accessToken": response.data.access_token,
        "spotifyTokens.expiresAt": new Date(
          Date.now() + response.data.expires_in * 1000,
        ).toISOString(),
        "spotifyTokens.lastUpdated":
          admin.firestore.FieldValue.serverTimestamp(),
      });

    // Return the new access token
    return {
      status: "success",
      accessToken: response.data.access_token,
      expiresIn: response.data.expires_in,
    };
  } catch (error) {
    console.error("Error refreshing token:", error);
    return { status: "error", message: error.message };
  }
});
