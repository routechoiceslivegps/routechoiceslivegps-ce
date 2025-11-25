const Point = (() => {
	function P(x, y) {
		this.x = x;
		this.y = y;
	}
	return P;
})();

const LatLon = (() => {
	class L {
		constructor(lat, lon) {
			this.lat = lat;
			this.lon = lon;
		}
	}
	L.prototype.distance = function (coordinates) {
		const C = Math.PI / 180;
		const dlat = this.lat - coordinates.lat;
		const dlon = this.lon - coordinates.lon;
		const a =
			Math.sin((C * dlat) / 2) ** 2 +
			Math.cos(C * this.lat) *
				Math.cos(C * coordinates.lat) *
				Math.sin((C * dlon) / 2) ** 2;
		return 12756274 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
	};
	return L;
})();

const SpheroidProjection = (() => {
	const pi = Math.PI;
	const number180 = 180.0;
	const rad = 6378137;
	const originShift = pi * rad;
	const pi_180 = pi / number180;
	class S {
		LatLonToMeters(coordinates) {
			return new Point(
				coordinates.lon * rad * pi_180,
				Math.log(Math.tan(((90 + coordinates.lat) * pi_180) / 2)) * rad,
			);
		}
		MetersToLatLon(mxy) {
			return new LatLon(
				(2 * Math.atan(Math.exp(mxy.y / rad)) - pi / 2) / pi_180,
				mxy.x / rad / pi_180,
			);
		}
		resolution(zoom) {
			return (2 * originShift) / (256 * 2 ** zoom);
		}
		zoomForPixelSize(pixelSize) {
			for (let i = 0; i < 30; i++) {
				if (pixelSize > this.resolution(i)) {
					return Math.max(i - 1, 0);
				}
			}
		}
		pixelsToMeters(px, py, zoom) {
			const res = this.resolution(zoom);
			const mx = px * res - originShift;
			const my = py * res - originShift;
			return new Point(mx, my);
		}
	}
	return S;
})();

function adj(m) {
	// Compute the adjugate of m
	return [
		m[4] * m[8] - m[5] * m[7],
		m[2] * m[7] - m[1] * m[8],
		m[1] * m[5] - m[2] * m[4],
		m[5] * m[6] - m[3] * m[8],
		m[0] * m[8] - m[2] * m[6],
		m[2] * m[3] - m[0] * m[5],
		m[3] * m[7] - m[4] * m[6],
		m[1] * m[6] - m[0] * m[7],
		m[0] * m[4] - m[1] * m[3],
	];
}
function multmm(a, b) {
	// multiply two matrices
	const c = Array(9);
	for (let i = 0; i !== 3; ++i) {
		for (let j = 0; j !== 3; ++j) {
			let cij = 0;
			for (let k = 0; k !== 3; ++k) {
				cij += a[3 * i + k] * b[3 * k + j];
			}
			c[3 * i + j] = cij;
		}
	}
	return c;
}

function multmv(m, v) {
	// multiply matrix and vector
	return [
		m[0] * v[0] + m[1] * v[1] + m[2] * v[2],
		m[3] * v[0] + m[4] * v[1] + m[5] * v[2],
		m[6] * v[0] + m[7] * v[1] + m[8] * v[2],
	];
}
function basisToPoints(x1, y1, x2, y2, x3, y3, x4, y4) {
	const m = [x1, x2, x3, y1, y2, y3, 1, 1, 1];
	const v = multmv(adj(m), [x4, y4, 1]);
	return multmm(m, [v[0], 0, 0, 0, v[1], 0, 0, 0, v[2]]);
}

function general2DProjection(
	x1s,
	y1s,
	x1d,
	y1d,
	x2s,
	y2s,
	x2d,
	y2d,
	x3s,
	y3s,
	x3d,
	y3d,
	x4s,
	y4s,
	x4d,
	y4d,
) {
	const s = basisToPoints(x1s, y1s, x2s, y2s, x3s, y3s, x4s, y4s);
	const d = basisToPoints(x1d, y1d, x2d, y2d, x3d, y3d, x4d, y4d);
	return multmm(d, adj(s));
}
function project(m, x, y) {
	const v = multmv(m, [x, y, 1]);
	return [v[0] / v[2], v[1] / v[2]];
}

function cornerCalTransform(
	width,
	height,
	top_left_coordinates,
	top_right_coordinates,
	bottom_right_coordinates,
	bottom_left_coordinates,
) {
	const proj = new SpheroidProjection();
	const top_left_meters = proj.LatLonToMeters(top_left_coordinates);
	const top_right_meters = proj.LatLonToMeters(top_right_coordinates);
	const bottom_right_meters = proj.LatLonToMeters(bottom_right_coordinates);
	const bottom_left_meters = proj.LatLonToMeters(bottom_left_coordinates);
	const matrix3d = general2DProjection(
		top_left_meters.x,
		top_left_meters.y,
		0,
		0,
		top_right_meters.x,
		top_right_meters.y,
		width,
		0,
		bottom_right_meters.x,
		bottom_right_meters.y,
		width,
		height,
		bottom_left_meters.x,
		bottom_left_meters.y,
		0,
		height,
	);
	return (latLon) => {
		const meters = proj.LatLonToMeters(latLon);
		const xy = project(matrix3d, meters.x, meters.y);
		return new Point(xy[0], xy[1]);
	};
}

function getResolution(
	width,
	height,
	top_left_coordinates,
	top_right_coordinates,
	bottom_right_coordinates,
	bottom_left_coordinates,
) {
	const transform = cornerCalTransform(
		width,
		height,
		top_left_coordinates,
		top_right_coordinates,
		bottom_right_coordinates,
		bottom_left_coordinates,
	);
	const topLeftMapXY = transform(top_left_coordinates);
	const topRightMapXY = transform(top_right_coordinates);
	const bottomRightMapXY = transform(bottom_right_coordinates);
	const bottomLeftMapXY = transform(bottom_left_coordinates);
	const proj = new SpheroidProjection();
	const top_left_meters = proj.LatLonToMeters(top_left_coordinates);
	const top_right_meters = proj.LatLonToMeters(top_right_coordinates);
	const bottom_right_meters = proj.LatLonToMeters(bottom_right_coordinates);
	const bottom_left_meters = proj.LatLonToMeters(bottom_left_coordinates);

	const resA =
		Math.sqrt(
			(top_left_meters.x - bottom_right_meters.x) ** 2 +
				(top_left_meters.y - bottom_right_meters.y) ** 2,
		) /
		Math.sqrt(
			(topLeftMapXY.x - bottomRightMapXY.x) ** 2 +
				(topLeftMapXY.y - bottomRightMapXY.y) ** 2,
		);
	const resB =
		Math.sqrt(
			(top_right_meters.x - bottom_left_meters.x) ** 2 +
				(top_right_meters.y - bottom_left_meters.y) ** 2,
		) /
		Math.sqrt(
			(topRightMapXY.x - bottomLeftMapXY.x) ** 2 +
				(topRightMapXY.y - bottomLeftMapXY.y) ** 2,
		);
	return (resA + resB) / 2;
}

function cornerBackTransform(
	width,
	height,
	top_left_coordinates,
	top_right_coordinates,
	bottom_right_coordinates,
	bottom_left_coordinates,
) {
	const proj = new SpheroidProjection();
	const top_left_meters = proj.LatLonToMeters(top_left_coordinates);
	const top_right_meters = proj.LatLonToMeters(top_right_coordinates);
	const bottom_right_meters = proj.LatLonToMeters(bottom_right_coordinates);
	const bottom_left_meters = proj.LatLonToMeters(bottom_left_coordinates);
	const matrix3d = general2DProjection(
		0,
		0,
		top_left_meters.x,
		top_left_meters.y,
		width,
		0,
		top_right_meters.x,
		top_right_meters.y,
		width,
		height,
		bottom_right_meters.x,
		bottom_right_meters.y,
		0,
		height,
		bottom_left_meters.x,
		bottom_left_meters.y,
	);
	return (coords) => {
		const xy = project(matrix3d, coords.x, coords.y);
		return proj.MetersToLatLon(new Point(xy[0], xy[1]));
	};
}

const dataURItoBlob = (dataURI) => {
	// convert base64/URLEncoded data component to raw binary data held in a string
	let byteString;
	if (dataURI.split(",")[0].indexOf("base64") >= 0)
		byteString = atob(dataURI.split(",")[1]);
	else byteString = unescape(dataURI.split(",")[1]);

	// separate out the mime component
	const mimeString = dataURI.split(",")[0].split(":")[1].split(";")[0];

	// write the bytes of the string to a typed array
	const ia = new Uint8Array(byteString.length);
	for (let i = 0; i < byteString.length; i++) {
		ia[i] = byteString.charCodeAt(i);
	}
	return new Blob([ia], { type: mimeString });
};

module.exports = {
	Point,
	LatLon,
	SpheroidProjection,
	cornerCalTransform,
	cornerBackTransform,
	dataURItoBlob,
	getResolution,
};
