const { createCanvas } = require("canvas");
const { LatLon, cornerCalTransform, getResolution } = require("./helpers");

const extractSpeed = (trackPoints) => {
	const speeds = [];
	let prevSpeed = 1;
	for (let i = 0; i < trackPoints.length; i++) {
		const minIdx = Math.max(i - 10, 0);
		const maxIdx = Math.min(minIdx + 10, trackPoints.length - 1);
		const trackPointsPortion = trackPoints.slice(minIdx, maxIdx);
		let distance = 0;
		for (let j = 0; j < trackPointsPortion.length - 1; j++) {
			const from = new LatLon(
				trackPointsPortion[j].coordinates[0],
				trackPointsPortion[j].coordinates[1],
			);
			const to = new LatLon(
				trackPointsPortion[j + 1].coordinates[0],
				trackPointsPortion[j + 1].coordinates[1],
			);
			distance += from.distance(to);
		}
		let speed =
			(distance / (trackPoints[maxIdx].time - trackPoints[minIdx].time)) * 3600;
		if (Number.isNaN(speed)) {
			speed = prevSpeed;
		}
		speeds.push(speed);
		prevSpeed = speed;
	}
	return speeds;
};

const extractDistance = (trackPoints) => {
	let distance = 0;
	for (let i = 0; i < trackPoints.length - 1; i++) {
		const from = new LatLon(
			trackPoints[i].coordinates[0],
			trackPoints[i].coordinates[1],
		);
		const to = new LatLon(
			trackPoints[i + 1].coordinates[0],
			trackPoints[i + 1].coordinates[1],
		);
		distance += from.distance(to);
	}
	return distance;
};

const extractBounds = (image, cornersCoordinates, trackPoints) => {
	const transform = cornerCalTransform(
		image.width,
		image.height,
		cornersCoordinates.top_left,
		cornersCoordinates.top_right,
		cornersCoordinates.bottom_right,
		cornersCoordinates.bottom_left,
	);

	let minX = 0;
	let maxX = image.width;
	let minY = 0;
	let maxY = image.height;
	for (let i = 0; i < trackPoints.length; i++) {
		const pt = transform(
			new LatLon(trackPoints[i].coordinates[0], trackPoints[i].coordinates[1]),
		);
		minX = pt.x < minX ? pt.x : minX;
		maxX = pt.x > maxX ? pt.x : maxX;
		minY = pt.y < minY ? pt.y : minY;
		maxY = pt.y > maxY ? pt.y : maxY;
	}
	return {
		minX: Math.floor(minX),
		maxX: Math.ceil(maxX),
		minY: Math.floor(minY),
		maxY: Math.ceil(maxY),
	};
};

const scaleImage = (image, ratio) => {
	const canvas = createCanvas(
		Math.floor(image.width * ratio),
		Math.floor(image.height * ratio),
	);
	const ctx = canvas.getContext("2d");
	ctx.drawImage(
		image,
		0,
		0,
		Math.floor(image.width * ratio),
		Math.floor(image.height * ratio),
	);
	return canvas;
};

const drawTrackOnMap = async (
	image,
	cornersCoordinates,
	trackPoints,
	timezoneName = "Europe/Helsinki",
	includeHeader = false,
	includeTrack = true,
) => {
	const bounds = extractBounds(image, cornersCoordinates, trackPoints);

	const mWidth = bounds.maxX - bounds.minX;
	const mHeight = bounds.maxY - bounds.minY;
	const MAX = 32767;

	if (mHeight > MAX || mWidth > MAX) {
		const scaledImg = scaleImage(image, MAX / Math.max(mHeight, mWidth));
		return drawTrackOnMap(
			scaledImg,
			cornersCoordinates,
			trackPoints,
			timezoneName,
			includeHeader,
			includeTrack,
		);
	}

	const resolution =
		getResolution(
			image.width,
			image.height,
			cornersCoordinates.top_left,
			cornersCoordinates.top_right,
			cornersCoordinates.bottom_right,
			cornersCoordinates.bottom_left,
		) / 1.702;

	const canvas = createCanvas(
		bounds.maxX - bounds.minX,
		bounds.maxY - bounds.minY,
	);

	const ctx = canvas.getContext("2d");

	// draw a background
	ctx.fillStyle = "white";
	ctx.fillRect(0, 0, canvas.width, canvas.height);

	ctx.drawImage(
		image,
		Math.round(-bounds.minX),
		Math.round(-bounds.minY),
		Math.round(image.width),
		Math.round(image.height),
	);

	const outlineWidth = Math.max(2, 2 / resolution);
	const weight = Math.max(4, 4 / resolution);

	const speeds = extractSpeed(trackPoints);

	let minSpeed = null;
	let maxSpeed = null;
	if (speeds.length) {
		const sumSpeeds = speeds.reduce((a, b) => a + b, 0);
		const avgSpeed = sumSpeeds / speeds.length;
		const sumVariance = speeds.reduce((a, b) => a + (b - avgSpeed) ** 2, 0);
		const standardDev = Math.sqrt(sumVariance / speeds.length);
		minSpeed = avgSpeed - standardDev;
		maxSpeed = avgSpeed + standardDev;
	}

	const palette = ((initPalette) => {
		const pCanvas = createCanvas(1, 256);
		const pCtx = pCanvas.getContext("2d");
		const gradient = pCtx.createLinearGradient(0, 0, 0, 256);

		for (const i in initPalette) {
			gradient.addColorStop(Number.parseFloat(i), initPalette[i]);
		}

		pCtx.fillStyle = gradient;
		pCtx.fillRect(0, 0, 1, 256);
		return pCtx.getImageData(0, 0, 1, 256).data;
	})({
		0.0: "#ff0000",
		0.5: "#ffff00",
		1.0: "#008800",
	});

	const getRGBForPercent = (valueRelative) => {
		const paletteIndex = Math.min(
			Math.floor(valueRelative * 256) * 4,
			palette.length - 4,
		);

		return [
			palette[paletteIndex],
			palette[paletteIndex + 1],
			palette[paletteIndex + 2],
		];
	};

	const getRGBForValue = (value) => {
		let valueRelative = Math.min(
			Math.max((value - minSpeed) / (maxSpeed - minSpeed), 0),
			0.999,
		);
		if (Number.isNaN(valueRelative)) {
			valueRelative = 0;
		}
		return getRGBForPercent(valueRelative);
	};

	if (includeTrack) {
		const canvas2 = createCanvas(canvas.width, canvas.height);
		const ctx2 = canvas2.getContext("2d");
		const canvas3 = createCanvas(canvas.width, canvas.height);
		const ctx3 = canvas3.getContext("2d");

		const transform = cornerCalTransform(
			image.width,
			image.height,
			cornersCoordinates.top_left,
			cornersCoordinates.top_right,
			cornersCoordinates.bottom_right,
			cornersCoordinates.bottom_left,
		);

		// drawOutline
		ctx3.lineWidth = weight + 2 * outlineWidth;
		ctx3.strokeStyle = "black";
		ctx3.lineCap = "round";
		ctx3.lineJoin = "round";
		ctx3.beginPath();
		let prevPt = null;
		for (let i = 0; i < trackPoints.length; i++) {
			const pt = transform(
				new LatLon(
					trackPoints[i].coordinates[0],
					trackPoints[i].coordinates[1],
				),
			);
			if (
				!prevPt ||
				Math.sqrt(
					Math.round(prevPt.x) -
						Math.round(pt.x) ** 2 +
						(Math.round(prevPt.y) - Math.round(pt.y) ** 2),
				) > weight
			) {
				prevPt = pt;
				ctx3.lineTo(
					Math.round(pt.x - bounds.minX),
					Math.round(pt.y - bounds.minY),
				);
			}
		}
		ctx3.stroke();

		ctx3.globalCompositeOperation = "destination-out";
		ctx3.lineWidth = weight;
		ctx3.stroke();
		ctx3.globalCompositeOperation = "source-over";

		// drawColoredPath
		ctx2.lineWidth = weight;
		ctx2.lineCap = "round";
		ctx2.lineJoin = "round";
		let pointStart = transform(
			new LatLon(trackPoints[0].coordinates[0], trackPoints[0].coordinates[1]),
		);
		for (let j = 1; j < trackPoints.length; j++) {
			const pointEnd = transform(
				new LatLon(
					trackPoints[j].coordinates[0],
					trackPoints[j].coordinates[1],
				),
			);
			const distanceX = pointEnd.x - pointStart.x;
			const distanceY = pointEnd.x - pointStart.x;
			const d = Math.sqrt(distanceX ** 2 + distanceY ** 2);

			if (d < 1) {
				continue;
			}

			// Create a gradient for each segment, pick start end end colors from palette gradient
			const gradient = ctx2.createLinearGradient(
				Math.round(pointStart.x - bounds.minX),
				Math.round(pointStart.y - bounds.minY),
				Math.round(pointEnd.x - bounds.minX),
				Math.round(pointEnd.y - bounds.minY),
			);
			const gradientStartRGB = getRGBForValue(speeds[j - 1]);
			const gradientEndRGB = getRGBForValue(speeds[j]);
			gradient.addColorStop(0, `rgb(${gradientStartRGB.join(",")})`);
			gradient.addColorStop(1, `rgb(${gradientEndRGB.join(",")})`);
			ctx2.strokeStyle = gradient;

			ctx2.beginPath();
			ctx2.moveTo(
				Math.round(pointStart.x - bounds.minX),
				Math.round(pointStart.y - bounds.minY),
			);
			ctx2.lineTo(
				Math.round(pointEnd.x - bounds.minX),
				Math.round(pointEnd.y - bounds.minY),
			);
			ctx2.stroke();

			pointStart = pointEnd;
		}

		if (trackPoints.length && trackPoints[0].time) {
			let prevT = +trackPoints[0].time - 20e3;
			let count = 0;
			ctx3.strokeStyle = "#222";
			const size = Math.min(3, 3 / resolution);
			for (let j = 1; j < trackPoints.length - 1; j++) {
				if (+trackPoints[j].time >= +prevT + 10e3) {
					ctx3.lineWidth = (count % 6 === 0 ? 3 : 1) / resolution;
					const pointStart = transform(
						new LatLon(
							trackPoints[j - 1].coordinates[0],
							trackPoints[j - 1].coordinates[1],
						),
					);
					const point = transform(
						new LatLon(
							trackPoints[j].coordinates[0],
							trackPoints[j].coordinates[1],
						),
					);
					const pointNext = transform(
						new LatLon(
							trackPoints[j + 1].coordinates[0],
							trackPoints[j + 1].coordinates[1],
						),
					);
					const angle =
						Math.atan2(pointNext.y - pointStart.y, pointNext.x - pointStart.x) +
						Math.PI / 2;
					ctx3.beginPath();
					ctx3.moveTo(
						Math.round(point.x - bounds.minX - Math.cos(angle) * size),
						Math.round(point.y - bounds.minY - Math.sin(angle) * size),
					);
					ctx3.lineTo(
						Math.round(point.x - bounds.minX + Math.cos(angle) * size),
						Math.round(point.y - bounds.minY + Math.sin(angle) * size),
					);
					ctx3.stroke();
					prevT = trackPoints[j].time;
					count++;
				}
			}
		}
		ctx.globalAlpha = 0.45;
		ctx.drawImage(canvas2, 0, 0);
		ctx.globalAlpha = 0.7;
		ctx.drawImage(canvas3, 0, 0);
	}
	if (includeHeader) {
		const headerHeight = 70;
		const paletteWidth = 180;
		const paletteX = 40;
		const paletteY = 30;
		const lineWidth = 16;
		const canvas4 = createCanvas(canvas.width, canvas.height + headerHeight);
		const ctx4 = canvas4.getContext("2d");

		ctx4.drawImage(canvas, 0, headerHeight);
		// draw a background
		ctx4.fillStyle = "#222";
		ctx4.fillRect(0, 0, canvas4.width, headerHeight);

		ctx4.font = "15px Arial";
		ctx4.fillStyle = "white";
		if (includeTrack && trackPoints.length && trackPoints[0].time) {
			const gradient = ctx4.createLinearGradient(
				paletteX,
				0,
				paletteWidth + paletteX,
				0,
			);
			gradient.addColorStop(0, `rgb(${getRGBForPercent(0).join(",")})`);
			gradient.addColorStop(0.5, `rgb(${getRGBForPercent(0.5).join(",")})`);
			gradient.addColorStop(1, `rgb(${getRGBForPercent(1).join(",")})`);

			ctx4.lineWidth = 16;
			ctx4.strokeStyle = gradient;
			ctx4.beginPath();
			ctx4.moveTo(paletteX, paletteY);
			ctx4.lineTo(paletteX + paletteWidth, paletteY);
			ctx4.stroke();

			ctx4.lineWidth = 1;
			ctx4.strokeStyle = "#222";
			ctx4.beginPath();
			ctx4.moveTo(paletteX + paletteWidth / 2, paletteY - lineWidth / 2);
			ctx4.lineTo(paletteX + paletteWidth / 2, paletteY + lineWidth / 2);
			ctx4.stroke();

			const minSpeedTxt = getSpeedText(minSpeed);
			const medSpeedTxt = getSpeedText((maxSpeed + minSpeed) / 2);
			const maxSpeedTxt = getSpeedText(maxSpeed);

			ctx4.textAlign = "center";
			ctx4.fillText(minSpeedTxt, paletteX, paletteY + lineWidth / 2 + 15);
			ctx4.fillText(
				medSpeedTxt,
				paletteX + paletteWidth / 2,
				paletteY + lineWidth / 2 + 15,
			);
			ctx4.fillText(
				maxSpeedTxt,
				paletteX + paletteWidth,
				paletteY + lineWidth / 2 + 15,
			);
		}
		ctx4.textAlign = "left";
		if (includeTrack && trackPoints.length) {
			const dist = extractDistance(trackPoints);
			ctx4.fillText(
				`${(dist / 1e3).toFixed(1)}km   |`,
				paletteX + paletteWidth + 45,
				paletteY,
			);
			ctx4.fillText(
				printTime(
					trackPoints[trackPoints.length - 1].time - trackPoints[0].time,
				),
				paletteX + paletteWidth + 115,
				paletteY,
			);
			ctx4.fillText(
				`${new Date(trackPoints[0].time).toLocaleString(undefined, {
					timeZone: timezoneName,
					weekday: "long",
					year: "numeric",
					month: "long",
					day: "numeric",
					timeZoneName: "short",
					hour12: false,
					hour: "numeric",
					minute: "numeric",
					second: "numeric",
				})}`,
				paletteX + paletteWidth + 45,
				paletteY + 20,
			);
		}

		ctx4.font = "60px Arial";
		ctx4.fillText("mapdump.com", canvas.width - 400, headerHeight - 17);
		return canvas4;
	}
	return canvas;
};

const getSpeedText = (s) => {
	return `${s.toFixed(1)}km/h`;
};

const printTime = (t) => {
	const date = new Date(null);
	date.setSeconds(t / 1e3);
	const iso = date.toISOString().substr(11, 8);
	const h = Number.parseInt(iso.slice(0, 2));
	const hasH = h > 0;
	const hPart = hasH ? `${h}h` : "";
	const m = Number.parseInt(iso.slice(3, 5));
	const hasM = hasH || m > 0;
	let mPart = hasM ? `${m}m` : "";
	if (hasH) {
		mPart = mPart.padStart(3, "0");
	}
	const s = Number.parseInt(iso.slice(6, 8));
	const hasS = hasM || s > 0;
	let sPart = hasS ? `${s}s` : "";
	if (hasM) {
		sPart = sPart.padStart(3, "0");
	}
	return hPart + mPart + sPart;
};

module.exports = {
	drawTrackOnMap,
};
