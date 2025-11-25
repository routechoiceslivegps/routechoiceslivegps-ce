#! /usr/bin/env node
const fs = require("node:fs");
const { loadImage } = require("canvas");
const { drawTrackOnMap } = require("./lib/mapdump");

void (async (sysArgs) => {
	const [
		imageFilename,
		cornersCoordsRaw,
		trackFilename,
		timezone,
		showHeaderRaw,
		showRouteRaw,
	] = sysArgs;
	const showHeader = showHeaderRaw === "1";
	const showRoute = showRouteRaw === "1";
	const cornersCoordsArray = cornersCoordsRaw
		.split(",")
		.map((val) => Number.parseFloat(val));
	const cornersCoords = {
		top_left: {
			lat: cornersCoordsArray[0],
			lon: cornersCoordsArray[1],
		},
		top_right: {
			lat: cornersCoordsArray[2],
			lon: cornersCoordsArray[3],
		},
		bottom_right: {
			lat: cornersCoordsArray[4],
			lon: cornersCoordsArray[5],
		},
		bottom_left: {
			lat: cornersCoordsArray[6],
			lon: cornersCoordsArray[7],
		},
	};
	const trackRaw = fs.readFileSync(trackFilename, {
		encoding: "utf8",
		flag: "r",
	});
	const trackJson = JSON.parse(trackRaw);
	const trackPoints = trackJson.map((pt) => {
		return {
			time: pt[0] * 1000,
			coordinates: [pt[1], pt[2]],
		};
	});
	console.log(trackPoints);
	const mapImage = await loadImage(imageFilename);
	const canvas = await drawTrackOnMap(
		mapImage,
		cornersCoords,
		trackPoints,
		timezone,
		showHeader,
		showRoute,
	);
	const dataURI = canvas.toDataURL("image/jpeg", { quality: 0.9 });
	process.stdout.write(dataURI);
})(process.argv.slice(2));
